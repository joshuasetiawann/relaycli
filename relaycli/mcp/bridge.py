"""MCP ↔ tool registry bridge — attach MCP servers to the agent's tools."""

from __future__ import annotations

import json
import os
import shlex
import threading
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from relaycli.mcp.client import MCPClient, MCPError, MCPServerConfig, _resolve_env_ref
from relaycli.tools.registry import Tool, ToolError, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult


class _AnyArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


def _sanitize(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
    return cleaned[:64]


@dataclass
class MCPTool(Tool):
    client: MCPClient = None
    server_name: str = ""
    remote_name: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def json_schema(self) -> dict[str, Any]:
        params = self.input_schema or {"type": "object", "properties": {}}
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": params}}

    def run(self, arguments: str | dict | None, ctx: ToolContext | None = None) -> ToolResult:
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
            from rich.markup import escape as _escape
            compact = json.dumps(data)
            if len(compact) > 120:
                compact = compact[:119] + "…"
            decision = ctx.permissions.confirm("command", prompt_text=f"Call MCP tool {_escape(label)} {_escape(compact)}?")
            if not decision.approved:
                return ToolResult.error(f"MCP call {label} was declined.", summary=f"mcp {label} (declined)")
        try:
            output = self.client.call_tool(self.remote_name, data)
        except MCPError as exc:
            return ToolResult.error(f"MCP {label} failed: {exc}", summary=f"mcp {label} (failed)")
        return ToolResult(ok=True, output=output, summary=f"mcp {label}")


_clients: dict[str, MCPClient] = {}
_start_locks: dict[str, threading.Lock] = {}
_clients_lock = threading.Lock()
_atexit_registered = False


def configured_servers(raw: dict | None = None) -> dict[str, MCPServerConfig]:
    if raw is None:
        from relaycli.config.manager import load_app_config
        raw = load_app_config()._raw
    servers: dict[str, MCPServerConfig] = {}
    for name, tbl in (raw.get("mcp") or {}).items():
        if not isinstance(tbl, dict):
            continue
        command = tbl.get("command")
        if isinstance(command, str):
            command = shlex.split(command)
        if not isinstance(command, list) or not command:
            continue
        env = {k: str(v) for k, v in (tbl.get("env") or {}).items()}
        servers[name] = MCPServerConfig(name=name, command=[str(c) for c in command], env=env, enabled=bool(tbl.get("enabled", True)))
    return servers


def enabled_servers() -> dict[str, MCPServerConfig]:
    return {n: s for n, s in configured_servers().items() if s.enabled}


def save_server(name: str, command: list[str], env: dict[str, str] | None = None) -> None:
    from relaycli.config.manager import load_app_config, save_app_config
    cfg = load_app_config()
    mcp_tbl = dict(cfg._raw.get("mcp") or {})
    entry: dict[str, Any] = {"command": command, "enabled": True}
    if env:
        entry["env"] = env
    mcp_tbl[name] = entry
    cfg._raw["mcp"] = mcp_tbl
    save_app_config(cfg)


def remove_server(name: str) -> bool:
    from relaycli.config.manager import load_app_config, save_app_config
    cfg = load_app_config()
    mcp_tbl = dict(cfg._raw.get("mcp") or {})
    if name not in mcp_tbl:
        return False
    del mcp_tbl[name]
    cfg._raw["mcp"] = mcp_tbl
    save_app_config(cfg)
    return True


def get_client(config: MCPServerConfig) -> MCPClient:
    global _atexit_registered
    with _clients_lock:
        client = _clients.get(config.name)
        if client is not None and client.alive:
            return client
        if not _atexit_registered:
            import atexit
            atexit.register(shutdown_all)
            _atexit_registered = True
        start_lock = _start_locks.setdefault(config.name, threading.Lock())
    with start_lock:
        with _clients_lock:
            client = _clients.get(config.name)
            if client is not None and client.alive:
                return client
        client = MCPClient(config)
        try:
            client.start()
        except MCPError:
            client.close()
            raise
        with _clients_lock:
            _clients[config.name] = client
        return client


def shutdown_all() -> None:
    with _clients_lock:
        clients = list(_clients.values())
        _clients.clear()
    for client in clients:
        client.close()


def extend_registry(reg: ToolRegistry, *, console=None) -> ToolRegistry:
    for name, config in enabled_servers().items():
        try:
            client = get_client(config)
        except MCPError as exc:
            if console is not None:
                from rich.markup import escape
                console.print(f"[yellow]mcp {escape(name)}: {escape(str(exc))}[/yellow]")
            continue
        for tool in client.tools:
            remote = str(tool.get("name") or "")
            if not remote:
                continue
            reg.add(MCPTool(
                name=_sanitize(f"mcp_{name}_{remote}"),
                description=(tool.get("description") or f"{name} MCP tool {remote}")[:1000],
                args_model=_AnyArgs,
                func=lambda *_a, **_k: None,
                client=client, server_name=name, remote_name=remote,
                input_schema=dict(tool.get("inputSchema") or {}),
            ))
    return reg


def server_status() -> list[dict[str, Any]]:
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
        rows.append({"name": name, "command": " ".join(config.command), "state": state, "tools": count})
    return rows
