"""Roster↔relay bridge tests: template shape, model resolution order, enabled set."""

from __future__ import annotations

import pytest

from relaycli import appconfig
from relaycli.appconfig import RoleConfig, load_app_config
from relaycli.config import PermissionMode, Settings
from relaycli.roster import (
    enabled_specialists,
    is_assignable,
    roster_template,
    specialist_model,
    specialist_runtime,
)


@pytest.fixture(autouse=True)
def _temp_config(monkeypatch, tmp_path):
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "config.toml")


def _settings(**kw) -> Settings:
    kw.setdefault("model", "base/model")
    kw.setdefault("permission_mode", PermissionMode.suggest)
    return Settings(_env_file=None, **kw)


def test_roster_template_has_header_placeholders_and_body():
    t = roster_template("backend")
    # the Agent fills these — they must survive verbatim
    for ph in ("{cwd}", "{mode}", "{mode_desc}", "{tool_list}"):
        assert ph in t
    assert "Backend" in t and "server" in t.lower()
    assert "untrusted" in t.lower()
    # brace-free body: .format() with the Agent's kwargs must not raise
    t.format(cwd="/x", mode="suggest", mode_desc="d", tool_list="- read_file")


def test_roster_template_unknown_role_generic():
    t = roster_template("customrole")
    assert "customrole" in t and "{tool_list}" in t


def test_specialist_model_resolution_order():
    cfg = load_app_config()
    # roster: with no explicit assignment a role uses its default tier
    cfg.tiers["strong"] = "qwen/coder"          # backend defaults to 'strong'
    assert specialist_model(_settings(), cfg, "backend") == "qwen/coder"
    # an explicit concrete assignment is used directly
    cfg.roles["backend"] = RoleConfig(model="concrete/backend-model")
    assert specialist_model(_settings(), cfg, "backend") == "concrete/backend-model"
    # a Settings <role>_model override wins over the roster (coder has a field)
    s = _settings(coder_model="override/coder")
    cfg.roles["coder"] = RoleConfig(model="from/roster")
    assert specialist_model(s, cfg, "coder") == "override/coder"


def test_specialist_model_base_fallback_when_tier_unset():
    cfg = load_app_config()
    cfg.tiers = {}
    saved = dict(appconfig.DEFAULT_TIERS)
    appconfig.DEFAULT_TIERS.clear()
    try:
        # no tier resolvable anywhere → the base model
        assert specialist_model(_settings(), cfg, "backend") == "base/model"
    finally:
        appconfig.DEFAULT_TIERS.update(saved)


def test_enabled_specialists_excludes_pipeline_roles():
    cfg = load_app_config()
    specs = enabled_specialists(cfg)
    assert "coder" in specs                      # enabled implementer
    assert "planner" not in specs and "reviewer" not in specs   # pipeline roles
    # a disabled implementer drops out; enabling one adds it
    cfg.roles["backend"] = RoleConfig(enabled=True)
    assert "backend" in enabled_specialists(cfg)
    cfg.roles["coder"] = RoleConfig(enabled=False)
    assert "coder" not in enabled_specialists(cfg)


def test_specialist_runtime_bundles_prompt_and_model():
    cfg = load_app_config()
    cfg.roles["security"] = RoleConfig(model="audit/model")
    rt = specialist_runtime(_settings(), cfg, "security")
    assert rt.role_id == "security" and rt.display_name == "Security"
    assert rt.model == "audit/model" and "{tool_list}" in rt.template


def test_is_assignable():
    cfg = load_app_config()
    cfg.roles["frontend"] = RoleConfig(enabled=True)
    assert is_assignable(cfg, "frontend") is True
    assert is_assignable(cfg, "architect") is False   # disabled by default
    assert is_assignable(cfg, "nonexistent") is False
