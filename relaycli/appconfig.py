"""Persistent app configuration: preferences, model tiers, providers, roles.

This is the config/data layer for the roster system. It reads and writes the
nested sections of ``~/.relaycli/config.toml``:

    [preferences]           # permission_mode, theme, max_context_tokens, …
    [models]                # tier name -> concrete model id (fast/balanced/strong)
    [providers.<name>]      # api_key ("env:VAR" or literal) + optional base_url
    [roles.<id>]            # enabled (bool), model (tier name or concrete id)

It coexists with the flat keys the pydantic :class:`~relaycli.config.Settings`
reads (``model``, ``relay_enabled``, provider keys) — save() round-trips the
whole file, updating only the sections it manages and preserving the rest.

SECURITY: literal keys live only in the ``0600`` file, are never logged or
printed, and are always masked in output; the preferred form is an env
reference (``env:OPENAI_API_KEY``) resolved at read time. Env wins over a
stored literal (secrets), while config wins for preferences and roles.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from relaycli.config import CONFIG_FILE, ensure_config_dir
from relaycli.roles import BUILTIN_ROLES, TIERS, RoleDef, builtin_role
from relaycli.tools.base import atomic_write

# Canonical env var per provider (for env-reference defaults and "env wins").
PROVIDER_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "zhipu": "ZHIPUAI_API_KEY",
}

DEFAULT_PREFERENCES: dict[str, object] = {
    "permission_mode": "suggest",
    "theme": "dark",
    "max_context_tokens": 120_000,
}

# Example tier→model defaults (user-settable via `config tier`). Point at
# OpenRouter open-weights so a fresh install with an OpenRouter key works.
DEFAULT_TIERS: dict[str, str] = {
    "fast": "openrouter/openai/gpt-oss-120b:free",
    "balanced": "openrouter/cohere/north-mini-code:free",
    "strong": "openrouter/qwen/qwen3-coder:free",
}


@dataclass
class ProviderConfig:
    api_key: str | None = None   # "env:VAR" reference OR a literal secret
    base_url: str | None = None


@dataclass
class RoleConfig:
    enabled: bool | None = None  # None = use the built-in default
    model: str | None = None     # tier name or concrete id; None = default tier


@dataclass
class AppConfig:
    path: Path
    preferences: dict[str, object] = field(default_factory=dict)
    tiers: dict[str, str] = field(default_factory=dict)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    roles: dict[str, RoleConfig] = field(default_factory=dict)
    _raw: dict = field(default_factory=dict)  # full file, to preserve unknown keys

    # -- effective values (defaults + overrides) --------------------------
    def preference(self, key: str) -> object:
        return self.preferences.get(key, DEFAULT_PREFERENCES.get(key))

    def tier_model(self, tier: str) -> str | None:
        return self.tiers.get(tier) or DEFAULT_TIERS.get(tier)

    def role_enabled(self, role_id: str) -> bool:
        rc = self.roles.get(role_id)
        if rc is not None and rc.enabled is not None:
            return rc.enabled
        b = builtin_role(role_id)
        return b.enabled_by_default if b else True

    def role_assignment(self, role_id: str) -> str:
        """The role's assigned model or tier name (override or built-in tier)."""
        rc = self.roles.get(role_id)
        if rc is not None and rc.model:
            return rc.model
        b = builtin_role(role_id)
        return b.default_model_tier if b else "balanced"


def _provider_env(provider: str) -> str:
    return PROVIDER_ENV.get(provider, f"{provider.upper()}_API_KEY")


