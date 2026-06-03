"""Config-layer tests: persistence round-trip, model resolution, key masking."""

from __future__ import annotations

import stat

import pytest

from relaycli.appconfig import (
    AppConfig,
    ProviderConfig,
    RoleConfig,
    effective_roles,
    load_app_config,
    mask_key,
    resolve_provider_key,
    resolve_role_model,
    save_app_config,
    set_runtime_option,
)
from relaycli.roles import BUILTIN_ROLES, builtin_role


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "config.toml"


# -- roster ---------------------------------------------------------------
def test_roster_has_sixteen_roles_with_prompts():
    assert len(BUILTIN_ROLES) == 16
    ids = {r.id for r in BUILTIN_ROLES}
    assert {"orchestrator", "planner", "coder", "reviewer", "security"} <= ids
    for r in BUILTIN_ROLES:
        assert r.default_model_tier in ("fast", "balanced", "strong")
        assert r.system_prompt.strip()
        assert "untrusted" in r.system_prompt.lower()  # security block present


# -- persistence round-trip ----------------------------------------------
def test_save_and_load_round_trip(cfg_path):
    cfg = load_app_config(cfg_path)
    cfg.preferences["theme"] = "dark"
    cfg.tiers["strong"] = "claude-3-5-sonnet-latest"
    cfg.roles["reviewer"] = RoleConfig(enabled=True, model="strong")
    cfg.providers["openai"] = ProviderConfig(api_key="env:OPENAI_API_KEY")
    save_app_config(cfg)

    again = load_app_config(cfg_path)
    assert again.preference("theme") == "dark"
    assert again.tier_model("strong") == "claude-3-5-sonnet-latest"
    assert again.role_assignment("reviewer") == "strong"
    assert again.providers["openai"].api_key == "env:OPENAI_API_KEY"


def test_save_is_0600_and_preserves_unknown_keys(cfg_path):
    # A pre-existing flat key (what pydantic Settings reads) must survive a save.
    cfg_path.write_text('model = "openrouter/x"\nrelay_enabled = true\n', encoding="utf-8")
    cfg = load_app_config(cfg_path)
    cfg.tiers["fast"] = "gpt-4o-mini"
    save_app_config(cfg)

    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600
    text = cfg_path.read_text(encoding="utf-8")
    assert 'model = "openrouter/x"' in text
    assert "relay_enabled = true" in text
    assert "[models]" in text
    # and it re-parses cleanly
    assert load_app_config(cfg_path).tier_model("fast") == "gpt-4o-mini"


def test_set_runtime_option_mirrors_preferences_to_flat_settings(cfg_path):
    set_runtime_option("permission_mode", "full-auto", cfg_path)
    set_runtime_option("relay_enabled", True, cfg_path)
    set_runtime_option("max_context_tokens", 64000, cfg_path)

    cfg = load_app_config(cfg_path)
    assert cfg.preference("permission_mode") == "full-auto"
    assert cfg.preference("max_context_tokens") == 64000
    assert cfg._raw["permission_mode"] == "full-auto"
    assert cfg._raw["relay_enabled"] is True
    assert cfg._raw["token_budget"] == 64000


# -- model resolution -----------------------------------------------------
def test_resolve_role_model_via_tier_and_concrete(cfg_path):
    cfg = load_app_config(cfg_path)
    # built-in reviewer defaults to the 'strong' tier
    model, err = resolve_role_model(cfg, "reviewer")
    assert err is None and model == cfg.tier_model("strong")
    # a concrete assignment is used directly
    cfg.roles["reviewer"] = RoleConfig(model="claude-3-5-haiku-latest")
    model, err = resolve_role_model(cfg, "reviewer")
    assert err is None and model == "claude-3-5-haiku-latest"


def test_resolve_role_model_unset_tier_is_clear_error(cfg_path):
    cfg = load_app_config(cfg_path)
    cfg.tiers = {}  # wipe defaults
    cfg.roles["coder"] = RoleConfig(model="strong")
    # tier_model falls back to DEFAULT_TIERS, so force a truly empty tier
    from relaycli import appconfig
    saved = dict(appconfig.DEFAULT_TIERS)
    appconfig.DEFAULT_TIERS.clear()
    try:
        model, err = resolve_role_model(cfg, "coder")
    finally:
        appconfig.DEFAULT_TIERS.update(saved)
    assert model is None and err and "strong" in err


def test_effective_roles_reflects_enable_disable(cfg_path):
    cfg = load_app_config(cfg_path)
    cfg.roles["security"] = RoleConfig(enabled=True)   # off by default
    cfg.roles["planner"] = RoleConfig(enabled=False)   # on by default
    by_id = {r.id: r for r in effective_roles(cfg)}
    assert by_id["security"].enabled is True
    assert by_id["planner"].enabled is False
    assert by_id["coder"].enabled is builtin_role("coder").enabled_by_default
    assert len(by_id) == 16


# -- key masking + resolution --------------------------------------------
def test_mask_key_never_reveals_literal():
    assert mask_key(None) == "not set"
    assert mask_key("env:OPENAI_API_KEY") == "via env (OPENAI_API_KEY)"
    masked = mask_key("sk-verysecretkey1234")
    assert masked == "sk-…1234"
    assert "verysecret" not in masked


def test_resolve_provider_key_env_wins(monkeypatch, cfg_path):
    cfg = load_app_config(cfg_path)
    cfg.providers["openai"] = ProviderConfig(api_key="sk-literal-in-file")
    # no env → literal
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_provider_key(cfg, "openai") == "sk-literal-in-file"
    # env wins for secrets
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert resolve_provider_key(cfg, "openai") == "sk-from-env"


def test_resolve_provider_key_env_reference(monkeypatch, cfg_path):
    cfg = load_app_config(cfg_path)
    cfg.providers["anthropic"] = ProviderConfig(api_key="env:MY_ANT_KEY")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MY_ANT_KEY", "sk-ant-ref")
    assert resolve_provider_key(cfg, "anthropic") == "sk-ant-ref"
