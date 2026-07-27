"""LLM gateway — the single point of contact with LiteLLM.

All provider access flows through this module. It normalizes responses,
handles streaming, captures usage, and raises clear errors.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import urlparse

from relaycli.core.config import Settings, get_settings

_litellm: Any = None


def _lazy_litellm() -> Any:
    global _litellm
    if _litellm is None:
        import litellm
        litellm.drop_params = True
        litellm.telemetry = False
        litellm.suppress_debug_info = True
        _litellm = litellm
    return _litellm


def is_warm() -> bool:
    return _litellm is not None


_PROVIDER_KEY_ATTR: dict[str, str] = {
    "openai": "openai_api_key", "anthropic": "anthropic_api_key",
    "gemini": "gemini_api_key", "groq": "groq_api_key",
    "mistral": "mistral_api_key", "openrouter": "openrouter_api_key",
}
_CONFIG_KEY_PROVIDERS = {"openai", "anthropic", "gemini", "groq", "mistral", "openrouter",
                         "deepseek", "dashscope", "zhipu"}
_PROVIDER_ENV_HINT: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY", "dashscope": "DASHSCOPE_API_KEY", "zhipu": "ZHIPUAI_API_KEY",
}
_KEYLESS_PROVIDERS = {"ollama", "ollama_chat"}
_TOOL_CAPABLE_OLLAMA_HINTS = (
    "llama3.1", "llama3.2", "llama3.3", "qwen3", "qwen2.5-coder", "mistral", "granite3",
    "devstral", "gpt-oss", "command-r",
)
_TOOL_RISK_OLLAMA_HINTS = ("deepseek-coder", "codellama")
_PREFIX_PROVIDERS = {
    "openai", "anthropic", "gemini", "groq", "mistral", "openrouter",
    "deepseek", "dashscope", "zhipu", "ollama", "ollama_chat",
}
_BARE_NAME_HINTS = (
    ("gpt-", "openai"), ("chatgpt-", "openai"),
    ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
    ("claude-", "anthropic"), ("gemini-", "gemini"),
    ("mistral-", "mistral"), ("ministral-", "mistral"), ("codestral-", "mistral"),
)


def resolve_provider(model: str) -> str | None:
    head, sep, _ = model.partition("/")
    if sep and head in _PREFIX_PROVIDERS:
        return head
    for prefix, provider in _BARE_NAME_HINTS:
        if model.startswith(prefix):
            return provider
    return None


class LLMError(RuntimeError):
    pass


def _missing_key_message(provider: str, model: str, attr: str) -> str:
    return (f"No API key for '{provider}' (model '{model}'). "
            f"Set {attr.upper()} in your environment or ~/.relaycli/config.toml.")


def _missing_config_key_message(provider: str, model: str) -> str:
    env = _PROVIDER_ENV_HINT.get(provider, f"{provider.upper()}_API_KEY")
    return (f"No API key for '{provider}' (model '{model}'). "
            f"Set {env} in your environment or run: relaycli config set-key {provider}")


def _stored_provider_key(provider: str, *, include_standard_env: bool = False) -> str | None:
    try:
        from relaycli.config.manager import load_app_config, resolve_provider_key
        cfg = load_app_config()
        if include_standard_env:
            return resolve_provider_key(cfg, provider)
        stored = cfg.providers.get(provider)
        if stored is None or not stored.api_key:
            return None
        if stored.api_key.startswith("env:"):
            return os.environ.get(stored.api_key[4:])
        return stored.api_key
    except Exception:
        return None


def _settings_or_stored_key(settings: Settings, provider: str, attr: str | None) -> str | None:
    if attr:
        key = getattr(settings, attr)
        if key:
            return key
    if provider in _CONFIG_KEY_PROVIDERS:
        return _stored_provider_key(provider, include_standard_env=attr is None)
    return None


def preflight_settings(settings: Settings) -> str | None:
    llm = LLM(settings)
    models = [settings.model]
    if settings.relay_enabled:
        from relaycli.agent.router import routing_table
        models.extend(routing_table(settings).values())
    for model in dict.fromkeys(models):
        problem = llm.preflight(model)
        if problem:
            return problem
    return None


def key_status(settings: Settings, model: str | None = None) -> str | None:
    model = model or settings.model
    provider = resolve_provider(model)
    if provider is None:
        return None
    if provider in _KEYLESS_PROVIDERS:
        return "not needed"
    attr = _PROVIDER_KEY_ATTR.get(provider)
    if attr is None and provider not in _CONFIG_KEY_PROVIDERS:
        return None
    return "detected" if _settings_or_stored_key(settings, provider, attr) else "missing"


def ollama_models(settings: Settings, *, timeout: float = 0.5) -> list[str]:
    import urllib.error, urllib.request
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return []
    return [m["name"].strip() for m in data.get("models", []) if isinstance(m.get("name"), str) and m["name"].strip()]


def best_ollama_model(settings: Settings) -> str | None:
    models = ollama_models(settings)
    if not models:
        return None
    lowered = [(n, n.lower()) for n in models]
    for hint in _TOOL_CAPABLE_OLLAMA_HINTS:
        for name, low in lowered:
            if hint in low:
                return f"ollama_chat/{name}"
    return f"ollama_chat/{models[0]}"


def tool_capability_warning(model: str) -> str | None:
    provider = resolve_provider(model)
    if provider not in _KEYLESS_PROVIDERS:
        return None
    local = model.split("/", 1)[1].lower() if "/" in model else model.lower()
    if any(h in local for h in _TOOL_RISK_OLLAMA_HINTS):
        return (f"Model '{model}' may write tool calls as text instead of calling tools. "
                "Prefer llama3.1, qwen3, mistral, or run `relaycli init`.")
    if not any(h in local for h in _TOOL_CAPABLE_OLLAMA_HINTS):
        return (f"Model '{model}' is local but its tool-calling ability is unknown. "
                "If tool use stalls, switch models.")
    return None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def parsed_arguments(self) -> dict[str, Any]:
        raw = (self.arguments or "").strip()
        return json.loads(raw) if raw else {}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    model: str | None = None

    def to_assistant_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": _sanitize_arguments(tc.arguments)}}
                for tc in self.tool_calls
            ]
        return msg


def _sanitize_arguments(arguments: str) -> str:
    if not (arguments or "").strip():
        return "{}"
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return "{}"
    return arguments if isinstance(parsed, dict) else "{}"


def make_tool_result_message(tool_call: ToolCall, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.name, "content": content}


class LLM:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        model = model or self.settings.model
        call_args = self._build_call_args(messages, tools, model, temperature)
        if stream or on_token is not None:
            return self._complete_streaming(call_args, model, on_token)
        return self._complete_blocking(call_args, model)

    def stream(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> Iterator[str]:
        queue: list[str] = []
        self.complete(messages, tools=tools, model=model, temperature=temperature,
                      stream=True, on_token=queue.append)
        yield from queue

    def _build_call_args(self, messages, tools, model, temperature) -> dict[str, Any]:
        args: dict[str, Any] = {
            "model": model, "messages": list(messages),
            "temperature": self.settings.temperature if temperature is None else temperature,
            "timeout": self.settings.llm_timeout,
        }
        args.update(self._credential_kwargs(model))
        if tools:
            args["tools"] = tools
            args["tool_choice"] = "auto"
        return args

    def _credential_kwargs(self, model: str) -> dict[str, Any]:
        try:
            _, provider, _, _ = _lazy_litellm().get_llm_provider(model)
        except Exception as exc:
            raise LLMError(f"Unknown provider for model '{model}'. ({exc})") from exc
        if provider in _KEYLESS_PROVIDERS:
            return {"api_base": self.settings.ollama_base_url}
        attr = _PROVIDER_KEY_ATTR.get(provider)
        if attr is not None:
            key = _settings_or_stored_key(self.settings, provider, attr)
            if not key:
                raise LLMError(_missing_key_message(provider, model, attr))
            return {"api_key": key}
        key = _settings_or_stored_key(self.settings, provider, None)
        if key:
            return {"api_key": key}
        if provider in _CONFIG_KEY_PROVIDERS:
            raise LLMError(_missing_config_key_message(provider, model))
        return {}

    def preflight(self, model: str | None = None) -> str | None:
        model = model or self.settings.model
        provider = resolve_provider(model)
        if provider is None:
            return None
        if provider in _KEYLESS_PROVIDERS:
            return None
        attr = _PROVIDER_KEY_ATTR.get(provider)
        if attr is not None and not _settings_or_stored_key(self.settings, provider, attr):
            return _missing_key_message(provider, model, attr)
        if attr is None and provider in _CONFIG_KEY_PROVIDERS:
            if not _settings_or_stored_key(self.settings, provider, None):
                return _missing_config_key_message(provider, model)
        return None

    def _complete_blocking(self, call_args: dict[str, Any], model: str) -> LLMResponse:
        try:
            resp = _lazy_litellm().completion(**call_args)
            return self._normalize(resp, model)
        except Exception as exc:
            raise self._wrap_error(exc, model) from exc

    def _complete_streaming(self, call_args, model, on_token) -> LLMResponse:
        call_args = {**call_args, "stream": True, "stream_options": {"include_usage": True}}
        chunks: list[Any] = []
        try:
            for chunk in _lazy_litellm().completion(**call_args):
                chunks.append(chunk)
                content = _chunk_text(chunk)
                if content and on_token is not None:
                    on_token(content)
        except Exception as exc:
            raise self._wrap_error(exc, model) from exc
        return self._reassemble(chunks, model, call_args["messages"])

    def _reassemble(self, chunks, model, messages) -> LLMResponse:
        text_parts: list[str] = []
        tool_acc: dict[Any, dict[str, Any]] = {}
        order: list[Any] = []
        usage_obj = None
        finish_reason: str | None = None
        for chunk in chunks:
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage_obj = u
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
            for tc in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc, "index", None)
                if idx is None:
                    idx = len(order)
                if idx not in tool_acc:
                    tool_acc[idx] = {"id": None, "name": None, "frags": []}
                    order.append(idx)
                slot = tool_acc[idx]
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    arg = getattr(fn, "arguments", None)
                    if arg:
                        slot["frags"].append(arg)
        tool_calls: list[ToolCall] = []
        for i, idx in enumerate(order):
            slot = tool_acc[idx]
            if not slot["name"]:
                continue
            tool_calls.append(ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"],
                                       arguments=_resolve_arguments(slot["frags"])))
        text = "".join(text_parts)
        return LLMResponse(text=text, tool_calls=tool_calls,
                           usage=self._usage_from_stream(usage_obj, model, messages, text, tool_calls),
                           finish_reason=finish_reason, model=model)

    def _usage_from_stream(self, usage_obj, model, messages, text, tool_calls=None) -> Usage:
        if usage_obj is not None:
            pt = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage_obj, "completion_tokens", 0) or 0)
            tt = int(getattr(usage_obj, "total_tokens", 0) or (pt + ct))
        else:
            completion = text + "".join(f"{tc.name}{tc.arguments}" for tc in (tool_calls or []))
            pt = ct = tt = 0
            try:
                pt = int(_lazy_litellm().token_counter(model=model, messages=messages) or 0)
                ct = int(_lazy_litellm().token_counter(model=model, text=completion) or 0) if completion else 0
                tt = pt + ct
            except Exception:
                pass
        cost = 0.0
        if tt:
            try:
                pc, cc = _lazy_litellm().cost_per_token(model=model, prompt_tokens=pt, completion_tokens=ct)
                cost = float((pc or 0.0) + (cc or 0.0))
            except Exception:
                cost = 0.0
        return Usage(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt, cost_usd=cost)

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        choices = getattr(resp, "choices", None)
        if not choices:
            raise LLMError(f"Model '{model}' returned no choices.")
        choice = choices[0]
        message = getattr(choice, "message", None)
        text = getattr(message, "content", None) or "" if message is not None else ""
        tool_calls: list[ToolCall] = []
        for idx, tc in enumerate(getattr(message, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            tool_calls.append(ToolCall(id=getattr(tc, "id", None) or f"call_{idx}",
                                       name=getattr(fn, "name", None) or "",
                                       arguments=getattr(fn, "arguments", None) or "{}"))
        return LLMResponse(text=text, tool_calls=tool_calls,
                           usage=self._usage(resp, model),
                           finish_reason=getattr(choice, "finish_reason", None), model=model)

    def _usage(self, resp, model) -> Usage:
        u = getattr(resp, "usage", None)
        pt = int(getattr(u, "prompt_tokens", 0) or 0)
        ct = int(getattr(u, "completion_tokens", 0) or 0)
        tt = int(getattr(u, "total_tokens", 0) or (pt + ct))
        cost = 0.0
        try:
            cost = float(_lazy_litellm().completion_cost(completion_response=resp) or 0.0)
        except Exception:
            cost = 0.0
        return Usage(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt, cost_usd=cost)

    def _wrap_error(self, exc: Exception, model: str) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        name = type(exc).__name__
        raw = str(exc).strip()
        detail = raw.splitlines()[0] if raw else name
        detail = re.sub(r'\s+', ' ', detail)[:220]
        if "unavailable for free" in detail.lower():
            detail = f"This model is unavailable on the free tier. Use a paid key or switch to a local model."
        elif "notfound" in detail.lower():
            match = re.search(r'use this slug instead:\s*(\S+)', raw, re.IGNORECASE)
            if match:
                detail = f"Model not found — use '{match.group(1)}' instead."
        msg = f"Model call failed for '{model}' ({name}): {detail}"
        if "AuthenticationError" in name:
            provider = resolve_provider(model)
            if provider:
                msg += f"\nYour {provider} API key was rejected. Update it with: relaycli config set-key {provider}"
        return LLMError(msg)


def _chunk_text(chunk: Any) -> str:
    try:
        choices = chunk.choices
    except Exception:
        return ""
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    return getattr(delta, "content", None) or ""


def count_tokens(messages: Sequence[dict[str, Any]], model: str) -> int:
    try:
        return int(_lazy_litellm().token_counter(model=model, messages=list(messages)) or 0)
    except Exception:
        chars = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                chars += len(content)
        return max(1, chars // 4)


def _resolve_arguments(frags: list[str]) -> str:
    if not frags:
        return "{}"
    concat = "".join(frags)
    if _is_json(concat):
        return concat
    valid = [f for f in frags if _is_json(f)]
    return max(valid, key=len) if valid else frags[-1]


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False
