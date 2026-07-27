"""First-run init planning tests."""

from __future__ import annotations

import relaycli.appconfig as appconfig
import relaycli.onboarding as onboarding
from relaycli.config import Settings
from relaycli.onboarding import build_plan, normalize_services, run_init


def test_build_plan_prefers_ollama(monkeypatch):
    monkeypatch.setattr(onboarding, "best_ollama_model", lambda settings: "ollama_chat/llama3.1:8b")
    plan = build_plan(Settings(), model="auto")
    assert plan.model == "ollama_chat/llama3.1:8b"


def test_normalize_services_rejects_unknown():
    assert normalize_services("ollama, web,ollama") == ["ollama", "web"]
    try:
        normalize_services("redis")
    except Exception as exc:
        assert "unknown service" in str(exc)
    else:
        raise AssertionError("expected unknown service rejection")


def test_run_init_writes_flat_model(monkeypatch, tmp_path):
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(onboarding, "best_ollama_model", lambda settings: "ollama_chat/llama3.1:8b")
    monkeypatch.setattr(onboarding, "ollama_models", lambda settings: ["llama3.1:8b"])

    run_init(model="auto", yes=True, console=None)

    text = (tmp_path / "config.toml").read_text()
    assert 'model = "ollama_chat/llama3.1:8b"' in text
    assert 'permission_mode = "suggest"' in text
