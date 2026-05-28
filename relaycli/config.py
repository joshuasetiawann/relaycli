"""Configuration for RelayCLI, built on pydantic-settings.

Settings are resolved in the following precedence order (highest first):

1. Explicit constructor / CLI overrides (``init`` source).
2. Environment variables (provider keys use their standard names, e.g.
   ``OPENAI_API_KEY``; RelayCLI's own options use the ``RELAYCLI_`` prefix).
3. A local ``.env`` file in the current working directory.
4. ``~/.relaycli/config.toml``.
5. Field defaults.

Nothing here is hardcoded: every API key comes from the environment or a
user-owned config file. This module is intentionally the single source of
truth for runtime configuration.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CONFIG_DIR: Path = Path.home() / ".relaycli"
CONFIG_FILE: Path = CONFIG_DIR / "config.toml"

# Fields a *project-local* ``.env`` (read from the current working directory)
# must NOT be able to set. RelayCLI is designed to run inside arbitrary — and
# possibly untrusted — project directories, so a committed ``.env`` must never
# be able to silently escalate the permission mode (-> unattended command
# execution) or redirect model traffic to an attacker endpoint (-> exfiltration
# of file contents). These may still be set via the real environment, the
# user-owned ``~/.relaycli/config.toml``, or explicit CLI flags.
#
# Compared case-insensitively and listing every alias spelling, because a
# source keys aliased fields by the matched alias, not the field name.
_DOTENV_BLOCKED_FIELDS: frozenset[str] = frozenset(
    {
        "permission_mode",
        "relaycli_permission_mode",
        "ollama_base_url",
        "relaycli_ollama_base_url",
        "ollama_api_base",
    }
)


class _FilteredSource(PydanticBaseSettingsSource):
    """Wrap a settings source, dropping security-sensitive fields from it.

    Used to strip :data:`_DOTENV_BLOCKED_FIELDS` from the CWD ``.env`` source
    while leaving provider API keys and the model id loadable as documented.
    """

    def __init__(self, inner: PydanticBaseSettingsSource, blocked: frozenset[str]) -> None:
        super().__init__(inner.settings_cls)
        self._inner = inner
        self._blocked = blocked

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # pragma: no cover
        # Never used: __call__ is overridden to delegate to the wrapped source.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._inner().items() if k.lower() not in self._blocked}


def ensure_config_dir() -> Path:
    """Create ``~/.relaycli`` (if absent) with owner-only (0700) permissions.

    Prevents other local users from reading RelayCLI's on-disk state (e.g. the
    REPL history, which records typed prompts). ``mkdir`` mode is subject to the
    umask, so an explicit ``chmod`` is applied.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass  # best effort (e.g. non-POSIX or a mount that ignores chmod)
    return CONFIG_DIR


class PermissionMode(str, Enum):
    """How aggressively RelayCLI is allowed to act without asking."""

    suggest = "suggest"        # ask before any edit or command (default, safest)
    auto_edit = "auto-edit"    # auto-apply edits, still ask before commands
    full_auto = "full-auto"    # never prompt (a banner is shown when active)

    def __str__(self) -> str:  # nicer display in banners / Typer
        return self.value


class Settings(BaseSettings):
    """Runtime configuration for a RelayCLI session."""

    model_config = SettingsConfigDict(
        env_prefix="RELAYCLI_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        toml_file=CONFIG_FILE,
    )

    # --- Core behaviour -------------------------------------------------
    model: str = Field(
        default="gpt-4o-mini",
        description="LiteLLM model id, e.g. 'gpt-4o-mini', 'claude-3-5-sonnet-latest', 'ollama/llama3.1'.",
    )
    permission_mode: PermissionMode = Field(
        default=PermissionMode.suggest,
        description="suggest | auto-edit | full-auto",
    )
    max_iterations: int = Field(
        default=50, ge=1, description="Hard cap on agent loop iterations."
    )
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    token_budget: int = Field(
        default=120_000,
        ge=1_000,
        description="Approximate token budget before the session trims history.",
    )

    # --- Relay pipeline (Planner → Coder → Reviewer) ---------------------
    relay_enabled: bool = Field(
        default=False,
        description="Run requests through the Planner → Coder → Reviewer relay pipeline.",
    )
    planner_model: str | None = Field(
        default=None,
        description="Model for the relay Planner role (falls back to 'model').",
    )
    coder_model: str | None = Field(
        default=None,
        description="Model for the relay Coder role (falls back to 'model').",
    )
    reviewer_model: str | None = Field(
        default=None,
        description="Model for the relay Reviewer role (falls back to 'model').",
    )
    max_review_cycles: int = Field(
        default=2,
        ge=0,
        description=(
            "Revision cycles allowed after a 'revise' verdict "
            "(0 = no retries; an unresolved 'revise' ends the run as review_exhausted)."
        ),
    )
    # Optional extra roles (opt-in: each adds a full agent run per request).
    relay_explorer: bool = Field(
        default=False,
        description="Add a read-only Explorer before the Planner (compact context brief).",
    )
    relay_tester: bool = Field(
        default=False,
        description="Add a Tester after the Coder (runs the plan's verification step).",
    )
    relay_split_tasks: bool = Field(
        default=False,
        description=(
            "Task-split mode: run one fresh Coder agent per numbered plan step "
            "(clean context each) instead of a single Coder for the whole plan."
        ),
    )
    explorer_model: str | None = Field(
        default=None,
        description="Model for the relay Explorer role (falls back to 'model').",
    )
    tester_model: str | None = Field(
        default=None,
        description="Model for the relay Tester role (falls back to 'model').",
    )

    # --- Provider credentials (standard env var names) ------------------
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "RELAYCLI_OPENAI_API_KEY"),
    )
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "RELAYCLI_ANTHROPIC_API_KEY"),
    )
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "RELAYCLI_GEMINI_API_KEY"
        ),
    )
    groq_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "RELAYCLI_GROQ_API_KEY"),
    )
    mistral_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MISTRAL_API_KEY", "RELAYCLI_MISTRAL_API_KEY"),
    )
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "RELAYCLI_OPENROUTER_API_KEY"),
        description="OpenRouter key; use with models like 'openrouter/qwen/qwen3-coder:free'.",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices(
            "OLLAMA_BASE_URL", "OLLAMA_API_BASE", "RELAYCLI_OLLAMA_BASE_URL"
        ),
        description="Base URL for a local/remote Ollama server (no API key needed).",
    )

    # --- Helpers --------------------------------------------------------
    def detected_providers(self) -> dict[str, bool]:
        """Report which providers have a usable credential available.

        Ollama is always listed as available because it needs no API key
        (reachability is checked lazily at call time, not here).
        """
        return {
            "openai": bool(self.openai_api_key),
            "anthropic": bool(self.anthropic_api_key),
            "gemini": bool(self.gemini_api_key),
            "groq": bool(self.groq_api_key),
            "mistral": bool(self.mistral_api_key),
            "openrouter": bool(self.openrouter_api_key),
            "ollama": True,
        }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the TOML config source below env/.env but above defaults.

        ``TomlConfigSettingsSource`` returns an empty mapping when the file is
        absent, so it is always included (a missing ``config.toml`` is fine).
        """
        return (
            init_settings,
            env_settings,
            _FilteredSource(dotenv_settings, _DOTENV_BLOCKED_FIELDS),
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance for the current process."""
    return Settings()


def reload_settings() -> Settings:
    """Clear the cache and re-read configuration (used by tests / `/reload`)."""
    get_settings.cache_clear()
    return get_settings()
