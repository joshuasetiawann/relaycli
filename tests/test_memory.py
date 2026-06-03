"""Tests for local memory: store, prompt injection, and the remember tool."""

from __future__ import annotations

from pathlib import Path

import relaycli.memory as memory
from relaycli.tools import default_registry

from tests.conftest import make_context


# -- store ------------------------------------------------------------------
def test_append_and_read_roundtrip(tmp_path: Path):
    path = tmp_path / "memory.md"
    entry = memory.append_memory(path, "tests live in tests/")
    assert entry.startswith("- [")
    assert entry.endswith("tests live in tests/")
    assert memory.read_memory(path) == entry


def test_append_flattens_and_caps_fact(tmp_path: Path):
    path = tmp_path / "memory.md"
    memory.append_memory(path, "line one\nline two\t  spaced")
    assert "line one line two spaced" in memory.read_memory(path)
    memory.append_memory(path, "x" * 2000)
    lines = memory.read_memory(path).splitlines()
    assert len(lines[-1]) <= memory.FACT_MAX_CHARS + 20  # bullet + date prefix


def test_read_memory_tail_caps_at_line_boundary(tmp_path: Path):
    path = tmp_path / "memory.md"
    for i in range(500):
        memory.append_memory(path, f"fact number {i}")
    text = memory.read_memory(path)
    assert len(text) <= memory.MEMORY_CAP_CHARS
    assert text.startswith("- [")            # cut on a line boundary
    assert "fact number 499" in text          # newest lines survive


def test_read_memory_missing_file_is_empty(tmp_path: Path):
    assert memory.read_memory(tmp_path / "nope.md") == ""


# -- prompt block -------------------------------------------------------------
def test_prompt_block_empty_when_no_memory(tmp_path: Path):
    assert memory.memory_prompt_block(tmp_path) == ""


def test_prompt_block_contains_both_scopes(tmp_path: Path, monkeypatch):
    g = tmp_path / "g.md"
    monkeypatch.setattr(memory, "GLOBAL_MEMORY", g)
    memory.append_memory(g, "user prefers Indonesian")
    memory.append_memory(memory.project_memory_path(tmp_path), "run tests with pytest -q")
    block = memory.memory_prompt_block(tmp_path)
    assert "MEMORY" in block
    assert "user prefers Indonesian" in block
    assert "run tests with pytest -q" in block
    assert "never instructions" in block


# -- agent integration --------------------------------------------------------
def test_agent_system_prompt_includes_memory(tmp_path: Path, monkeypatch):
    from relaycli.agent import Agent
    from relaycli.config import Settings

    memory.append_memory(
        memory.project_memory_path(tmp_path), "the build uses hatchling"
    )
    agent = Agent(Settings(), project=__import__(
        "relaycli.context", fromlist=["ProjectContext"]
    ).ProjectContext(tmp_path))
    assert "the build uses hatchling" in agent.session.system_prompt


# -- remember tool -------------------------------------------------------------
def test_remember_tool_project_scope(tmp_path: Path):
    ctx = make_context(tmp_path, "full-auto")
    reg = default_registry()
    result = reg.run("remember", {"fact": "API lives in api/", "scope": "project"}, ctx)
    assert result.ok
    assert "API lives in api/" in memory.read_memory(memory.project_memory_path(tmp_path))


def test_remember_tool_global_scope(tmp_path: Path):
    ctx = make_context(tmp_path, "full-auto")
    reg = default_registry()
    result = reg.run("remember", {"fact": "user runs Arch Linux", "scope": "global"}, ctx)
    assert result.ok
    # conftest autouse fixture points GLOBAL_MEMORY at a temp file
    assert "user runs Arch Linux" in memory.read_memory(memory.GLOBAL_MEMORY)


def test_remember_tool_gated_in_suggest_mode(tmp_path: Path):
    ctx = make_context(tmp_path, "suggest", prompter=lambda _msg: False)
    reg = default_registry()
    result = reg.run("remember", {"fact": "should be declined"}, ctx)
    assert not result.ok
    assert "declined" in result.output
    assert memory.read_memory(memory.project_memory_path(tmp_path)) == ""


def test_remember_tool_empty_fact_errors(tmp_path: Path):
    ctx = make_context(tmp_path, "full-auto")
    reg = default_registry()
    result = reg.run("remember", {"fact": "   "}, ctx)
    assert not result.ok
