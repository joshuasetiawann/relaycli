"""Minimal MCP (Model Context Protocol) client — stdio transport.

Connects RelayCLI to external tool servers ("connectors"): filesystem, fetch,
GitHub, Postgres, a browser, or anything else speaking MCP over stdio. No new
dependency and no asyncio — the protocol is JSON-RPC 2.0, one JSON object per
line, which a subprocess pipe plus a reader thread handles fine.

Configuration lives in ``~/.relaycli/config.toml``::

    [mcp.github]
    command = ["npx", "-y", "@modelcontextprotocol/server-github"]
    env = { GITHUB_PERSONAL_ACCESS_TOKEN = "env:GITHUB_TOKEN" }
    enabled = true

``env:`` values (and ``env:`` args in ``command``) resolve against the real
environment at start time, so secrets stay out of the config file. Values are
never logged.

Server tools are registered into the session's ToolRegistry as
``mcp_<server>_<tool>``. Every call is **command-gated** — external side
effects are opaque, so they get the same caution as ``run_command``. Tool
output is untrusted data (the system prompt already says so).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from relaycli import __version__
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

PROTOCOL_VERSION = "2024-11-05"
INIT_TIMEOUT = 60.0    # npx/uvx may download the server on first run
CALL_TIMEOUT = 60.0
OUTPUT_CAP = 24_000    # chars of tool output fed back to the model


class MCPError(RuntimeError):
    """A server failed to start, answer, or behave."""


# ── configuration ─────────────────────────────────────────────────────────
@dataclass
class MCPServerConfig:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


# Well-known connectors: `relaycli mcp add <preset>` scaffolds the config.
# `requires` is the runtime binary the preset needs on PATH.
PRESETS: dict[str, dict[str, Any]] = {
    "filesystem": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."],
        "requires": "npx",
        "note": "read/write files under the working directory",
    },
    "fetch": {
        "command": ["uvx", "mcp-server-fetch"],
        "requires": "uvx",
        "note": "fetch web pages as markdown",
    },
    "github": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "env:GITHUB_TOKEN"},
        "requires": "npx",
        "note": "repos, issues, PRs (needs GITHUB_TOKEN)",
    },
    "postgres": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-postgres", "env:DATABASE_URL"],
        "requires": "npx",
        "note": "read-only SQL against DATABASE_URL",
    },
    "puppeteer": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-puppeteer"],
        "requires": "npx",
        "note": "drive a real browser",
    },
}


def configured_servers(raw: dict | None = None) -> dict[str, MCPServerConfig]:
    """Parse ``[mcp.<name>]`` tables (from ``raw`` or the live config file)."""
    if raw is None:
        from relaycli.appconfig import load_app_config

        raw = load_app_config()._raw
    servers: dict[str, MCPServerConfig] = {}
    for name, tbl in (raw.get("mcp") or {}).items():
        if not isinstance(tbl, dict):
            continue
        command = tbl.get("command")
        if isinstance(command, str):
            import shlex

            command = shlex.split(command)
        if not isinstance(command, list) or not command:
            continue
        env = {k: str(v) for k, v in (tbl.get("env") or {}).items()}
        servers[name] = MCPServerConfig(
            name=name,
            command=[str(c) for c in command],
            env=env,
            enabled=bool(tbl.get("enabled", True)),
        )
    return servers


def enabled_servers() -> dict[str, MCPServerConfig]:
    """The servers a session should attach (tests stub this out)."""
    return {n: s for n, s in configured_servers().items() if s.enabled}


def save_server(name: str, command: list[str], env: dict[str, str] | None = None) -> None:
    """Persist ``[mcp.<name>]`` through the appconfig layer (atomic, 0600)."""
    from relaycli.appconfig import load_app_config, save_app_config

    cfg = load_app_config()
    mcp_tbl = dict(cfg._raw.get("mcp") or {})
    entry: dict[str, Any] = {"command": command, "enabled": True}
    if env:
        entry["env"] = env
    mcp_tbl[name] = entry
    cfg._raw["mcp"] = mcp_tbl
    save_app_config(cfg)


def remove_server(name: str) -> bool:
    from relaycli.appconfig import load_app_config, save_app_config

    cfg = load_app_config()
    mcp_tbl = dict(cfg._raw.get("mcp") or {})
    if name not in mcp_tbl:
        return False
    del mcp_tbl[name]
    cfg._raw["mcp"] = mcp_tbl
    save_app_config(cfg)
    return True


def _resolve_env_ref(value: str) -> str:
    return os.environ.get(value[4:], "") if value.startswith("env:") else value


# ── client ────────────────────────────────────────────────────────────────
class MCPClient:
    """One running MCP server process and its JSON-RPC channel."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict[str, Any]] = []
        self._next_id = 0
        self._responses: dict[int, dict] = {}
        self._cond = threading.Condition()
        self._stderr_tail: deque[str] = deque(maxlen=50)
        self._dead_reason: str | None = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        """Spawn the process and run the MCP handshake + tools/list."""
        command = [_resolve_env_ref(part) for part in self.config.command]
        env = dict(os.environ)
        for key, value in self.config.env.items():
            env[key] = _resolve_env_ref(value)
        try:
            self.proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,  # line-buffered
            )
        except OSError as exc:
            raise MCPError(f"could not start '{command[0]}': {exc}") from exc

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "relaycli", "version": __version__},
            },
            timeout=INIT_TIMEOUT,
        )
        if not isinstance(result, dict):
            raise MCPError("initialize returned no result")
        self._notify("notifications/initialized")
        listed = self._rpc("tools/list", {}, timeout=INIT_TIMEOUT)
        self.tools = list((listed or {}).get("tools") or [])

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        self.proc = None
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and not self._dead_reason

    # -- protocol --------------------------------------------------------
    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = CALL_TIMEOUT) -> str:
        """Invoke one server tool; returns its text output (capped)."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if not isinstance(result, dict):
            raise MCPError("tools/call returned no result")
        parts = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, dict):
                parts.append(f"[{item.get('type', 'non-text')} content]")
        text = "\n".join(parts).strip() or "(no content)"
        if len(text) > OUTPUT_CAP:
            text = text[:OUTPUT_CAP] + f"\n… [truncated at {OUTPUT_CAP} chars]"
        if result.get("isError"):
            raise MCPError(text)
        return text

    def _rpc(self, method: str, params: dict, *, timeout: float) -> Any:
        if self.proc is None or self.proc.poll() is not None:
            raise MCPError(self._dead_reason or "server is not running")
        with self._cond:
            self._next_id += 1
            msg_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        with self._cond:
            ok = self._cond.wait_for(
                lambda: msg_id in self._responses or self._dead_reason is not None,
                timeout=timeout,
            )
            if msg_id in self._responses:
                response = self._responses.pop(msg_id)
            elif self._dead_reason:
                raise MCPError(self._dead_reason)
            elif not ok:
                raise MCPError(f"'{method}' timed out after {timeout:.0f}s")
            else:  # pragma: no cover - defensive
                raise MCPError(f"'{method}' failed")
        if "error" in response:
            err = response["error"] or {}
            raise MCPError(f"{err.get('message', 'server error')} (code {err.get('code')})")
        return response.get("result")

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, msg: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise MCPError("server is not running")
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"write to server failed: {exc}") from exc

    # -- background readers ------------------------------------------------
    def _read_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # not JSON-RPC: some servers log to stdout — skip
            if not isinstance(msg, dict):
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._cond:
                    self._responses[msg["id"]] = msg
                    self._cond.notify_all()
            elif "id" in msg and "method" in msg:
                # Server→client request (sampling etc.) — not supported.
                try:
                    self._send({
                        "jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": "not supported by relaycli"},
                    })
                except MCPError:
                    pass
            # notifications: ignored
        tail = "; ".join(list(self._stderr_tail)[-3:])
        with self._cond:
            self._dead_reason = "server exited" + (f" — {tail}" if tail else "")
            self._cond.notify_all()

    def _read_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self._stderr_tail.append(line.rstrip())


# ── tool bridge ─────────────────────────────────────────────────────────────
class _AnyArgs(BaseModel):
    """Permissive placeholder — real validation is the server's inputSchema."""

    model_config = ConfigDict(extra="allow")


