"""MCP client + tool-bridge tests, driven by the fake stdio server."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import relaycli.mcp as mcp
from relaycli.mcp import (
    MCPClient,
    MCPError,
    MCPServerConfig,
    _sanitize,
    configured_servers,
    extend_registry,
)
from relaycli.tools import default_registry

from tests.conftest import make_context

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def fake_config(name: str = "fake", env: dict | None = None) -> MCPServerConfig:
    return MCPServerConfig(
        name=name, command=[sys.executable, FAKE_SERVER], env=env or {}
    )


@pytest.fixture
def client():
    c = MCPClient(fake_config())
    c.start()
    yield c
    c.close()


# ── protocol ────────────────────────────────────────────────────────────────
def test_handshake_lists_tools(client):
    names = [t["name"] for t in client.tools]
    assert "echo" in names and "boom" in names
    assert client.alive


def test_call_tool_returns_text(client):
    assert client.call_tool("echo", {"text": "hai"}) == "echo: hai"


def test_call_tool_iserror_raises(client):
    with pytest.raises(MCPError, match="kaboom"):
        client.call_tool("boom", {})


def test_call_tool_unknown_raises_rpc_error(client):
    with pytest.raises(MCPError, match="unknown tool"):
        client.call_tool("nope", {})


def test_call_tool_timeout(client):
    with pytest.raises(MCPError, match="timed out"):
        client.call_tool("slow", {}, timeout=0.5)


def test_env_reference_resolution(monkeypatch):
    monkeypatch.setenv("REAL_SECRET", "s3cret-value")
    c = MCPClient(fake_config(env={"FAKE_MCP_SECRET": "env:REAL_SECRET"}))
    c.start()
    try:
        assert c.call_tool("env_probe", {}) == "secret=s3cret-value"
    finally:
        c.close()


def test_dead_server_raises_cleanly():
    config = MCPServerConfig(name="dead", command=[sys.executable, "-c", "import sys; sys.exit(3)"])
    c = MCPClient(config)
    with pytest.raises(MCPError):
        c.start()
    c.close()


def test_missing_binary_raises():
    c = MCPClient(MCPServerConfig(name="x", command=["definitely-not-a-real-binary-xyz"]))
    with pytest.raises(MCPError, match="could not start"):
        c.start()


# ── config parsing ───────────────────────────────────────────────────────────
def test_configured_servers_parses_tables():
    raw = {
        "mcp": {
            "gh": {"command": ["npx", "-y", "server-github"], "env": {"T": "env:GH"}},
            "str": {"command": "uvx mcp-server-fetch"},
            "off": {"command": ["x"], "enabled": False},
            "junk": {"command": 42},
            "empty": {},
        }
    }
    servers = configured_servers(raw)
    assert set(servers) == {"gh", "str", "off"}
    assert servers["str"].command == ["uvx", "mcp-server-fetch"]
    assert servers["gh"].env == {"T": "env:GH"}
    assert servers["off"].enabled is False


def test_sanitize_tool_names():
    assert _sanitize("mcp_gh_create.issue") == "mcp_gh_create_issue"
    assert len(_sanitize("x" * 100)) == 64


# ── registry bridge ───────────────────────────────────────────────────────────
def test_extend_registry_adds_gated_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp, "enabled_servers", lambda: {"fake": fake_config()})
    reg = extend_registry(default_registry())
    try:
        assert "mcp_fake_echo" in reg.names()
        schema = reg.get("mcp_fake_echo").json_schema()
        assert schema["function"]["parameters"]["required"] == ["text"]

        # full-auto: flows straight through to the server
        ctx = make_context(tmp_path, "full-auto")
        result = reg.run("mcp_fake_echo", {"text": "ok"}, ctx)
        assert result.ok and result.output == "echo: ok"

        # suggest: the call is command-gated and can be declined
        ctx = make_context(tmp_path, "suggest", prompter=lambda _m: False)
        result = reg.run("mcp_fake_echo", {"text": "no"}, ctx)
        assert not result.ok and "declined" in result.output

        # auto-edit still asks for MCP (command-class, not edit-class)
        asked = []
        ctx = make_context(tmp_path, "auto-edit", prompter=lambda m: asked.append(m) or True)
        result = reg.run("mcp_fake_echo", {"text": "ya"}, ctx)
        assert result.ok and asked
    finally:
        mcp.shutdown_all()


def test_extend_registry_survives_broken_server(monkeypatch):
    broken = MCPServerConfig(name="broken", command=["no-such-binary-zzz"])
    monkeypatch.setattr(mcp, "enabled_servers", lambda: {"broken": broken})
    reg = extend_registry(default_registry())
    assert "read_file" in reg.names()  # native tools intact
    assert not [n for n in reg.names() if n.startswith("mcp_")]


def test_mcp_tool_error_result_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp, "enabled_servers", lambda: {"fake": fake_config()})
    reg = extend_registry(default_registry())
    try:
        ctx = make_context(tmp_path, "full-auto")
        result = reg.run("mcp_fake_boom", {}, ctx)
        assert not result.ok
        assert "kaboom" in result.output
    finally:
        mcp.shutdown_all()


def test_save_and_remove_server_roundtrip(tmp_path, monkeypatch):
    import relaycli.appconfig as appconfig

    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "config.toml")
    mcp.save_server("gh", ["npx", "-y", "server-github"], {"TOKEN": "env:GH_TOKEN"})
    from relaycli.appconfig import load_app_config

    servers = configured_servers(load_app_config()._raw)
    assert servers["gh"].command == ["npx", "-y", "server-github"]
    assert servers["gh"].env == {"TOKEN": "env:GH_TOKEN"}

    assert mcp.remove_server("gh") is True
    assert "gh" not in configured_servers(load_app_config()._raw)
    assert mcp.remove_server("gh") is False


# ── fixes: escaping, races, response leaks ──────────────────────────────────
def test_mcp_confirm_prompt_escapes_adversarial_tool_name(monkeypatch, tmp_path):
    """A malicious tool name/args must not inject Rich markup into the
    confirmation prompt (mcp.py MCPTool.run)."""
    evil = MCPServerConfig(name="fake", command=[sys.executable, FAKE_SERVER])
    monkeypatch.setattr(mcp, "enabled_servers", lambda: {"fake": evil})
    reg = extend_registry(default_registry())
    try:
        tool = reg.get("mcp_fake_echo")
        tool.remote_name = "echo[/][bold red]INJECTED[/bold red]"
        seen = {}
        ctx = make_context(
            tmp_path, "suggest",
            prompter=lambda msg: seen.setdefault("prompt", msg) or False,
        )
        reg.run("mcp_fake_echo", {"text": "hi"}, ctx)
        assert "[/]" not in seen["prompt"] or "\\[/]" in seen["prompt"]
        assert "INJECTED" in seen["prompt"]  # text survives, just de-fanged
    finally:
        mcp.shutdown_all()


def test_extend_registry_error_print_escapes_stderr(monkeypatch):
    """A broken server's stderr must not be parsed as Rich markup when
    extend_registry reports the failure."""
    import io as _io
    from rich.console import Console

    broken = MCPServerConfig(
        name="x[/]evil", command=[sys.executable, "-c", "import sys; sys.exit(3)"]
    )
    monkeypatch.setattr(mcp, "enabled_servers", lambda: {"x": broken})
    console = Console(file=_io.StringIO(), force_terminal=False, width=200)
    reg = extend_registry(default_registry(), console=console)  # must not raise
    assert not [n for n in reg.names() if n.startswith("mcp_")]


def test_get_client_serializes_concurrent_starts(monkeypatch):
    """Two threads racing get_client() for the same server must produce
    exactly one process, not two (and no leaked orphan)."""
    import threading as _th

    config = fake_config("race")
    results = []

    def worker():
        results.append(mcp.get_client(config))

    try:
        threads = [_th.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(results) == 5
        assert len({id(c) for c in results}) == 1  # same client object
        assert results[0].alive
    finally:
        mcp.shutdown_all()


def test_get_client_does_not_cache_failed_start():
    """A server whose handshake fails must not be cached as 'alive' —
    the next call should retry cleanly instead of returning a wedged client."""
    bad = MCPServerConfig(name="badhandshake", command=[
        sys.executable, "-c",
        "import sys, time; sys.stderr.write('boom\\n'); time.sleep(30)",
    ])
    with pytest.raises(MCPError):
        mcp.get_client(bad)
    assert "badhandshake" not in mcp._clients
    with pytest.raises(MCPError):
        mcp.get_client(bad)  # retries cleanly, doesn't hang forever
    mcp.shutdown_all()


def test_rpc_timeout_does_not_leak_late_response(client):
    """A response that arrives after its RPC timed out must be dropped by
    _read_stdout, not accumulate forever in _responses."""
    with pytest.raises(MCPError, match="timed out"):
        client.call_tool("slow", {}, timeout=0.3)
    assert len(client._pending) == 0
    # the real reply for the abandoned call arrives ~30s later in the fake
    # server; we don't wait for it here, but a fresh in-time call must still
    # work correctly on the same shared client/connection.
    assert client.call_tool("echo", {"text": "still alive"}) == "echo: still alive"
