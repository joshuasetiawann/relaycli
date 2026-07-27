"""Tests for the Stage 2 routing policy engine in relaycli/agent/router.py:
candidate lists, local-first ordering, --offline enforcement, health
tracking, and escalation-on-failure. Pre-existing resolve_model/
role_enabled/routing_table behavior is covered in test_relay.py; this file
is only the new Stage 2 surface."""

from __future__ import annotations

import asyncio

import pytest

from unittest.mock import patch as mock_patch

from relaycli.agent.router import (
    HealthTracker,
    Role,
    RoutingError,
    call_with_escalation,
    classify_failure,
    detect_9router,
    is_local,
    resolve_candidates,
    resolve_model,
)
from relaycli.config import manager as appconfig
from relaycli.config.manager import load_app_config, save_app_config
from relaycli.core.config import Settings


@pytest.fixture(autouse=True)
def _temp_config(monkeypatch, tmp_path):
    """Tier resolution reads relaycli.config.manager.AppConfig.tiers, the
    same storage `relaycli config tier` writes — isolate it from the real
    ~/.relaycli/config.toml the way test_roster.py does."""
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "config.toml")


def _settings(**overrides):
    overrides.setdefault("model", "base/model")
    return Settings(_env_file=None, **overrides)


def _set_tiers(**tiers):
    """Persist tier assignments the way `relaycli config tier` does, so a
    fresh load_app_config() call inside agent.router sees them too."""
    cfg = load_app_config()
    cfg.tiers.update(tiers)
    save_app_config(cfg)


# --- is_local -------------------------------------------------------------
def test_is_local_recognizes_ollama_prefixes():
    assert is_local("ollama_chat/llama3.1")
    assert is_local("ollama/llama3.1")
    assert not is_local("gpt-4o")
    assert not is_local("openrouter/qwen/qwen3-coder:free")


# --- resolve_model backward compatibility with tier fallback --------------
def test_resolve_model_still_prefers_role_override():
    s = _settings(coder_model="strong/coder")
    assert resolve_model(s, Role.coder) == "strong/coder"


def test_resolve_model_falls_back_to_tier_when_no_role_override():
    _set_tiers(strong="tier-strong/model")
    s = _settings()
    # coder's default tier is "strong" and has no role override configured.
    assert resolve_model(s, Role.coder) == "tier-strong/model"


def test_resolve_model_falls_back_to_base_model_with_nothing_configured():
    s = _settings()
    assert resolve_model(s, Role.coder) == "base/model"
    assert resolve_model(s, Role.explorer) == "base/model"


# --- resolve_candidates -----------------------------------------------------
def test_resolve_candidates_single_entry_when_no_tier_configured():
    s = _settings()
    assert resolve_candidates(s, Role.coder) == ["base/model"]


def test_resolve_candidates_escalates_through_unused_tiers():
    _set_tiers(fast="local/fast", strong="cloud/strong")
    s = _settings()
    # explorer's tier is "fast"; balanced has no override so it falls
    # through to strong ("cloud/strong"), which then dedupes against the
    # explicit strong-tier escalation step.
    assert resolve_candidates(s, Role.explorer) == ["local/fast", "cloud/strong"]


def test_resolve_candidates_local_first_when_primary_is_local():
    _set_tiers(fast="ollama_chat/llama3.1", strong="gpt-4o")
    s = _settings()
    candidates = resolve_candidates(s, Role.explorer)
    assert candidates[0] == "ollama_chat/llama3.1"
    assert is_local(candidates[0])
    assert "gpt-4o" in candidates[1:]


def test_resolve_candidates_never_empty():
    for role in Role:
        assert resolve_candidates(_settings(), role)


def test_resolve_candidates_ignores_default_tiers_when_unconfigured():
    """AppConfig.tier_model() falls back to DEFAULT_TIERS (hardcoded local
    Ollama models) for the roster — but resolve_candidates must NOT: an
    unconfigured pipeline role should resolve to settings.model, not
    silently start targeting a local model the user never asked for. See
    _tier_model's docstring for the reasoning."""
    assert appconfig.DEFAULT_TIERS  # sanity: the roster default really exists
    s = _settings(model="cloud/base")
    assert resolve_candidates(s, Role.coder) == ["cloud/base"]


# --- --offline --------------------------------------------------------------
def test_offline_keeps_only_local_candidates():
    _set_tiers(fast="ollama_chat/llama3.1", strong="gpt-4o")
    s = _settings(offline=True)
    candidates = resolve_candidates(s, Role.explorer)
    assert candidates == ["ollama_chat/llama3.1"]
    assert all(is_local(m) for m in candidates)


def test_offline_raises_when_no_local_candidate_exists():
    """The critical guarantee: --offline must never silently fall back to
    a cloud candidate — that would defeat the entire point of the flag."""
    s = _settings(offline=True, model="gpt-4o")  # no local model configured anywhere
    with pytest.raises(RoutingError, match="offline"):
        resolve_candidates(s, Role.coder)


def test_offline_without_offline_flag_keeps_cloud_candidates():
    s = _settings(model="gpt-4o")
    assert resolve_candidates(s, Role.coder) == ["gpt-4o"]


