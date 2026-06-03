"""Interactive Configuration / Settings menu tests — handlers + separation."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from relaycli import appconfig
from relaycli.appconfig import load_app_config
from relaycli.config_menu import ConfigMenu, SettingsMenu


@pytest.fixture(autouse=True)
def _temp_config(monkeypatch, tmp_path):
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "config.toml")


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def _out(c: Console) -> str:
    return c.file.getvalue()


# ── Configuration ───────────────────────────────────────────────────────
def test_config_menu_enables_and_assigns_persistently():
    menu = ConfigMenu(load_app_config())
    msg, done = menu.handle("enable security")
    assert not done and "enabled" in msg
    menu.handle("model reviewer strong")
    menu.handle("tier strong claude-3-5-sonnet-latest")
    # persisted to disk
    cfg = load_app_config()
    assert cfg.role_enabled("security") is True
    assert cfg.role_assignment("reviewer") == "strong"
    assert cfg.tier_model("strong") == "claude-3-5-sonnet-latest"


def test_config_menu_sets_provider_key_masked():
    menu = ConfigMenu(load_app_config())
    msg, _ = menu.handle("key openai env:OPENAI_API_KEY")
    assert "via env" in msg
    msg2, _ = menu.handle("key anthropic sk-secretvalue-9999")
    assert "secretvalue" not in msg2 and "sk-…" in msg2  # never echoed raw
    assert load_app_config().providers["anthropic"].api_key == "sk-secretvalue-9999"


def test_config_menu_switches_sections_and_validates():
    menu = ConfigMenu(load_app_config())
    menu.handle("providers")
    assert menu.section == "providers"
    menu.handle("roles")
    assert menu.section == "roles"
    assert "unknown role" in menu.handle("enable wizard")[0]
    assert "unknown tier" in menu.handle("tier turbo x")[0]
    assert "unknown provider" in menu.handle("key acme x")[0]


def test_config_menu_quit():
    assert ConfigMenu(load_app_config()).handle("q")[1] is True


def test_config_render_lists_roles_and_providers():
    menu = ConfigMenu(load_app_config())
    c = _console(); menu.render(c)
    assert "Roles & Models" in _out(c) and "orchestrator" in _out(c)
    menu.section = "providers"
    c2 = _console(); menu.render(c2)
    assert "Providers & Keys" in _out(c2) and "openrouter" in _out(c2)


# ── Settings (preferences only) ─────────────────────────────────────────
def test_settings_menu_sets_preferences():
    menu = SettingsMenu(load_app_config())
    assert "permission_mode" in menu.handle("mode full-auto")[0]
    menu.handle("theme light")
    menu.handle("context 64000")
    cfg = load_app_config()
    assert cfg.preference("permission_mode") == "full-auto"
    assert cfg.preference("theme") == "light"
    assert cfg.preference("max_context_tokens") == 64000


def test_settings_menu_rejects_bad_input():
    menu = SettingsMenu(load_app_config())
    assert "invalid mode" in menu.handle("mode banana")[0]
    assert "usage" in menu.handle("context notanumber")[0]


def test_settings_screen_touches_only_preferences():
    # The Settings screen must NOT expose or change roles/models/keys.
    menu = SettingsMenu(load_app_config())
    menu.handle("mode suggest")
    cfg = load_app_config()
    assert cfg.roles == {} and cfg.providers == {} and cfg.tiers == {}
    # render shows preferences, never a roles/providers table
    c = _console(); menu.render(c)
    text = _out(c)
    assert "permission_mode" in text
    assert "Roles" not in text and "Providers" not in text


def test_configuration_screen_never_shows_settings_prefs():
    # Strict separation the other way: Configuration must not render prefs.
    c = _console(); ConfigMenu(load_app_config()).render(c)
    text = _out(c)
    assert "permission_mode" not in text and "theme" not in text
