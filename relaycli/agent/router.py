"""Model routing: role -> ordered candidate list, with local-first
escalation and failover.

Was a ~30-line override lookup (resolve_model: role override or the base
model). Still does exactly that when asked for one model, but the real
output now is resolve_candidates(): an ordered list, so a failing model
falls over to the next one automatically instead of failing the whole
request. fast|balanced|strong stays the user-facing vocabulary (matches
RoleDef.default_model_tier in core/roles.py); this module resolves a tier
to a concrete model rather than the caller needing to know one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from relaycli.core.config import Settings
from relaycli.core.logging import get_logger

_log = get_logger(__name__)


class RoutingError(RuntimeError):
    """No usable candidate exists for a role under the current settings —
    e.g. --offline with no local model configured for that role's tier.
    Raised rather than silently falling back to a candidate that would
    violate the setting that triggered this (offline still calling a
    cloud model, in particular), since that failure mode is worse than
    an explicit, actionable error."""


class Role(str, Enum):
    """A relay pipeline role, declared in pipeline order."""

    explorer = "explorer"  # optional: scouts the codebase before planning
    planner = "planner"
    coder = "coder"
    tester = "tester"      # optional: runs the verification step after coding
    reviewer = "reviewer"

    def __str__(self) -> str:  # nicer display in banners / summaries
        return self.value


# Default tier per pipeline role, used only when nothing more specific
# (role override, then tier override) is configured. Coder/reviewer get the
# strongest tier since they do the work / gate quality; explorer/tester are
# comparatively mechanical.
_ROLE_TIER: dict[str, str] = {
    "explorer": "fast",
    "planner": "balanced",
    "coder": "strong",
    "tester": "fast",
    "reviewer": "strong",
}

TIER_ORDER: tuple[str, ...] = ("fast", "balanced", "strong")


def is_local(model: str) -> bool:
    """Whether `model` runs on a local Ollama instance rather than a cloud
    provider — the one signal this module treats as free/private."""
    return model.startswith(("ollama_chat/", "ollama/"))


def _tier_model(settings: Settings, tier: str) -> str:
    """The configured model for `tier`, falling back up through stronger
    tiers and finally to `settings.model`.

    Reads relaycli.config.manager.AppConfig.tiers — the same storage
    `relaycli config tier <name> <model>` already writes for the 16-role
    roster — rather than a second, parallel setting, so the one command
    configures tier resolution for both the pipeline and the roster.

    Deliberately does NOT fall back to AppConfig's own DEFAULT_TIERS (three
    hardcoded local Ollama models) the way the roster's resolve_role_model
    does: those defaults are appropriate for a roster specialist explicitly
    assigned a tier, but applying them here would mean every pipeline role
    with no configuration at all silently starts targeting a local model
    the user may not have installed, instead of `settings.model` — a much
    bigger, unrequested behavior change than Stage 2 asks for. A tier only
    affects the pipeline once a user explicitly sets it.
    """
    from relaycli.config.manager import load_app_config

    cfg = load_app_config()
    explicit: str | None = cfg.tiers.get(tier)
    if explicit:
        return explicit
    idx = TIER_ORDER.index(tier)
    for stronger in TIER_ORDER[idx + 1:]:
        explicit = cfg.tiers.get(stronger)
        if explicit:
            return explicit
    return settings.model


def resolve_model(settings: Settings, role: Role) -> str:
    """Return the model id serving `role`: the role override, else its
    tier's resolved model (which itself falls back to `settings.model`
    when no tier is configured — so this is a strict superset of the old
    "role override or settings.model" behavior, not a replacement of it)."""
    override: str | None = getattr(settings, f"{role.value}_model")
    if override:
        return override
    return _tier_model(settings, _ROLE_TIER.get(role.value, "balanced"))


def role_enabled(settings: Settings, role: Role) -> bool:
    """Whether ``role`` takes part in the pipeline under ``settings``."""
    if role is Role.explorer:
        return settings.relay_explorer
    if role is Role.tester:
        return settings.relay_tester
    return True  # planner/coder/reviewer are the pipeline's backbone


def routing_table(settings: Settings) -> dict[Role, str]:
    """Active role → resolved model, in pipeline order (banners, ``config``)."""
    return {
        role: resolve_model(settings, role)
        for role in Role
        if role_enabled(settings, role)
    }


# --- candidate lists: the actual policy engine --------------------------


def resolve_candidates(settings: Settings, role: Role) -> list[str]:
    """Ordered model candidates for `role`: the resolved primary model,
    then one tier up for each remaining tier, deduplicated in order.
    Local-first: if the primary is a local model, escalation only reaches
    a cloud one after the primary demonstrably fails elsewhere — this
    function only orders candidates, it never calls anything.

    --offline (settings.offline) drops every non-local candidate. If that
    empties the list entirely, raises RoutingError rather than falling
    back to a cloud candidate — silently keeping one would mean --offline
    doesn't actually guarantee what its name promises.
    """
    primary = resolve_model(settings, role)
    tier = _ROLE_TIER.get(role.value, "balanced")
    idx = TIER_ORDER.index(tier)

    candidates = [primary]
    for stronger in TIER_ORDER[idx + 1:]:
        model = _tier_model(settings, stronger)
        if model not in candidates:
            candidates.append(model)

    if settings.offline:
        local_only = [m for m in candidates if is_local(m)]
        if not local_only:
            raise RoutingError(
                f"--offline is set but no local model is configured for role "
                f"'{role.value}' (tier '{tier}') — every candidate "
                f"({', '.join(candidates)}) is a cloud model. Set "
                f"fast_model/balanced_model/strong_model (or "
                f"{role.value}_model directly) to an ollama_chat/... model."
            )
        return local_only
    return candidates


# --- provider health: informs (never silently skips) candidate order ----


@dataclass
class _ProviderHealth:
    consecutive_failures: int = 0
    last_failure_at: float | None = None
    last_reason: str | None = None


@dataclass
class HealthTracker:
    """Per-session record of recent failures, keyed by model id. Informs
    ordering (a model that just failed sorts after ones that haven't) but
    never removes a candidate outright — a transient failure shouldn't
    permanently blacklist a model for the rest of the session."""

    _state: dict[str, _ProviderHealth] = field(default_factory=dict)

    def record_failure(self, model: str, reason: str) -> None:
        entry = self._state.setdefault(model, _ProviderHealth())
        entry.consecutive_failures += 1
        entry.last_failure_at = time.time()
        entry.last_reason = reason
        _log.warning("provider health: %s failed (%s) — %d consecutive",
                      model, reason, entry.consecutive_failures)

    def record_success(self, model: str) -> None:
        if model in self._state:
            self._state[model] = _ProviderHealth()

    def is_healthy(self, model: str) -> bool:
        entry = self._state.get(model)
        return entry is None or entry.consecutive_failures == 0

    def order_by_health(self, candidates: list[str]) -> list[str]:
        """Stable-sort `candidates` so recently-failed models sort last,
        without dropping anything — see HealthTracker's own docstring."""
        return sorted(candidates, key=lambda m: (not self.is_healthy(m),))


def classify_failure(exc: Exception) -> str:
    """A short reason tag for an LLM call failure, for escalation logging
    and health tracking. Deliberately coarse (matches the keyword-based
    style relaycli.core.llm.LLM._wrap_error already uses for the same
    exceptions) rather than a rigid taxonomy this module would have to
    keep in lockstep with every provider SDK's exception hierarchy.

    Checks both the exception's own type name and its message text: the
    call site this feeds (relaycli.agent.loop.Agent) sees LLM.complete()'s
    already-wrapped LLMError, not the original provider exception — every
    real instance has the uniform type name "LLMError", with the original
    type (AuthenticationError, RateLimitError, ...) only surviving inside
    _wrap_error's message text. Checking type name only would silently
    misclassify every wrapped failure as "error" — verified directly
    against a wrapped LLMError, not just a raw exception, before trusting
    this branch (see test_router_policy.py).
    """
    name = type(exc).__name__.lower()
    detail = str(exc).lower()
    if "authenticationerror" in name or "authenticationerror" in detail or "unauthorized" in detail:
        return "auth_failed"
    if "ratelimit" in name or "ratelimit" in detail or "429" in detail or "rate limit" in detail:
        return "rate_limited"
    if any(kw in detail for kw in ("connection", "refused", "timed out", "timeout")):
        return "unavailable"
    return "error"


# --- escalation: one call site, used by the agent loop -------------------


async def call_with_escalation(settings, role: Role, call, *, health: HealthTracker | None = None):
    """Try each of `role`'s candidates in order, calling `call(model)` for
    each until one succeeds. `call` is an async callable taking a model id
    and returning the completion (or raising on failure — typically
    relaycli.core.llm.LLM.acomplete bound to a fixed set of other args).

    Escalates to the next tier on any failure, logging the reason (this is
    the log the master prompt asks for — it's what tells a user which
    tasks actually needed the expensive model). Re-raises the *last*
    failure if every candidate fails, since that's the most relevant one
    to show the user.
    """
    health = health if health is not None else HealthTracker()
    candidates = health.order_by_health(resolve_candidates(settings, role))
    last_exc: Exception | None = None
    for i, model in enumerate(candidates):
        try:
            result = await call(model)
            health.record_success(model)
            return result
        except Exception as exc:  # noqa: BLE001 - re-raised below if nothing else works
            reason = classify_failure(exc)
            health.record_failure(model, reason)
            last_exc = exc
            if i + 1 < len(candidates):
                _log.warning(
                    "escalating %s -> %s for role %s (reason: %s)",
                    model, candidates[i + 1], role.value, reason,
                )
    assert last_exc is not None  # candidates is never empty (see resolve_candidates)
    raise last_exc


def call_with_escalation_sync(settings, role: Role, call, *, health: HealthTracker | None = None):
    """Sync twin of call_with_escalation, for relaycli.agent.loop.Agent's
    still-synchronous run() — Stage 3 is what makes the agent loop async
    (asyncio.gather over multiple agents); until then this is the real
    integration point. Deliberately duplicated rather than sharing a body
    with the async version: a sync and an async loop can't share control
    flow without a callback-driven abstraction that would make both harder
    to follow for a ~15-line function."""
    health = health if health is not None else HealthTracker()
    candidates = health.order_by_health(resolve_candidates(settings, role))
    last_exc: Exception | None = None
    for i, model in enumerate(candidates):
        try:
            result = call(model)
            health.record_success(model)
            return result
        except Exception as exc:  # noqa: BLE001 - re-raised below if nothing else works
            reason = classify_failure(exc)
            health.record_failure(model, reason)
            last_exc = exc
            if i + 1 < len(candidates):
                _log.warning(
                    "escalating %s -> %s for role %s (reason: %s)",
                    model, candidates[i + 1], role.value, reason,
                )
    assert last_exc is not None
    raise last_exc


# --- 9Router detection ----------------------------------------------------


def detect_9router(settings: Settings | None = None, *, timeout: float = 0.5) -> bool:
    """Whether a 9Router instance (an OpenAI-compatible proxy doing its own
    cheapest-model routing, retries, tiered fallback, and usage tracking —
    see the v2 prompt's Part B §4.4) is reachable at nine_router_base_url.

    A short-timeout probe that never raises: unreachable, a timeout, or a
    non-2xx response are all just "not running", the common case for
    anyone not using it. Detection only — enabling it is a separate,
    explicit step (settings.use_9router / `relaycli config use-9router`),
    never automatic, so it can't silently redirect provider calls through
    something a user didn't ask for.
    """
    import urllib.error
    import urllib.request

    base_url = (settings.nine_router_base_url if settings else None) or "http://localhost:20128/v1"
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False
