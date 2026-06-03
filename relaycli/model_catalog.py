"""Model discovery and Ollama model management.

The UI and terminal config commands both need the same view of "models the
user can pick": curated fallbacks, live provider lists when a key is present,
and locally installed Ollama models. This module keeps that logic away from
the web handler so it is testable without a browser.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from relaycli.config import Settings
from relaycli.llm import ollama_models


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str
    desc: str
    group: str
    provider: str
    source: str = "catalog"
    current: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "id": self.id,
            "name": self.name,
            "desc": self.desc,
            "group": self.group,
            "provider": self.provider,
            "source": self.source,
            "current": self.current,
        }


PROVIDER_GROUP: dict[str, str] = {
    "openai": "GPT",
    "anthropic": "Claude",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "dashscope": "Qwen",
    "zhipu": "GLM",
    "groq": "Groq",
    "mistral": "Mistral",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
}

PROVIDER_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "zhipu": "ZHIPUAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

PROVIDER_SETTING_ATTR: dict[str, str] = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "gemini": "gemini_api_key",
    "groq": "groq_api_key",
    "mistral": "mistral_api_key",
    "openrouter": "openrouter_api_key",
}

STATIC_CATALOG: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("openai", "GPT", (
        ("gpt-4o", "OpenAI - catalog"),
        ("gpt-4o-mini", "OpenAI - catalog"),
    )),
    ("gemini", "Gemini", (
        ("gemini/gemini-1.5-pro", "Google - catalog"),
        ("gemini/gemini-1.5-flash", "Google - catalog"),
    )),
    ("anthropic", "Claude", (
        ("claude-3-5-sonnet-latest", "Anthropic - catalog"),
        ("claude-3-5-haiku-latest", "Anthropic - catalog"),
    )),
    ("groq", "Groq", (
        ("groq/llama-3.3-70b-versatile", "Groq - catalog"),
        ("groq/llama-3.1-8b-instant", "Groq - catalog"),
    )),
    ("mistral", "Mistral", (
        ("mistral/mistral-small-latest", "Mistral - catalog"),
        ("mistral/codestral-latest", "Mistral - catalog"),
    )),
    ("deepseek", "DeepSeek", (
        ("deepseek/deepseek-chat", "DeepSeek - catalog"),
        ("deepseek/deepseek-reasoner", "DeepSeek - catalog"),
    )),
    ("dashscope", "Qwen", (
        ("dashscope/qwen-max", "Alibaba - catalog"),
        ("dashscope/qwen-plus", "Alibaba - catalog"),
    )),
    ("zhipu", "GLM", (
        ("zhipu/glm-4-plus", "Zhipu - catalog"),
        ("zhipu/glm-4-flash", "Zhipu - catalog"),
    )),
    ("openrouter", "OpenRouter", (
        ("openrouter/cohere/north-mini-code:free", "Cohere - catalog"),
        ("openrouter/qwen/qwen3-coder:free", "Qwen coder - catalog"),
        ("openrouter/openai/gpt-oss-120b:free", "GPT-OSS 120B - catalog"),
        ("openrouter/meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 - catalog"),
        ("openrouter/qwen/qwen3-next-80b-a3b-instruct:free", "Qwen3-Next - catalog"),
        ("openrouter/deepseek/deepseek-v4-flash", "DeepSeek V4 - catalog"),
        ("openrouter/z-ai/glm-4.7", "GLM 4.7 - catalog"),
        ("openrouter/moonshotai/kimi-k2.6", "Kimi K2.6 - catalog"),
    )),
    ("ollama", "Ollama", (
        ("ollama_chat/llama3.1", "local - needs pull"),
        ("ollama_chat/qwen2.5-coder", "local - needs pull"),
    )),
)

_LIVE_CACHE: dict[tuple[str, str], tuple[float, list[tuple[str, str]]]] = {}
_LIVE_TTL_SECONDS = 300
_MAX_LIVE_MODELS = 250


def short_model_name(model: str) -> str:
    """Compact display name matching the web UI's existing behavior."""
    return model.rsplit("/", 1)[-1]