def load_app_config(path: Path | None = None) -> AppConfig:
    """Read the config file (missing file → all defaults).

    ``path`` defaults to the module's :data:`CONFIG_FILE` resolved at CALL
    time, so tests can redirect it with ``monkeypatch.setattr``.
    """
    path = path or CONFIG_FILE
    raw: dict = {}
    if path.exists():
        try:
            with open(path, "rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            raw = {}

    providers = {
        name: ProviderConfig(api_key=tbl.get("api_key"), base_url=tbl.get("base_url"))
        for name, tbl in (raw.get("providers") or {}).items()
        if isinstance(tbl, dict)
    }
    roles = {
        rid: RoleConfig(enabled=tbl.get("enabled"), model=tbl.get("model"))
        for rid, tbl in (raw.get("roles") or {}).items()
        if isinstance(tbl, dict)
    }
    return AppConfig(
        path=path,
        preferences=dict(raw.get("preferences") or {}),
        tiers=dict(raw.get("models") or {}),
        providers=providers,
        roles=roles,
        _raw=raw,
    )


def save_app_config(cfg: AppConfig) -> None:
    """Write the managed sections back, preserving every other key. 0600, atomic."""
    ensure_config_dir()
    raw = dict(cfg._raw)  # keep unknown top-level keys (model, relay_*, flat keys)
    if cfg.preferences:
        raw["preferences"] = dict(cfg.preferences)
    if cfg.tiers:
        raw["models"] = dict(cfg.tiers)
    if cfg.providers:
        raw["providers"] = {
            name: {k: v for k, v in (("api_key", p.api_key), ("base_url", p.base_url))
                   if v is not None}
            for name, p in cfg.providers.items()
        }
    if cfg.roles:
        raw["roles"] = {
            rid: {k: v for k, v in (("enabled", r.enabled), ("model", r.model))
                  if v is not None}
            for rid, r in cfg.roles.items()
        }
    cfg._raw = raw
    atomic_write(cfg.path, _dump_toml(raw))
    try:
        os.chmod(cfg.path, 0o600)  # atomic_write preserves existing mode; force 0600
    except OSError:
        pass


def set_base_model(model: str, path: Path | None = None) -> None:
    """Persist the flat runtime ``model`` key without disturbing sections."""
    model = model.strip()
    if not model:
        raise ValueError("model id required")
    cfg = load_app_config(path)
    cfg._raw["model"] = model
    recent = cfg._raw.get("recent_models")
    if not isinstance(recent, list):
        recent = []
    cfg._raw["recent_models"] = [model, *[str(m) for m in recent if str(m) != model]][:8]
    save_app_config(cfg)


def recent_models(path: Path | None = None) -> list[str]:
    """Most recently selected runtime models, newest first."""
    raw = load_app_config(path)._raw.get("recent_models")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        model = str(item).strip()
        if model and model not in out:
            out.append(model)
    return out


# -- security helpers -----------------------------------------------------
def mask_key(raw: str | None) -> str:
    """Display form for a stored key: env-ref shown, literal masked, never raw."""
    if not raw:
        return "not set"
    if raw.startswith("env:"):
        return f"via env ({raw[4:]})"
    if len(raw) <= 8:
        return "•" * len(raw)
    return f"{raw[:3]}…{raw[-4:]}"


def resolve_provider_key(cfg: AppConfig, provider: str) -> str | None:
    """The concrete key for ``provider`` — env wins, then the stored value.

    Env references (``env:VAR``) resolve against the process environment;
    literals are returned as-is. Returns None when nothing is available.
    """
    env_var = _provider_env(provider)
    if os.environ.get(env_var):            # env wins for secrets
        return os.environ[env_var]
    stored = cfg.providers.get(provider)
    if stored is None or not stored.api_key:
        return None
    if stored.api_key.startswith("env:"):
        return os.environ.get(stored.api_key[4:])
    return stored.api_key


# -- model resolution -----------------------------------------------------
def resolve_role_model(cfg: AppConfig, role_id: str) -> tuple[str | None, str | None]:
    """Return (concrete_model_id, error). error is a readable message or None.

    A role's assignment is either a concrete model id (used directly) or a tier
    name, mapped through [models]. An unset tier is a clear, actionable error.
    """
    assigned = cfg.role_assignment(role_id)
    if assigned in TIERS:
        model = cfg.tier_model(assigned)
        if not model:
            return None, f"tier '{assigned}' has no model set (config tier {assigned} <model-id>)"
        return model, None
    return assigned, None


@dataclass
class ResolvedRole:
    """A role's effective state, for tables and menus."""

    id: str
    display_name: str
    enabled: bool
    assigned: str            # tier name or concrete id
    model: str | None        # resolved concrete model
    error: str | None        # resolution problem, if any


def effective_roles(cfg: AppConfig) -> list[ResolvedRole]:
    """All roles (built-ins + any config-only) with their resolved state."""
    ids: list[str] = [r.id for r in BUILTIN_ROLES]
    for rid in cfg.roles:
        if rid not in ids:
            ids.append(rid)
    out: list[ResolvedRole] = []
    for rid in ids:
        b: RoleDef | None = builtin_role(rid)
        model, error = resolve_role_model(cfg, rid)
        out.append(ResolvedRole(
            id=rid,
            display_name=b.display_name if b else rid,
            enabled=cfg.role_enabled(rid),
            assigned=cfg.role_assignment(rid),
            model=model,
            error=error,
        ))
    return out


# -- minimal TOML writer (stdlib has a reader, not a writer) --------------
def _toml_scalar(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int) or isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    raise TypeError(f"cannot serialize {type(v).__name__} to TOML")


def _dump_table(name: str, tbl: dict, lines: list[str]) -> None:
    scalars = {k: v for k, v in tbl.items() if not isinstance(v, dict)}
    subtables = {k: v for k, v in tbl.items() if isinstance(v, dict)}
    lines.append(f"[{name}]")
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_scalar(v)}")
    for k, v in subtables.items():
        lines.append("")
        _dump_table(f"{name}.{k}", v, lines)


def _dump_toml(data: dict) -> str:
    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_scalar(v)}")
    for k, v in tables.items():
        lines.append("")
        _dump_table(k, v, lines)
    return "\n".join(lines).lstrip("\n") + "\n"
