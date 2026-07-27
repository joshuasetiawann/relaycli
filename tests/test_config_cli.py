"""`relaycli config …` subcommand tests — hermetic against a temp config file."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from relaycli import appconfig
from relaycli.config import Settings, get_settings
from relaycli.appconfig import load_app_config
from relaycli.config_cli import config_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _temp_config(monkeypatch, tmp_path):
    # Redirect the config layer at CALL time so no test touches the real file.
    path = tmp_path / "config.toml"
    monkeypatch.setattr(appconfig, "CONFIG_FILE", path)
    monkeypatch.setitem(Settings.model_config, "toml_file", str(path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _cfg():
    return load_app_config()


def test_show_lists_roles_masked():
    r = runner.invoke(config_app, ["show"])
    assert r.exit_code == 0
    assert "orchestrator" in r.output and "reviewer" in r.output
    assert "Model tiers" in r.output and "Providers" in r.output


def test_set_model_persists_across_restarts():
    r = runner.invoke(config_app, ["set-model", "reviewer", "strong"])
    assert r.exit_code == 0
    assert _cfg().role_assignment("reviewer") == "strong"
    # concrete id too
    runner.invoke(config_app, ["set-model", "coder", "claude-3-5-sonnet-latest"])
    assert _cfg().role_assignment("coder") == "claude-3-5-sonnet-latest"


def test_tier_sets_model():
    r = runner.invoke(config_app, ["tier", "strong", "gpt-4o"])
    assert r.exit_code == 0
    assert _cfg().tier_model("strong") == "gpt-4o"
    assert runner.invoke(config_app, ["tier", "nope", "x"]).exit_code == 2


def test_enable_disable_persists():
    runner.invoke(config_app, ["enable", "security"])
    assert _cfg().role_enabled("security") is True
    runner.invoke(config_app, ["disable", "planner"])
    assert _cfg().role_enabled("planner") is False


def test_unknown_role_is_rejected():
    r = runner.invoke(config_app, ["set-model", "wizard", "strong"])
    assert r.exit_code == 2 and "Unknown role" in r.output
    assert runner.invoke(config_app, ["enable", "wizard"]).exit_code == 2


def test_set_key_env_reference_never_echoes_secret():
    r = runner.invoke(config_app, ["set-key", "openai", "--env", "OPENAI_API_KEY"])
    assert r.exit_code == 0
    assert _cfg().providers["openai"].api_key == "env:OPENAI_API_KEY"
    # show displays it masked
    out = runner.invoke(config_app, ["show"]).output
    assert "via env (OPENAI_API_KEY)" in out


def test_set_key_literal_is_masked_and_not_echoed():
    r = runner.invoke(config_app, ["set-key", "anthropic", "--value", "sk-supersecret-123456"])
    assert r.exit_code == 0
    assert "supersecret" not in r.output              # never echoed
    assert "sk-…" in r.output and "3456" in r.output   # masked confirmation (Rich may color parts)
    assert _cfg().providers["anthropic"].api_key == "sk-supersecret-123456"  # stored literal


def test_set_key_unknown_provider_rejected():
    assert runner.invoke(config_app, ["set-key", "acme", "--env", "X"]).exit_code == 2


def test_config_no_subcommand_shows():
    r = runner.invoke(config_app, [])
    assert r.exit_code == 0 and "Roles" in r.output


def test_path_prints_location():
    r = runner.invoke(config_app, ["path"])
    assert r.exit_code == 0 and "config.toml" in r.output


def test_show_escapes_model_markup():
    # A model id containing Rich markup must render literally, not be parsed.
    runner.invoke(config_app, ["set-model", "coder", "[i]evil[/i]"])
    out = runner.invoke(config_app, ["show"]).output
    assert "[i]evil" in out


def test_models_lists_and_filters_catalog():
    r = runner.invoke(config_app, ["models", "--catalog-only", "--search", "qwen"])
    assert r.exit_code == 0
    assert "qwen" in r.output.lower()
    assert "Available models" in r.output


def test_select_model_persists_default_model():
    r = runner.invoke(config_app, ["select-model", "ollama_chat/qwen2.5-coder:0.5b"])
    assert r.exit_code == 0
    assert _cfg()._raw["model"] == "ollama_chat/qwen2.5-coder:0.5b"


def test_choose_model_can_pick_by_number():
    r = runner.invoke(
        config_app,
        ["choose-model", "--catalog-only", "--provider", "ollama"],
        input="1\n",
    )
    assert r.exit_code == 0
    assert _cfg()._raw["model"].startswith("ollama_chat/")


def test_ollama_pull_uses_helper(monkeypatch):
    from relaycli import config_cli

    calls = []
    monkeypatch.setattr(config_cli, "pull_ollama_model", lambda settings, model: calls.append(model) or model)

    r = runner.invoke(config_app, ["ollama-pull", "qwen2.5-coder:0.5b"])

    assert r.exit_code == 0
    assert calls == ["qwen2.5-coder:0.5b"]
    assert "installed" in r.output
