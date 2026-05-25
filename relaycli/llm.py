"""LiteLLM wrapper — the single gateway for every model call.

All provider access in RelayCLI flows through this module; nothing else
imports LiteLLM directly. The wrapper:

* normalizes the provider response into :class:`LLMResponse`
  (plain text + a list of :class:`ToolCall`),
* supports streaming via an ``on_token`` callback,
* captures token usage and an estimated cost per call,
* raises :class:`LLMError` with a clear message (never a raw stack trace)
  when a key or model is missing or a provider call fails.

Credentials come exclusively from :mod:`relaycli.config`. Keys are passed
directly to LiteLLM (not exported into the process environment) by this module.
Note that a user's shell may still hold provider keys as real environment
variables; ``run_command`` therefore scrubs the known provider-key names from a
spawned command's environment so a command cannot read them back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from relaycli.config import Settings, get_settings

_litellm: Any = None


def _lazy_litellm() -> Any:
    """Import LiteLLM on first real use, configured once.

    The import pulls in the whole litellm/openai module tree — tens of
    seconds from a cold disk — so it must never run at startup: the banner,
    preflight, and `relaycli config` all stay import-free. Configuration:
    drop_params silently drops params a provider doesn't support; telemetry
    off means never phone home.
    """
    global _litellm
    if _litellm is None:
        import litellm

        litellm.drop_params = True
        litellm.telemetry = False
        litellm.suppress_debug_info = True
        _litellm = litellm
    return _litellm


def is_warm() -> bool:
    """True once LiteLLM has been imported (i.e. a model call has started)."""
    return _litellm is not None


# LiteLLM provider id -> the Settings attribute holding that provider's key.
_PROVIDER_KEY_ATTR: dict[str, str] = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "gemini": "gemini_api_key",
    "groq": "groq_api_key",
    "mistral": "mistral_api_key",
    "openrouter": "openrouter_api_key",
}
_KEYLESS_PROVIDERS = {"ollama", "ollama_chat"}

# Model-id -> provider fast path for the no-network checks (preflight,
# key_status). Deliberately tiny and permissive: anything unrecognized
# returns None ("make no claim") and the real call path — which uses
# LiteLLM's full resolver — stays authoritative. Kept in sync with
# _PROVIDER_KEY_ATTR/_KEYLESS_PROVIDERS above.
_PREFIX_PROVIDERS = {
    "openai", "anthropic", "gemini", "groq", "mistral", "openrouter",
    "ollama", "ollama_chat",
}
_BARE_NAME_HINTS = (
    ("gpt-", "openai"),
    ("chatgpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "gemini"),
    ("mistral-", "mistral"),
    ("ministral-", "mistral"),
    ("codestral-", "mistral"),
)


def _resolve_provider(model: str) -> str | None:
    """Cheap model-id -> provider mapping; None when we can't tell."""
    head, sep, _ = model.partition("/")
    if sep and head in _PREFIX_PROVIDERS:
        return head
    for prefix, provider in _BARE_NAME_HINTS:
        if model.startswith(prefix):
            return provider
    return None


class LLMError(RuntimeError):
    """A configuration or provider error, carrying a user-facing message."""


def _missing_key_message(provider: str, model: str, attr: str) -> str:
    return (
        f"No API key configured for provider '{provider}' (model '{model}'). "
        f"Set {attr.upper()} in your environment / .env, or add it to "
        f"~/.relaycli/config.toml."
    )


def preflight_settings(settings: Settings) -> str | None:
    """Preflight every model a session with ``settings`` would use.

    Checks the base model plus, when the relay pipeline is enabled, each
    routed role model. Returns the first problem found, or None.
    """
    llm = LLM(settings)
    models = [settings.model]
    if settings.relay_enabled:
        from relaycli.router import routing_table  # local: keep layering flat

        models.extend(routing_table(settings).values())
    for model in dict.fromkeys(models):
        problem = llm.preflight(model)
        if problem:
            return problem
    return None


def key_status(settings: Settings, model: str | None = None) -> str | None:
    """Classify the credential state for ``model``.

    Returns ``"detected"`` / ``"missing"`` / ``"not needed"``, or None when
    this module doesn't manage the provider's key (unknown/custom providers
    may be configured through LiteLLM's own env) — callers should then make
    no claim about credentials. Uses the no-import fast resolver so banners
    stay instant; the real call path is the authority.
    """
    model = model or settings.model
    provider = _resolve_provider(model)
    if provider is None:
        return None
    if provider in _KEYLESS_PROVIDERS:
        return "not needed"
    attr = _PROVIDER_KEY_ATTR.get(provider)
    if attr is None:
        return None
    return "detected" if getattr(settings, attr) else "missing"