def provider_key(settings: Settings, provider: str) -> str | None:
    """Return the best available key without printing or logging it."""
    attr = PROVIDER_SETTING_ATTR.get(provider)
    if attr:
        value = getattr(settings, attr, None)
        if value:
            return value
    env = PROVIDER_ENV.get(provider)
    if env and os.environ.get(env):
        return os.environ[env]
    try:
        from relaycli.appconfig import load_app_config, resolve_provider_key

        return resolve_provider_key(load_app_config(), provider)
    except Exception:
        return None


def model_choices(
    settings: Settings,
    *,
    current: str | None = None,
    provider_filter: str | None = None,
    query: str | None = None,
    live: bool = True,
    timeout: float = 0.8,
) -> list[dict[str, str | bool]]:
    """Return a flat, grouped model catalog ready for web JSON or CLI tables."""
    current = current or settings.model
    provider_filter = (provider_filter or "").strip().lower() or None
    query = (query or "").strip().lower()

    choices: list[ModelChoice] = []
    for model_id in _recent_model_ids():
        choice = ModelChoice(
            id=model_id,
            name=short_model_name(model_id),
            desc="recently used",
            group="Recent",
            provider=_provider_from_model_id(model_id),
            source="recent",
            current=model_id == current,
        )
        if (not provider_filter or provider_filter in {"recent", choice.provider}) and _matches(choice, query):
            choices.append(choice)

    for provider, group, fallback in STATIC_CATALOG:
        if provider_filter and provider_filter not in {provider, group.lower()}:
            continue
        entries = list(fallback)
        source = "catalog"
        if provider == "ollama":
            installed = detected_ollama_models(settings, timeout=timeout)
            if installed:
                entries = installed
                source = "installed"
        elif live:
            fetched = live_provider_models(settings, provider, timeout=timeout)
            if fetched:
                entries = fetched
                source = "live"
        for model_id, desc in entries:
            if any(existing.id == model_id for existing in choices):
                continue
            choice = ModelChoice(
                id=model_id,
                name=short_model_name(model_id),
                desc=desc,
                group=group,
                provider=provider,
                source=source,
                current=model_id == current,
            )
            if _matches(choice, query):
                choices.append(choice)

    known = {choice.id for choice in choices}
    all_known = {mid for _, _, rows in STATIC_CATALOG for mid, _ in rows} | known
    if current and current not in all_known and _current_matches(current, query, provider_filter):
        choices.insert(0, ModelChoice(
            id=current,
            name=short_model_name(current),
            desc="in use",
            group="Current",
            provider=_provider_from_model_id(current),
            source="current",
            current=True,
        ))
    return [choice.as_dict() for choice in choices]


def _recent_model_ids() -> list[str]:
    try:
        from relaycli.appconfig import recent_models

        raw = recent_models()
    except Exception:
        raw = []
    out: list[str] = []
    for model in raw:
        if model and model not in out:
            out.append(model)
    return out[:8]


def detected_ollama_models(settings: Settings, *, timeout: float = 0.8) -> list[tuple[str, str]]:
    return [(f"ollama_chat/{name}", "local - installed") for name in ollama_models(settings, timeout=timeout)]


def live_provider_models(
    settings: Settings,
    provider: str,
    *,
    timeout: float = 0.8,
) -> list[tuple[str, str]]:
    key = provider_key(settings, provider)
    if not key:
        return []
    cache_key = (provider, hashlib.sha256(key.encode("utf-8")).hexdigest()[:16])
    cached = _LIVE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < _LIVE_TTL_SECONDS:
        return list(cached[1])
    try:
        models = _fetch_provider_models(provider, key, timeout=timeout)
    except Exception:
        models = []
    models = _dedupe_models(models)[:_MAX_LIVE_MODELS]
    if models:
        _LIVE_CACHE[cache_key] = (time.time(), models)
    return models


