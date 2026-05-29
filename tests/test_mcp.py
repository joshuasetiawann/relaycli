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
