"""Central configuration — pydantic-settings with TOML + env + .env merge.

Precedence (highest first):
  1. Explicit constructor / CLI overrides
  2. Real environment variables (``OPENAI_API_KEY``, ``RELAYCLI_*``)
  3. ``.env`` from the working directory (blocked fields cannot be set here)
  4. ``~/.relaycli/config.toml``
  5. Defaults
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, TomlConfigSettingsSource

CONFIG_DIR: Path = Path.home() / ".relaycli"
CONFIG_FILE: Path = CONFIG_DIR / "config.toml"

_DOTENV_BLOCKED_FIELDS: frozenset[str] = frozenset({
    "permission_mode", "relaycli_permission_mode",
    "ollama_base_url", "relaycli_ollama_base_url", "ollama_api_base",
})


class _FilteredSource(PydanticBaseSettingsSource):
    def __init__(self, inner: PydanticBaseSettingsSource, blocked: frozenset[str]) -> None:
        super().__init__(inner.settings_cls)
        self._inner = inner
        self._blocked = blocked

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._inner().items() if k.lower() not in self._blocked}


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    return CONFIG_DIR


class PermissionMode(str, Enum):
    suggest = "suggest"
    auto_edit = "auto-edit"
    full_auto = "full-auto"

    def __str__(self) -> str:
        return self.value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RELAYCLI_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        toml_file=None,
    )

    model: str = Field(default="gpt-4o-mini")
    permission_mode: PermissionMode = Field(default=PermissionMode.suggest)
    max_iterations: int = Field(default=8, ge=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    token_budget: int = Field(default=120_000, ge=1_000)
    llm_timeout: int = Field(default=120, ge=10, description="Timeout in seconds for each LLM completion call")
    skills_auto: bool = Field(default=True)
    local_scaffolds: bool = Field(default=False)

    relay_enabled: bool = Field(default=False)
    planner_model: str | None = Field(default=None)
    coder_model: str | None = Field(default=None)
    reviewer_model: str | None = Field(default=None)
    max_review_cycles: int = Field(default=2, ge=0)
    relay_explorer: bool = Field(default=False)
    relay_tester: bool = Field(default=False)
    relay_split_tasks: bool = Field(default=False)
    explorer_model: str | None = Field(default=None)
    tester_model: str | None = Field(default=None)

    # Part A (parallel orchestration): the master prompt's own rule 2 —
    # "Parallelism lands behind a flag... users must be able to fall back."
    # False by default; sequential relay (relay_enabled) is unaffected by
    # this and keeps working exactly as before regardless of this flag.
    experimental_parallel: bool = Field(default=False)
    max_concurrent_agents: int = Field(default=3, ge=1)

    # Tier-based routing (v2 Part B): a pipeline role's default tier
    # resolves through relaycli.config.manager.AppConfig.tiers — the same
    # storage `relaycli config tier <name> <model>` already writes for the
    # 16-role roster — rather than a second, parallel setting here. See
    # agent/router.py's _tier_model.
    offline: bool = Field(
        default=False,
        description="Refuse every cloud provider; only local (ollama_chat/ollama) models are used.",
    )
    use_9router: bool = Field(
        default=False,
        description="Route provider calls through a local 9Router instance instead of calling providers directly.",
    )
    nine_router_base_url: str = Field(default="http://localhost:20128/v1")

    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "RELAYCLI_OPENAI_API_KEY"))
    anthropic_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "RELAYCLI_ANTHROPIC_API_KEY"))
    gemini_api_key: str | None = Field(default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", "RELAYCLI_GEMINI_API_KEY"))
    groq_api_key: str | None = Field(default=None, validation_alias=AliasChoices("GROQ_API_KEY", "RELAYCLI_GROQ_API_KEY"))
    mistral_api_key: str | None = Field(default=None, validation_alias=AliasChoices("MISTRAL_API_KEY", "RELAYCLI_MISTRAL_API_KEY"))
    openrouter_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY", "RELAYCLI_OPENROUTER_API_KEY"))
    ollama_base_url: str = Field(default="http://localhost:11434", validation_alias=AliasChoices("OLLAMA_BASE_URL", "OLLAMA_API_BASE", "RELAYCLI_OLLAMA_BASE_URL"))

    @field_validator("model")
    @classmethod
    def _model_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "model must not be blank — set one with `relaycli -m <model-id>`, "
                "`relaycli config set-model <role> <model-id>`, or the RELAYCLI_MODEL "
                "environment variable."
            )
        return v

    @field_validator(
        "openai_api_key", "anthropic_api_key", "gemini_api_key",
        "groq_api_key", "mistral_api_key", "openrouter_api_key",
    )
    @classmethod
    def _api_key_looks_plausible(cls, v: str | None, info) -> str | None:
        if not v:  # None or "" both mean "no key configured" — always fine
            return v
        if any(c.isspace() for c in v):
            raise ValueError(
                f"{info.field_name} contains whitespace, which is never valid in a real "
                f"API key — check for a stray newline or copy-paste artifact and re-set it."
            )
        if len(v) < 4:
            raise ValueError(
                f"{info.field_name} is only {len(v)} character(s) long, too short to be a "
                f"real API key — double-check the value, or leave it unset if you don't "
                f"have one yet."
            )
        return v

    def detected_providers(self) -> dict[str, bool]:
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
        return (
            init_settings,
            env_settings,
            _FilteredSource(dotenv_settings, _DOTENV_BLOCKED_FIELDS),
            TomlConfigSettingsSource(
                settings_cls, toml_file=settings_cls.model_config.get("toml_file") or CONFIG_FILE
            ),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