# --- classify_failure --------------------------------------------------------
def test_classify_failure_auth():
    class AuthenticationError(Exception):
        pass
    assert classify_failure(AuthenticationError("bad key")) == "auth_failed"


def test_classify_failure_rate_limit():
    class RateLimitError(Exception):
        pass
    assert classify_failure(RateLimitError("429 too many requests")) == "rate_limited"


def test_classify_failure_connection():
    assert classify_failure(ConnectionError("Connection refused")) == "unavailable"
    assert classify_failure(TimeoutError("timed out")) == "unavailable"


def test_classify_failure_generic():
    assert classify_failure(ValueError("something else entirely")) == "error"


def test_classify_failure_handles_llmerror_wrapped_exceptions():
    """Regression: the real call site (agent/loop.py's Agent) only ever
    sees relaycli.core.llm.LLM.complete()'s already-wrapped LLMError, whose
    own type name is always "LLMError" — the original exception's type
    name (AuthenticationError, RateLimitError, ...) only survives inside
    _wrap_error's message text. Checking type(exc).__name__ alone silently
    misclassified every one of these as "error" until this was caught."""
    from relaycli.core.llm import LLMError

    auth = LLMError("Model call failed for 'gpt-4o' (AuthenticationError): invalid api key")
    assert classify_failure(auth) == "auth_failed"

    rate_limited = LLMError("Model call failed for 'gpt-4o' (RateLimitError): 429 too many requests")
    assert classify_failure(rate_limited) == "rate_limited"

    unavailable = LLMError(
        "Model call failed for 'ollama_chat/llama3.1' (APIConnectionError): Connection refused"
    )
    assert classify_failure(unavailable) == "unavailable"


# --- HealthTracker -----------------------------------------------------------
def test_health_tracker_starts_healthy():
    h = HealthTracker()
    assert h.is_healthy("any/model")


def test_health_tracker_unhealthy_after_failure():
    h = HealthTracker()
    h.record_failure("flaky/model", "unavailable")
    assert not h.is_healthy("flaky/model")


def test_health_tracker_recovers_on_success():
    h = HealthTracker()
    h.record_failure("flaky/model", "unavailable")
    h.record_success("flaky/model")
    assert h.is_healthy("flaky/model")


def test_health_tracker_orders_unhealthy_candidates_last():
    h = HealthTracker()
    h.record_failure("a", "unavailable")
    ordered = h.order_by_health(["a", "b", "c"])
    assert ordered[0] != "a"
    assert set(ordered) == {"a", "b", "c"}  # nothing dropped, only reordered


# --- call_with_escalation ----------------------------------------------------
def test_call_with_escalation_succeeds_on_first_candidate():
    _set_tiers(strong="only/model")
    s = _settings()

    async def _run():
        calls = []

        async def call(model):
            calls.append(model)
            return "result"

        result = await call_with_escalation(s, Role.coder, call)
        assert result == "result"
        assert calls == ["only/model"]

    asyncio.run(_run())


def test_call_with_escalation_falls_through_to_next_candidate():
    _set_tiers(fast="local/fast", strong="cloud/strong")
    s = _settings()

    async def _run():
        calls = []

        async def call(model):
            calls.append(model)
            if model == "local/fast":
                raise ConnectionError("refused")
            return f"ok:{model}"

        result = await call_with_escalation(s, Role.explorer, call)
        assert result == "ok:cloud/strong"
        assert calls == ["local/fast", "cloud/strong"]

    asyncio.run(_run())


def test_call_with_escalation_reraises_last_failure_when_all_fail():
    _set_tiers(fast="local/fast", strong="cloud/strong")
    s = _settings()

    async def _run():
        async def call(model):
            raise RuntimeError(f"boom:{model}")

        with pytest.raises(RuntimeError, match="boom:cloud/strong"):
            await call_with_escalation(s, Role.explorer, call)

    asyncio.run(_run())


# --- detect_9router ----------------------------------------------------
def test_detect_9router_true_when_reachable():
    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with mock_patch("urllib.request.urlopen", return_value=_Resp()):
        assert detect_9router(_settings()) is True


def test_detect_9router_false_when_unreachable():
    with mock_patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("nope")):
        assert detect_9router(_settings()) is False


def test_detect_9router_false_on_non_2xx():
    class _Resp:
        status = 500
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with mock_patch("urllib.request.urlopen", return_value=_Resp()):
        assert detect_9router(_settings()) is False


def test_detect_9router_uses_configured_base_url():
    seen = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        return _Resp()

    s = _settings(nine_router_base_url="http://example:9999/v1")
    with mock_patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert detect_9router(s) is True
    assert seen["url"] == "http://example:9999/v1/models"


def test_call_with_escalation_records_health():
    _set_tiers(fast="local/fast", strong="cloud/strong")
    s = _settings()

    async def _run():
        health = HealthTracker()

        async def call(model):
            if model == "local/fast":
                raise ConnectionError("refused")
            return "ok"

        await call_with_escalation(s, Role.explorer, call, health=health)
        assert not health.is_healthy("local/fast")
        assert health.is_healthy("cloud/strong")

    asyncio.run(_run())
