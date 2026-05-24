"""Stage 5 tests: REPL slash-commands + rendering (no network)."""

from __future__ import annotations

import io

from rich.console import Console

from relaycli.config import PermissionMode, Settings
from relaycli.llm import ToolCall, Usage
from relaycli.render import RichReporter, make_unified_diff, render_task_summary
from relaycli.repl import Repl
from relaycli.tools.base import ToolResult


def _repl(mode=PermissionMode.suggest, model="gpt-4o-mini"):
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    settings = Settings(model=model, permission_mode=mode)
    return Repl(settings, console=console), console


def _out(console) -> str:
    return console.file.getvalue()


# --- slash commands ----------------------------------------------------
def test_slash_model_switches_model():
    repl, console = _repl()
    assert repl._handle_slash("/model ollama_chat/llama3.1") is False
    assert repl.settings.model == "ollama_chat/llama3.1"
    assert repl.agent.session.model == "ollama_chat/llama3.1"


def test_slash_mode_switches_and_updates_system_prompt():
    repl, console = _repl()
    repl._handle_slash("/mode full-auto")
    assert repl.settings.permission_mode is PermissionMode.full_auto
    assert repl.permissions.mode is PermissionMode.full_auto
    # The system prompt the agent sends reflects the new mode.
    assert "full-auto" in repl.agent.session.to_messages()[0]["content"]
    # full-auto prints a warning banner.
    assert "full-auto" in _out(console)


def test_slash_mode_invalid_is_rejected():
    repl, console = _repl()
    repl._handle_slash("/mode banana")
    assert repl.settings.permission_mode is PermissionMode.suggest
    assert "Invalid mode" in _out(console)


def test_slash_clear_resets_session():
    repl, _ = _repl()
    repl.agent.session.add_user("hello")
    assert repl.agent.session.messages
    repl._handle_slash("/clear")
    assert repl.agent.session.messages == []


def test_slash_exit_returns_true():
    repl, _ = _repl()
    assert repl._handle_slash("/exit") is True
    assert repl._handle_slash("/quit") is True


def test_slash_unknown_command():
    repl, console = _repl()
    assert repl._handle_slash("/wat") is False
    assert "Unknown command" in _out(console)


def test_slash_help_lists_commands():
    repl, console = _repl()
    repl._handle_slash("/help")
    text = _out(console)
    assert "/model" in text and "/mode" in text and "/diff" in text


def test_slash_diff_no_crash():
    # In this repo .git exists; in a fresh dir it falls back gracefully.
    repl, console = _repl()
    repl._handle_slash("/diff")
    # Either shows a diff or a "no changes"/"not a git repo" note — never raises.
    assert _out(console) != ""


# --- rendering ---------------------------------------------------------
def test_rich_reporter_activity_lines():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    call = ToolCall(id="c1", name="edit_file", arguments="{}")
    reporter.tool_start(call)
    reporter.tool_end(call, ToolResult(ok=True, output="ok", summary="edit app.py (+2 -1)"))
    reporter.tool_end(
        ToolCall(id="c2", name="run_command", arguments="{}"),
        ToolResult(ok=False, output="boom", summary="run pytest → exit 1"),
    )
    out = _out(console)
    assert "edit app.py (+2 -1)" in out
    assert "run pytest → exit 1" in out
    assert reporter.tools_used == ["edit_file", "run_command"]


def test_rich_reporter_streams_text():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    reporter.assistant_token("Hello ")
    reporter.assistant_token("world")
    reporter.assistant_end()
    assert "Hello world" in _out(console)


def test_render_task_summary():
    class _R:
        stopped_reason = "done"
        iterations = 3
        tool_calls = 4
        usage = Usage(total_tokens=123, cost_usd=0.0012)
        elapsed = 2.5

    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_task_summary(console, _R(), ["read_file", "read_file", "edit_file"])
    out = _out(console)
    assert "done" in out and "3 steps" in out and "123 tokens" in out
    assert "read_file×2" in out


def test_make_unified_diff_no_trailing_newline():
    diff = make_unified_diff("a", "b", "f.txt")
    assert "-a" in diff and "+b" in diff