def _sanitize(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
    return cleaned[:64]


@dataclass
class MCPTool(Tool):
    """A server-provided tool exposed through the normal registry."""

    client: MCPClient = None  # type: ignore[assignment]
    server_name: str = ""
    remote_name: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def json_schema(self) -> dict[str, Any]:
        params = self.input_schema or {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }

    def run(self, arguments: str | dict | None, ctx: ToolContext | None = None) -> ToolResult:
        from relaycli.tools import ToolError

        if isinstance(arguments, str):
            try:
                data = json.loads(arguments.strip() or "{}")
            except json.JSONDecodeError as exc:
                raise ToolError(f"Tool '{self.name}' received malformed JSON: {exc}") from exc
        else:
            data = arguments or {}
        if not isinstance(data, dict):
            raise ToolError(f"Tool '{self.name}' expects an object of arguments.")

        label = f"{self.server_name}:{self.remote_name}"
        if ctx is not None:
            compact = json.dumps(data)
            if len(compact) > 120:
                compact = compact[:119] + "…"
            decision = ctx.permissions.confirm(
                "command", prompt_text=f"Call MCP tool {label} {compact}?"
            )
            if not decision.approved:
                return ToolResult.error(
                    f"MCP call {label} was declined.", summary=f"mcp {label} (declined)"
                )

        try:
            output = self.client.call_tool(self.remote_name, data)
        except MCPError as exc:
            return ToolResult.error(f"MCP {label} failed: {exc}", summary=f"mcp {label} (failed)")
        return ToolResult(ok=True, output=output, summary=f"mcp {label}")


# ── session attachment ───────────────────────────────────────────────────────
# One client per configured server per process: the REPL, the relay coder and
# the web session share processes instead of spawning duplicates.
_clients: dict[str, MCPClient] = {}
_clients_lock = threading.Lock()
_atexit_registered = False


def get_client(config: MCPServerConfig) -> MCPClient:
    """The process-wide client for ``config`` (started on first use)."""
    global _atexit_registered
    with _clients_lock:
        client = _clients.get(config.name)
        if client is not None and client.alive:
            return client
        client = MCPClient(config)
        _clients[config.name] = client
        if not _atexit_registered:
            import atexit

            atexit.register(shutdown_all)
            _atexit_registered = True
    client.start()  # outside the lock: npx cold start can take a while
    return client


def shutdown_all() -> None:
    with _clients_lock:
        clients = list(_clients.values())
        _clients.clear()
    for client in clients:
        client.close()


def extend_registry(reg: ToolRegistry, *, console=None) -> ToolRegistry:
    """Append every enabled MCP server's tools to ``reg`` (best effort).

    A server that fails to start is reported (when a console is given) and
    skipped — a broken connector must never take the session down.
    """
    for name, config in enabled_servers().items():
        try:
            client = get_client(config)
        except MCPError as exc:
            if console is not None:
                console.print(f"[yellow]mcp {name}: {exc}[/yellow]")
            continue
        for tool in client.tools:
            remote = str(tool.get("name") or "")
            if not remote:
                continue
            reg.add(MCPTool(
                name=_sanitize(f"mcp_{name}_{remote}"),
                description=(tool.get("description") or f"{name} MCP tool {remote}")[:1000],
                args_model=_AnyArgs,
                func=lambda *_a, **_k: None,  # unused: run() is overridden
                client=client,
                server_name=name,
                remote_name=remote,
                input_schema=dict(tool.get("inputSchema") or {}),
            ))
    return reg


def server_status() -> list[dict[str, Any]]:
    """Status rows for /mcp and the web UI (no secrets)."""
    rows = []
    for name, config in configured_servers().items():
        client = _clients.get(name)
        if not config.enabled:
            state, count = "disabled", 0
        elif client is None:
            state, count = "configured", 0
        elif client.alive:
            state, count = "running", len(client.tools)
        else:
            state, count = "failed", 0
        rows.append({
            "name": name,
            "command": " ".join(config.command),
            "state": state,
            "tools": count,
        })
    return rows
