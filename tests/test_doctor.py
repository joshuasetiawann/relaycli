"""Doctor checks: hermetic, no network (probers injected)."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

import relaycli.doctor as doctor
from relaycli.config import Settings
from relaycli.doctor import (
    Check,
    check_config_perms,
    check_installation,
    check_key_drift,
    check_ollama,
    check_openrouter_key,
    check_writable_dirs,
    render_checks,
)


def _console():
    return Console(file=io.StringIO(), force_terminal=False, width=120)


def test_config_perms_flags_world_readable(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("x = 1\n")
    cfg.chmod(0o644)
    checks = check_config_perms(cfg, tmp_path)
    assert checks[0].status == doctor.FAIL
    cfg.chmod(0o600)
    tmp_path.chmod(0o700)
    checks = check_config_perms(cfg, tmp_path)
    assert [c.status for c in checks] == [doctor.OK, doctor.OK]


def test_config_missing_is_warn(tmp_path: Path):
    checks = check_config_perms(tmp_path / "none.toml", tmp_path)
    assert checks[0].status == doctor.WARN


def test_installation_check_verifies_bundled_assets_and_tools():
    checks = check_installation()
    by_name = {check.name: check for check in checks}

    assert by_name["builtin skills"].status == doctor.OK
    assert "bundled" in by_name["builtin skills"].detail
    assert by_name["core tools"].status == doctor.OK
    assert "registered" in by_name["core tools"].detail
    assert by_name["desktop ui"].status == doctor.OK


def test_openrouter_key_probes():
    s = Settings(OPENROUTER_API_KEY="sk-or-live")
    ok = check_openrouter_key(s, prober=lambda k: (200, "my-key"))
    assert ok.status == doctor.OK
    dead = check_openrouter_key(s, prober=lambda k: (401, ""))
    assert dead.status == doctor.FAIL
    assert "set-key openrouter" in dead.detail
    # empty string (not None: ambient config.toml/.env would refill None)
    assert check_openrouter_key(Settings(OPENROUTER_API_KEY="")).status == doctor.SKIP


def test_openrouter_key_never_leaks_key():
    s = Settings(OPENROUTER_API_KEY="sk-or-supersecret123456")
    for prober_result in [(200, ""), (401, ""), (500, "")]:
        check = check_openrouter_key(s, prober=lambda k: prober_result)
        assert "supersecret" not in check.detail


def test_key_drift_detects_mismatch(tmp_path: Path, monkeypatch):
    import relaycli.doctor as d

    cfg = tmp_path / "config.toml"
    cfg.write_text('OPENROUTER_API_KEY = "sk-or-old"\n')
    monkeypatch.setattr(d, "CONFIG_FILE", cfg)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("OPENROUTER_API_KEY=sk-or-new\n")
    check = check_key_drift(Settings(), proj)
    assert check.status == doctor.WARN
    assert "DIFFERENT" in check.detail

    (proj / ".env").write_text("OPENROUTER_API_KEY=sk-or-old\n")
    assert check_key_drift(Settings(), proj).status == doctor.OK

    assert check_key_drift(Settings(), tmp_path / "empty").status == doctor.SKIP


def test_writable_dirs(tmp_path: Path):
    checks = check_writable_dirs(tmp_path)
    assert all(c.status == doctor.OK for c in checks)


def test_render_checks_exit_codes():
    console = _console()
    assert render_checks(console, [Check("a", doctor.OK)]) == 0
    assert render_checks(console, [Check("a", doctor.WARN)]) == 0
    assert render_checks(console, [Check("a", doctor.FAIL, "boom")]) == 1
    out = console.file.getvalue()
    assert "failed" in out and "all good" in out


def test_openrouter_key_probe_non_json_response_does_not_crash():
    """A 200 with a non-JSON/undecodable body must SKIP, not raise."""
    s = Settings(OPENROUTER_API_KEY="sk-or-live")

    def bad_prober(key):
        import json
        json.loads("not json")  # raises JSONDecodeError (a ValueError)

    check = check_openrouter_key(s, prober=bad_prober)
    assert check.status == doctor.SKIP


def test_ollama_check_detects_models(monkeypatch):
    import relaycli.llm as llm

    monkeypatch.setattr(llm, "ollama_models", lambda settings: ["qwen2.5-coder:7b", "llama3.1:8b"])
    check = check_ollama(Settings())
    assert check.status == doctor.OK
    assert "llama3.1:8b" in check.detail


def test_ollama_check_skips_when_unreachable(monkeypatch):
    import relaycli.llm as llm

    monkeypatch.setattr(llm, "ollama_models", lambda settings: [])
    check = check_ollama(Settings())
    assert check.status == doctor.SKIP
    assert "ollama serve" in check.detail
