"""Stage 1 smoke tests: the package imports and config actually works."""

from __future__ import annotations

import importlib
from pathlib import Path
import tomllib

import pytest


def test_package_imports():
    pkg = importlib.import_module("relaycli")
    assert pkg.__version__


def test_project_metadata_version_matches_runtime():
    pkg = importlib.import_module("relaycli")
    root = Path(__file__).resolve().parents[1]
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert meta["project"]["version"] == pkg.__version__


@pytest.mark.parametrize(
    "modname",
    [
        "relaycli.cli",
        "relaycli.config",
        "relaycli.llm",
        "relaycli.agent",
        "relaycli.repl",
        "relaycli.session",
        "relaycli.render",
        "relaycli.context",
        "relaycli.permissions",
        "relaycli.tools",
    ],
)
def test_all_modules_importable(modname):
    assert importlib.import_module(modname) is not None


def test_settings_defaults(monkeypatch):
    # Isolate from the developer's real environment.
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                "GROQ_API_KEY", "MISTRAL_API_KEY", "RELAYCLI_MODEL",
                "RELAYCLI_PERMISSION_MODE"):
        monkeypatch.delenv(key, raising=False)

    from relaycli.config import PermissionMode, Settings

    settings = Settings()
    assert settings.model
    assert settings.permission_mode is PermissionMode.suggest
    assert settings.max_iterations >= 1
    providers = settings.detected_providers()
    assert providers["ollama"] is True
    assert providers["openai"] is False


def test_provider_detection_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    from relaycli.config import Settings

    settings = Settings()
    assert settings.openai_api_key == "sk-test-123"
    assert settings.detected_providers()["openai"] is True
