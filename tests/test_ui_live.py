"""Tests for relaycli/ui/live.py — the --experimental-parallel live view.

Exercises the pure/independently-testable pieces (refusal detection,
view-model building, frame rendering) against a real Scheduler running
fake, LLM-free tasks — the same pattern test_agent_scheduler.py uses.
run_parallel_with_view / _run_with_live_frame / _run_with_progress_lines
call orchestrator.run_parallel(), which needs a real or heavily-mocked LLM
stack; test_agent_orchestrator.py deliberately doesn't go there either, so
this file doesn't either.
"""

from __future__ import annotations

import asyncio
import io
import time

import pytest
from rich.console import Console
from rich.text import Text

from relaycli.agent.graph import Task, TaskGraph
from relaycli.agent.scheduler import Scheduler, TaskOutcome
from relaycli.core.config import PermissionMode, Settings
from relaycli.core.llm import Usage
from relaycli.ui.live import (
    LiveFrame,
    _progress_line,
    lane_views_for,
    live_view_supported,
    narrow_terminal_refusal,
    render_frame_lines,
    render_status_bar,
)


def _graph(*tasks: Task) -> TaskGraph:
    return TaskGraph.from_tasks(list(tasks))


def _console(*, width=120) -> Console:
    return Console(file=io.StringIO(), width=width, force_terminal=True)


# --- narrow_terminal_refusal / live_view_supported --------------------------
def test_narrow_terminal_refusal_none_at_80_and_above():
    assert narrow_terminal_refusal(_console(width=80)) is None
    assert narrow_terminal_refusal(_console(width=120)) is None


def test_narrow_terminal_refusal_exact_message_below_80():
    assert narrow_terminal_refusal(_console(width=64)) == "relaycli needs 80 columns (have 64)"


def test_live_view_requires_a_real_terminal():
    settings = Settings(permission_mode=PermissionMode.full_auto)
    # force_terminal=False is an explicit override — needed because this
    # environment sets FORCE_COLOR, which otherwise makes Rich's own
    # auto-detection report is_terminal=True even for a StringIO file.
    non_tty = Console(file=io.StringIO(), width=120, force_terminal=False)
    assert live_view_supported(non_tty, settings) is False


def test_live_view_requires_full_auto_permission_mode():
    console = _console(width=120)
    for mode in (PermissionMode.suggest, PermissionMode.auto_edit):
        assert live_view_supported(console, Settings(permission_mode=mode)) is False
    assert live_view_supported(console, Settings(permission_mode=PermissionMode.full_auto)) is True


def test_live_view_requires_80_columns():
    settings = Settings(permission_mode=PermissionMode.full_auto)
    assert live_view_supported(_console(width=64), settings) is False


def test_live_view_respects_no_motion(monkeypatch):
    monkeypatch.setenv("NO_MOTION", "1")
    settings = Settings(permission_mode=PermissionMode.full_auto)
    assert live_view_supported(_console(width=120), settings) is False


# --- lane_views_for / render_status_bar / render_frame_lines ----------------
def test_lane_views_for_reflects_a_running_task_mid_run():
    holder: dict = {}
    seen = []

    def on_tick():
        sched = holder["scheduler"]
        views = lane_views_for(sched)
        if any(v.status == "running" for v in views):
            seen.append(views)

    async def run_task(task):
        await asyncio.to_thread(time.sleep, 0.05)
        return TaskOutcome(task_id=task.id, ok=True, summary="done", usage=Usage(total_tokens=42))

    graph = _graph(Task(id="a", role_id="backend", goal="build the api"))
    sched = Scheduler(graph, run_task, on_tick=on_tick, should_stop_poll_interval=0.01)
    holder["scheduler"] = sched
    asyncio.run(sched.run())

    assert seen, "should have observed 'a' running before it completed"
    running_view = seen[0][0]
    assert running_view.task_id == "a"
    assert running_view.role_id == "backend"
    assert running_view.elapsed_s >= 0
    # Usage isn't known until completion — a running task must not fabricate cost/tokens.
    assert running_view.tokens == 0
    assert running_view.cost_usd == 0.0


def test_lane_views_for_reflects_completed_usage():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done", usage=Usage(total_tokens=99, cost_usd=0.05))

    graph = _graph(Task(id="a", role_id="backend", goal="x"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    views = lane_views_for(sched)
    assert views[0].status == "done"
    assert views[0].tokens == 99
    assert views[0].cost_usd == 0.05
    assert views[0].elapsed_s >= 0


def test_render_status_bar_shows_progress_and_spend():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done", usage=Usage(total_tokens=10, cost_usd=0.01))

    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    sched = Scheduler(graph, run_task, max_concurrent_agents=1)
    asyncio.run(sched.run())

    bar = render_status_bar(sched, "no_color")
    assert "2/2 done" in bar.plain
    assert "20 tokens" in bar.plain
    assert "$0.0200" in bar.plain


@pytest.mark.parametrize("mode", ["dark", "light", "no_color"])
def test_render_frame_lines_one_row_per_lane_plus_status_bar(mode):
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done")

    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    lines = render_frame_lines(sched, _console(), mode)
    assert len(lines) == 1 + 2  # status bar + 2 lanes
    assert all(isinstance(line, Text) for line in lines)
    assert all("\n" not in line.plain for line in lines)


def test_live_frame_rich_console_protocol_yields_the_same_lines():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done")

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    console = _console()
    frame = LiveFrame(sched, "dark")
    rendered = list(frame.__rich_console__(console, console.options))
    assert len(rendered) == 2  # status bar + 1 lane


# --- progress line formatting ------------------------------------------------
@pytest.mark.parametrize("mode", ["dark", "no_color"])
def test_progress_line_includes_task_id_and_role_code(mode):
    line = _progress_line("auth-task", "backend", "running", mode)
    assert "auth-task" in line.plain
    assert "bnd" in line.plain


def test_progress_line_no_color_uses_spelled_out_word():
    line = _progress_line("t1", "coder", "blocked", "no_color")
    assert "WAIT" in line.plain


def test_progress_line_handles_unknown_role_gracefully():
    line = _progress_line("t1", "some-future-role", "done", "dark")
    assert "t1" in line.plain


# --- Live's context manager composes correctly with a fast failure ---------
def test_live_frame_cleans_up_and_propagates_graph_error(monkeypatch):
    """No real orchestrator/LLM call — this only proves that when
    run_parallel raises before ever calling on_scheduler_ready (e.g. a bad
    Orchestrator plan → GraphError), the `with Live(...) as live:` block
    in _run_with_live_frame still tears down cleanly and re-raises rather
    than hanging or swallowing the error."""
    import relaycli.agent.orchestrator as orchestrator_mod
    from relaycli.agent.graph import GraphError
    from relaycli.ui.live import _run_with_live_frame

    async def fake_run_parallel(*args, **kwargs):
        raise GraphError("orchestrator produced unparseable output")

    monkeypatch.setattr(orchestrator_mod, "run_parallel", fake_run_parallel)

    async def main():
        with pytest.raises(GraphError):
            await _run_with_live_frame(
                Settings(permission_mode=PermissionMode.full_auto), "do something",
                console=_console(), project=None, permissions=None,
            )

    asyncio.run(main())