@dataclass
class ToolCall:
    """A single tool/function call requested by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string exactly as the model produced it

    def parsed_arguments(self) -> dict[str, Any]:
        """Best-effort parse of the JSON argument string."""
        raw = (self.arguments or "").strip()
        if not raw:
            return {}
        return json.loads(raw)


@dataclass
class Usage:
    """Token accounting + estimated USD cost for one or more calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


@dataclass
class LLMResponse:
    """Normalized result of a single model call."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    model: str | None = None

    def to_assistant_message(self) -> dict[str, Any]:
        """Re-serialize this response as an OpenAI-style assistant message.

        Used to append the model's turn (including its tool calls) back into
        the conversation before sending tool results.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        return msg


def make_tool_result_message(tool_call: ToolCall, content: str) -> dict[str, Any]:
    """Build the ``role: tool`` message carrying a tool's output."""
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "name": tool_call.name,
        "content": content,
    }


class LLM:
    """Thin, normalized client over LiteLLM for one configured session."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- public API ------------------------------------------------------
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
        """Call the model once and return a normalized :class:`LLMResponse`.

        If ``stream`` is true (or an ``on_token`` callback is given), the reply
        is streamed; ``on_token`` receives each text delta as it arrives and the
        full structured response (text + tool calls + usage) is still returned.
        """
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
        """Generator yielding text deltas as they stream in.

        Convenience wrapper around :meth:`complete` for simple display loops
        that don't need the final structured response.
        """
        queue: list[str] = []
        self.complete(
            messages,
            tools=tools,
            model=model,
            temperature=temperature,
            stream=True,
            on_token=queue.append,
        )
        yield from queue

    # -- internals -------------------------------------------------------
    def _build_call_args(
        self,
        messages: Sequence[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        temperature: float | None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": self.settings.temperature if temperature is None else temperature,
        }
        args.update(self._credential_kwargs(model))
        if tools:
            args["tools"] = tools
            args["tool_choice"] = "auto"
        return args

    def _credential_kwargs(self, model: str) -> dict[str, Any]:
        """Resolve provider-specific credentials for ``model``.

        Raises :class:`LLMError` for an unknown model or a missing key.
        """
        try:
            _, provider, _, _ = _lazy_litellm().get_llm_provider(model)
        except Exception as exc:  # unknown/malformed model id
            raise LLMError(
                f"Could not determine a provider for model '{model}'. "
                f"Check the model id (e.g. 'gpt-4o-mini', 'claude-3-5-sonnet-latest', "
                f"'ollama_chat/llama3.1'). ({exc})"
            ) from exc

        if provider in _KEYLESS_PROVIDERS:
            return {"api_base": self.settings.ollama_base_url}

        attr = _PROVIDER_KEY_ATTR.get(provider)
        if attr is not None:
            key = getattr(self.settings, attr)
            if not key:
                raise LLMError(_missing_key_message(provider, model, attr))
            return {"api_key": key}

        # Provider we don't special-case: let LiteLLM read its own env var.
        return {}

    def preflight(self, model: str | None = None) -> str | None:
        """Return the credential problem for ``model``, or None if runnable.

        A no-network, no-import check so UIs can warn at startup instead of
        failing on the first request. Only providers this module manages keys
        for are judged: unknown/custom providers pass (LiteLLM may resolve
        their own env), and so does an unrecognizable model id (the real call
        surfaces that error with full context).
        """
        model = model or self.settings.model
        provider = _resolve_provider(model)
        if provider is None:
            return None
        if provider in _KEYLESS_PROVIDERS:
            return None
        attr = _PROVIDER_KEY_ATTR.get(provider)
        if attr is not None and not getattr(self.settings, attr):
            return _missing_key_message(provider, model, attr)
        return None

    def _complete_blocking(self, call_args: dict[str, Any], model: str) -> LLMResponse:
        try:
            resp = _lazy_litellm().completion(**call_args)
            # Normalize inside the try so a malformed/empty response is surfaced
            # as a clean LLMError, not a raw traceback (the module's contract).
            return self._normalize(resp, model)
        except Exception as exc:
            raise self._wrap_error(exc, model) from exc

    def _complete_streaming(
        self,
        call_args: dict[str, Any],
        model: str,
        on_token: Callable[[str], None] | None,
    ) -> LLMResponse:
        # include_usage: ask the provider to emit a final usage chunk so token
        # counts/cost are the provider's real numbers rather than a heuristic
        # (which under-counts tool-call turns). drop_params=True means providers
        # that don't support it silently ignore it.
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

        # Reassemble ourselves. We do NOT use litellm.stream_chunk_builder for
        # tool calls: some providers (Ollama) stream the *full* arguments in
        # every chunk rather than incremental fragments, and naive
        # concatenation doubles the JSON ({"x":1}{"x":1}).
        return self._reassemble(chunks, model, call_args["messages"])

    def _reassemble(
        self, chunks: list[Any], model: str, messages: list[dict[str, Any]]
    ) -> LLMResponse:
        text_parts: list[str] = []
        tool_acc: dict[Any, dict[str, Any]] = {}
        order: list[Any] = []
        usage_obj: Any = None
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
            tool_calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{i}",
                    name=slot["name"],
                    arguments=_resolve_arguments(slot["frags"]),
                )
            )

        text = "".join(text_parts)
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=self._usage_from_stream(usage_obj, model, messages, text, tool_calls),
            finish_reason=finish_reason,
            model=model,
        )

    def _usage_from_stream(
        self,
        usage_obj: Any,
        model: str,
        messages: list[dict[str, Any]],
        text: str,
        tool_calls: list[ToolCall] | None = None,
    ) -> Usage:
        if usage_obj is not None:
            pt = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage_obj, "completion_tokens", 0) or 0)
            tt = int(getattr(usage_obj, "total_tokens", 0) or (pt + ct))
        else:
            # Fallback estimate: count the tool-call payload too, not just the
            # text — tool-call-only turns would otherwise be scored as 0.
            completion = text + "".join(
                f"{tc.name}{tc.arguments}" for tc in (tool_calls or [])
            )
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
                pc, cc = _lazy_litellm().cost_per_token(
                    model=model, prompt_tokens=pt, completion_tokens=ct
                )
                cost = float((pc or 0.0) + (cc or 0.0))
            except Exception:
                cost = 0.0
        return Usage(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt, cost_usd=cost)

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        choices = getattr(resp, "choices", None)
        if not choices:
            raise LLMError(f"Model '{model}' returned no choices (empty/blocked response).")
        choice = choices[0]
        message = getattr(choice, "message", None)
        text = getattr(message, "content", None) or "" if message is not None else ""

        tool_calls: list[ToolCall] = []
        for idx, tc in enumerate(getattr(message, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) or ""
            arguments = getattr(fn, "arguments", None) or "{}"
            tool_calls.append(
                ToolCall(id=getattr(tc, "id", None) or f"call_{idx}", name=name, arguments=arguments)
            )

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=self._usage(resp, model),
            finish_reason=getattr(choice, "finish_reason", None),
            model=model,
        )

    def _usage(self, resp: Any, model: str) -> Usage:
        u = getattr(resp, "usage", None)
        pt = int(getattr(u, "prompt_tokens", 0) or 0)
        ct = int(getattr(u, "completion_tokens", 0) or 0)
        tt = int(getattr(u, "total_tokens", 0) or (pt + ct))
        cost = 0.0
        try:
            cost = float(_lazy_litellm().completion_cost(completion_response=resp) or 0.0)
        except Exception:
            cost = 0.0  # local/unknown-priced models have no cost data
        return Usage(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt, cost_usd=cost)

    def _wrap_error(self, exc: Exception, model: str) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        name = type(exc).__name__
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else name
        return LLMError(f"Model call failed for '{model}' ({name}): {detail}")


def _chunk_text(chunk: Any) -> str:
    """Extract the text delta from a streaming chunk, tolerating odd shapes."""
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
    """Estimate the token count of ``messages`` for ``model``.

    Kept here so token counting (a LiteLLM facility) stays behind this gateway.
    Falls back to a ~4-chars/token heuristic if the provider tokenizer is
    unavailable.
    """
    try:
        return int(_lazy_litellm().token_counter(model=model, messages=list(messages)) or 0)
    except Exception:
        chars = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                chars += len(content)
        return max(1, chars // 4)


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def _resolve_arguments(frags: list[str]) -> str:
    """Turn streamed argument fragments into one JSON string.

    Handles two provider behaviours:
    * incremental (OpenAI): fragments concatenate into one JSON object.
    * non-incremental (Ollama): each fragment is the *complete* JSON, so
      concatenation would duplicate it — fall back to a single valid fragment.
    """
    if not frags:
        return "{}"
    concat = "".join(frags)
    if _is_json(concat):
        return concat
    valid = [f for f in frags if _is_json(f)]
    if valid:
        return max(valid, key=len)
    return frags[-1]
