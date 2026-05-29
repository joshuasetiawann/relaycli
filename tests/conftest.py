"""Shared pytest fixtures for RelayCLI tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from relaycli.config import PermissionMode
from relaycli.context import ProjectContext
from relaycli.permissions import PermissionManager
from relaycli.tools.base import ToolContext


@pytest.fixture(autouse=True)
def _no_ambient_mcp_servers(monkeypatch):
    """Never let a developer's real [mcp] config spawn server processes in
    tests. MCP tests build their own MCPServerConfig / stub this back."""
    import relaycli.mcp as mcp

    monkeypatch.setattr(mcp, "enabled_servers", lambda: {})
    yield


@pytest.fixture(autouse=True)
def _hermetic_global_memory(tmp_path_factory, monkeypatch):
    """Point global memory at a per-test temp file so the developer's real
    ~/.relaycli/memory.md never leaks into test prompts (and tests never
    write to it)."""
    import relaycli.memory as memory

    fake = tmp_path_factory.mktemp("memory") / "memory.md"
    monkeypatch.setattr(memory, "GLOBAL_MEMORY", fake)
    yield


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """A small sample project tree used by the tool tests."""
    (tmp_path / "app.py").write_text(
        "def hello():\n    return 'hi'\n\n# TODO: write tests\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Sample\n\nTODO: write docs\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_SECRET=topsecret-do-not-leak\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("API_SECRET=\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("build/\nignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("should be ignored\n", encoding="utf-8")
    build = tmp_path / "build"
    build.mkdir()
    (build / "out.txt").write_text("artifact\n", encoding="utf-8")
    (tmp_path / "binary.dat").write_bytes(b"\x00\x01\x02RELAY\x00\xff")
    return tmp_path


def make_context(
    root: Path,
    mode: PermissionMode | str = PermissionMode.suggest,
    *,
    prompter=None,
) -> ToolContext:
    """Build a ToolContext with a captured (non-interactive) console."""
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    return ToolContext(
        project=ProjectContext(root),
        permissions=PermissionManager(mode, prompter=prompter, console=console),
        console=console,
    )


def console_text(ctx: ToolContext) -> str:
    """Return everything written to the captured console so far."""
    return ctx.console.file.getvalue()  # type: ignore[attr-defined]


@pytest.fixture
def make_ctx():
    """Expose make_context as a fixture for parametrized use."""
    return make_context
