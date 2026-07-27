"""MCP client — stdio JSON-RPC transport for one server process."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from relaycli import __version__

PROTOCOL_VERSION = "2024-11-05"
INIT_TIMEOUT = 60.0
CALL_TIMEOUT = 60.0
OUTPUT_CAP = 24_000


class MCPError(RuntimeError):
    pass


@dataclass
class MCPServerConfig:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


PRESETS: dict[str, dict[str, Any]] = {
    "filesystem": {"command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."], "requires": "npx", "note": "read/write files under the working directory"},
    "fetch": {"command": ["uvx", "mcp-server-fetch"], "requires": "uvx", "note": "fetch web pages as markdown"},
    "github": {"command": ["npx", "-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "env:GITHUB_TOKEN"}, "requires": "npx", "note": "repos, issues, PRs (needs GITHUB_TOKEN)"},
    "postgres": {"command": ["npx", "-y", "@modelcontextprotocol/server-postgres", "env:DATABASE_URL"], "requires": "npx", "note": "read-only SQL against DATABASE_URL"},
    "puppeteer": {"command": ["npx", "-y", "@modelcontextprotocol/server-puppeteer"], "requires": "npx", "note": "drive a real browser"},
}


def _resolve_env_ref(value: str) -> str:
    return os.environ.get(value[4:], "") if value.startswith("env:") else value


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict[str, Any]] = []
        self._next_id = 0
        self._responses: dict[int, dict] = {}
        self._pending: set[int] = set()
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        self._stderr_tail: deque[str] = deque(maxlen=50)
        self._dead_reason: str | None = None

    def start(self) -> None:
        command = [_resolve_env_ref(part) for part in self.config.command]
        env = dict(os.environ)
        for key, value in self.config.env.items():
            env[key] = _resolve_env_ref(value)
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        except OSError as exc:
            raise MCPError(f"could not start '{command[0]}': {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        result = self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "clientInfo": {"name": "relaycli", "version": __version__}}, timeout=INIT_TIMEOUT)
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

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = CALL_TIMEOUT) -> str:
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
            self._pending.add(msg_id)
        try:
            self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            with self._cond:
                ok = self._cond.wait_for(lambda: msg_id in self._responses or self._dead_reason is not None, timeout=timeout)
                if msg_id in self._responses:
                    response = self._responses.pop(msg_id)
                elif self._dead_reason:
                    raise MCPError(self._dead_reason)
                elif not ok:
                    raise MCPError(f"'{method}' timed out after {timeout:.0f}s")
                else:
                    raise MCPError(f"'{method}' failed")
        finally:
            with self._cond:
                self._pending.discard(msg_id)
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
            with self._write_lock:
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"write to server failed: {exc}") from exc

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
                continue
            if not isinstance(msg, dict):
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._cond:
                    if msg["id"] in self._pending:
                        self._responses[msg["id"]] = msg
                    self._cond.notify_all()
            elif "id" in msg and "method" in msg:
                try:
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported by relaycli"}})
                except MCPError:
                    pass
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
