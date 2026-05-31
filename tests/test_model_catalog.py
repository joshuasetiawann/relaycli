"""Model catalog tests: no real provider or Ollama network calls."""

from __future__ import annotations

import json

import pytest

from relaycli import appconfig
from relaycli.config import Settings
from relaycli import model_catalog


class _Resp:
    def __init__(self, data: dict):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self._data).encode("utf-8")


def _settings(**kw) -> Settings:
    kw.setdefault("model", "fake/model")
    return Settings(_env_file=None, **kw)


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch, tmp_path):
    model_catalog._LIVE_CACHE.clear()
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setitem(Settings.model_config, "toml_file", str(tmp_path / "config.toml"))
    for env in model_catalog.PROVIDER_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(model_catalog, "ollama_models", lambda settings, timeout=0.8: [])


def test_model_choices_include_live_openrouter_models(monkeypatch):
    def fake_urlopen(req, timeout=0.8):
        assert req.full_url == "https://openrouter.ai/api/v1/models"
        assert req.headers["Authorization"] == "Bearer sk-router"
        return _Resp({"data": [{"id": "qwen/qwen3-coder:free"}, {"id": "openai/gpt-4o-mini"}]})

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-router")

    rows = model_catalog.model_choices(
        _settings(),
        provider_filter="openrouter",
    )

    assert [r["id"] for r in rows] == [
        "openrouter/qwen/qwen3-coder:free",
        "openrouter/openai/gpt-4o-mini",
    ]
    assert {r["source"] for r in rows} == {"live"}


def test_model_choices_detect_installed_ollama_models(monkeypatch):
    monkeypatch.setattr(
        model_catalog,
        "ollama_models",
        lambda settings, timeout=0.8: ["qwen2.5-coder:0.5b", "smollm2:360m"],
    )

    rows = model_catalog.model_choices(_settings(), provider_filter="ollama")

    assert [r["id"] for r in rows] == [
        "ollama_chat/qwen2.5-coder:0.5b",
        "ollama_chat/smollm2:360m",
    ]
    assert {r["source"] for r in rows} == {"installed"}


def test_search_filters_catalog_without_network():
    rows = model_catalog.model_choices(
        _settings(),
        query="qwen",
        live=False,
    )

    assert rows
    assert all("qwen" in " ".join(map(str, r.values())).lower() for r in rows)


def test_recent_models_are_listed_first():
    from relaycli.appconfig import set_base_model

    set_base_model("ollama_chat/qwen2.5-coder:0.5b")
    set_base_model("openrouter/qwen/qwen3-coder:free")

    rows = model_catalog.model_choices(_settings(model="openrouter/qwen/qwen3-coder:free"), live=False)

    assert rows[0]["group"] == "Recent"
    assert rows[0]["id"] == "openrouter/qwen/qwen3-coder:free"
    assert rows[1]["group"] == "Recent"
    assert rows[1]["id"] == "ollama_chat/qwen2.5-coder:0.5b"


def test_pull_ollama_model_posts_normalized_name(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=3600):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp({"status": "success"})

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    settings = _settings()
    settings.ollama_base_url = "http://127.0.0.1:11434"

    name = model_catalog.pull_ollama_model(settings, "ollama_chat/qwen2.5-coder:0.5b")

    assert name == "qwen2.5-coder:0.5b"
    assert seen == {
        "url": "http://127.0.0.1:11434/api/pull",
        "body": {"name": "qwen2.5-coder:0.5b", "stream": False},
    }


def test_ollama_model_name_rejects_whitespace():
    with pytest.raises(ValueError):
        model_catalog.normalize_ollama_model_name("qwen 0.5b")