def pull_ollama_model(settings: Settings, model: str, *, timeout: float = 3600) -> str:
    """Pull MODEL into Ollama via the local HTTP API and return its clean name."""
    name = normalize_ollama_model_name(model)
    url = settings.ollama_base_url.rstrip("/") + "/api/pull"
    payload = json.dumps({"name": name, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "RelayCLI/desktop"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama pull failed: {exc}") from exc
    try:
        data = json.loads(body or "{}")
    except ValueError:
        data = {}
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"]))
    return name


def normalize_ollama_model_name(model: str) -> str:
    name = (model or "").strip()
    if name.startswith("ollama_chat/"):
        name = name.split("/", 1)[1]
    elif name.startswith("ollama/"):
        name = name.split("/", 1)[1]
    if not name:
        raise ValueError("Ollama model name required.")
    if any(ch.isspace() for ch in name) or any(ord(ch) < 32 for ch in name):
        raise ValueError("Ollama model names cannot contain whitespace or control characters.")
    return name


def _matches(choice: ModelChoice, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join((
        choice.id, choice.name, choice.desc, choice.group, choice.provider, choice.source,
    )).lower()
    return query in haystack


def _current_matches(current: str, query: str, provider_filter: str | None) -> bool:
    provider = _provider_from_model_id(current)
    if provider_filter and provider_filter not in {provider, PROVIDER_GROUP.get(provider, "").lower()}:
        return False
    if not query:
        return True
    return query in current.lower() or query in short_model_name(current).lower()


def _provider_from_model_id(model: str) -> str:
    head, sep, _ = model.partition("/")
    if sep:
        if head in PROVIDER_GROUP:
            return head
        if head == "ollama_chat":
            return "ollama"
    if model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")):
        return "openai"
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "gemini"
    return "custom"


def _dedupe_models(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for model_id, desc in rows:
        model_id = model_id.strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        out.append((model_id, desc))
    return out


def _fetch_provider_models(provider: str, key: str, *, timeout: float) -> list[tuple[str, str]]:
    if provider == "openai":
        data = _json_get(
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
    elif provider == "anthropic":
        data = _json_get(
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout,
        )
    elif provider == "gemini":
        qs = urllib.parse.urlencode({"key": key})
        data = _json_get(f"https://generativelanguage.googleapis.com/v1beta/models?{qs}", {}, timeout)
    elif provider == "groq":
        data = _json_get(
            "https://api.groq.com/openai/v1/models",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
    elif provider == "mistral":
        data = _json_get(
            "https://api.mistral.ai/v1/models",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
    elif provider == "openrouter":
        data = _json_get(
            "https://openrouter.ai/api/v1/models",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
    elif provider == "deepseek":
        data = _json_get(
            "https://api.deepseek.com/models",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
    elif provider == "dashscope":
        data = _json_get(
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
    elif provider == "zhipu":
        data = _json_get(
            "https://open.bigmodel.cn/api/paas/v4/models",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
    else:
        return []
    return _parse_model_response(provider, data)


def _json_get(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "RelayCLI/desktop",
            **headers,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_model_response(provider: str, data: dict[str, Any]) -> list[tuple[str, str]]:
    rows = data.get("data")
    if rows is None:
        rows = data.get("models")
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, str]] = []
    for item in rows:
        if isinstance(item, str):
            raw = item
        elif isinstance(item, dict):
            raw = item.get("id") or item.get("name")
        else:
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        model_id = _format_model_id(provider, raw.strip())
        out.append((model_id, f"{PROVIDER_GROUP.get(provider, provider)} - live"))
    return out


def _format_model_id(provider: str, raw: str) -> str:
    if provider == "gemini":
        raw = raw.removeprefix("models/")
        return raw if raw.startswith("gemini/") else f"gemini/{raw}"
    if provider in {"openai", "anthropic"}:
        return raw
    if provider == "openrouter":
        return raw if raw.startswith("openrouter/") else f"openrouter/{raw}"
    if provider == "dashscope":
        return raw if raw.startswith("dashscope/") else f"dashscope/{raw}"
    if provider == "zhipu":
        return raw if raw.startswith("zhipu/") else f"zhipu/{raw}"
    return raw if raw.startswith(f"{provider}/") else f"{provider}/{raw}"
