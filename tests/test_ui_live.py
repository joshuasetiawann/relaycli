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
import json
import time

import pytest
from rich.console import Console
from rich.text import Text

from relaycli.agent.graph import Task, TaskGraph
from relaycli.agent.scheduler import Scheduler, TaskOutcome
from relaycli.core.config import PermissionMode, Settings
from relaycli.core.llm import Usage
from relaycli.core.llm import ToolCall
from relaycli.ui.frame import render_status_bar
from relaycli.ui.layout import resolve_columns
from relaycli.ui.live import (
    LiveFrame,
    _progress_line,
    lane_views_for,
    live_view_supported,
    narrow_terminal_refusal,
    render_frame_lines,
    status_bar_data,
)


def _bar(sched, mode="no_color", width=120, **kwargs):
    """The status bar as the frame draws it — data assembled from the real
    Scheduler, then rendered by the pure renderer."""
    return render_status_bar(status_bar_data(sched, **kwargs), resolve_columns(width),
                             mode, width)


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

    bar = _bar(sched).plain
    assert "2a" in bar or "2 agent" in bar
    assert "$0.02" in bar


def test_status_bar_never_overflows_the_terminal():
    """A bar one column too wide wraps, and the wrap pushes every row of
    the frame down by one on each repaint."""
    from pathlib import Path as _Path

    from relaycli.ui.frame import StatusBarData

    data = StatusBarData(cwd=_Path("/very/deeply/nested/project/directory/name/here"),
                         branch="feature/an-extremely-long-branch-name-that-will-not-fit",
                         dirty=17, permission_mode="auto-edit", agents=6,
                         tokens=402_700, spent_usd=2.71, limit_usd=3.0)
    for width in (80, 100, 120, 200):
        bar = render_status_bar(data, resolve_columns(width), "dark", width)
        assert bar.cell_len <= width, f"status bar overflowed at {width} columns"


def test_status_bar_keeps_the_spend_when_it_has_to_drop_something():
    """What gives way is the path and the mode — things you can recover by
    looking elsewhere. The spend is not one of them."""
    from pathlib import Path as _Path

    from relaycli.ui.frame import StatusBarData

    data = StatusBarData(cwd=_Path("/" + "x" * 300), branch="b" * 80, dirty=3,
                         permission_mode="auto-edit", agents=4, tokens=1000,
                         spent_usd=1.87, limit_usd=3.0)
    bar = render_status_bar(data, resolve_columns(120), "dark", 120).plain
    assert "$1.87" in bar
    assert "4 agents" in bar


@pytest.mark.parametrize("mode", ["dark", "light", "no_color"])
def test_render_frame_lines_one_row_per_lane_plus_status_bar(mode):
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done")

    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    lines = render_frame_lines(sched, _console(), mode)
    body = "\n".join(line.plain for line in lines)
    assert all(isinstance(line, Text) for line in lines)
    assert all("\n" not in line.plain for line in lines)
    # Status bar, rule, one row per lane, rule, input caret, key strip.
    assert len(lines) == 2 + 2 + 3
    assert "❯" in body or ">" in body


def test_frame_never_exceeds_the_terminal_height():
    """Every row past the last line scrolls the frame, and a frame that
    scrolls is not a pinned frame."""
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done")

    graph = _graph(*[Task(id=f"t{i}", role_id="coder", goal="x") for i in range(9)])
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    for height in (12, 24, 40):
        console = Console(file=io.StringIO(), width=120, height=height, force_terminal=True)
        lines = render_frame_lines(sched, console, "dark")
        assert len(lines) <= height, f"frame was {len(lines)} rows in a {height}-row terminal"


def test_every_frame_row_fits_the_terminal_width():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done")

    graph = _graph(Task(id="a", role_id="coder", goal="x" * 200))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    for width in (80, 120, 200):
        console = Console(file=io.StringIO(), width=width, height=24, force_terminal=True)
        for line in render_frame_lines(sched, console, "dark"):
            assert line.cell_len <= width, f"{line.plain!r} overflowed {width} columns"


def test_the_input_and_key_strip_stay_on_the_last_rows():
    """Pinned means pinned: two transcript entries or two hundred, the
    strip is still the bottom line."""
    from relaycli.ui.live import LaneActivity

    sched = _settled_scheduler("a")
    activity = LaneActivity()
    console = Console(file=io.StringIO(), width=120, height=24, force_terminal=True)

    def bottom():
        lines = render_frame_lines(sched, console, "dark", None, activity)
        return len(lines), lines[-1].plain

    quiet_len, quiet_last = bottom()
    reporter = activity.reporter_for("a", "coder")
    for i in range(60):
        reporter.tool_start(ToolCall(id=str(i), name="read", arguments='{"path": "x.py"}'))
    busy_len, busy_last = bottom()

    assert quiet_len == busy_len
    assert quiet_last == busy_last
    assert "tab" in busy_last


def test_live_frame_rich_console_protocol_yields_the_same_lines():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done")

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    console = _console()
    live_frame = LiveFrame(sched, "dark")
    rendered = list(live_frame.__rich_console__(console, console.options))
    assert len(rendered) == len(render_frame_lines(sched, console, "dark"))


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


# --- key-map view state (Stage 4 remainder: focus mode + key overlay) --------
def _settled_scheduler(*ids: str) -> Scheduler:
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done")

    graph = _graph(*[Task(id=i, role_id="coder", goal=f"goal {i}") for i in ids])
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())
    return sched


def test_lane_views_mark_the_selected_lane_focused():
    sched = _settled_scheduler("a", "b", "c")
    lanes = lane_views_for(sched, selected=1)
    assert [lane.focused for lane in lanes] == [False, True, False]


def test_lane_views_focus_nothing_when_no_selection_is_given():
    """The progress-lines path and any other non-interactive caller must
    render exactly as they did before the key map existed."""
    assert not any(lane.focused for lane in lane_views_for(_settled_scheduler("a", "b")))


def test_focused_lane_row_draws_the_focus_rail():
    from relaycli.ui import theme
    from relaycli.ui.layout import resolve_columns
    from relaycli.ui.lanes import render_lane_row

    lane = lane_views_for(_settled_scheduler("a", "b"), selected=0)[0]
    row = render_lane_row(lane, resolve_columns(120), "dark")
    # §4 gives the row a one-column left gutter; the rail is the first
    # thing after it.
    assert row.plain.lstrip(" ").startswith(theme.MARKERS["focus_rail"].symbol)


def test_help_overlay_replaces_the_lane_list():
    """§4's row budget has no room for both on a 24-row terminal."""
    from relaycli.ui import keymap

    sched = _settled_scheduler("a", "b", "c")
    lines = render_frame_lines(sched, _console(), "dark", keymap.ViewState(show_help=True))
    body = "\n".join(line.plain for line in lines)
    assert "tab" in body and "esc" in body
    for task_id in ("a", "b", "c"):
        assert f" {task_id} " not in body, "lane rows should be hidden behind the overlay"


def test_collapsed_lane_list_leaves_only_the_status_bar():
    from relaycli.ui import keymap

    sched = _settled_scheduler("a", "b", "c")
    lines = render_frame_lines(sched, _console(), "dark", keymap.ViewState(lane_list_collapsed=True))
    body = "\n".join(line.plain for line in lines)
    for task_id in ("a", "b", "c"):
        assert f" {task_id} " not in body
    assert "3 agents" in lines[0].plain


def test_live_frame_reads_view_state_fresh_on_every_frame():
    """The key reader replaces the ViewState between frames; a LiveFrame
    that captured one instant's state would pin the cursor in place."""
    from relaycli.ui import keymap

    sched = _settled_scheduler("a", "b")
    box = {"state": keymap.ViewState(selected=0)}
    frame = LiveFrame(sched, "dark", lambda: box["state"])
    console = _console()

    first = "\n".join(t.plain for t in frame.__rich_console__(console, console.options))
    box["state"] = keymap.ViewState(selected=1)
    second = "\n".join(t.plain for t in frame.__rich_console__(console, console.options))
    assert first != second, "the frame ignored the updated ViewState"


def test_grouping_never_folds_away_the_focused_lane():
    """Above the grouping threshold, settled lanes collapse into count
    rows — but the cursor must never point at a row that isn't drawn."""
    from relaycli.ui.lanes import LaneView, group_for_display
    from relaycli.ui.layout import LANE_LIST_MAX_ROWS

    sched = _settled_scheduler("a", "b", "c", "d", "e", "f", "g")
    lanes = lane_views_for(sched, selected=6)   # every lane is 'done'
    displayed = group_for_display(lanes, max_rows=LANE_LIST_MAX_ROWS)
    expanded = [l.task_id for l in displayed if isinstance(l, LaneView)]
    assert "g" in expanded


# --- lane actions dispatched to a real Scheduler (Stage 4: task steering) ---
def test_dispatch_drop_cancels_the_task_under_the_cursor():
    from relaycli.ui.live import dispatch_lane_action

    sched = _settled_scheduler("a", "b", "c")   # graph order == cursor order
    assert dispatch_lane_action(sched, "drop_task", 1) == "b"
    assert sched._cancel_requests == {"b"}


def test_dispatch_retry_targets_the_task_under_the_cursor():
    from relaycli.ui.live import dispatch_lane_action

    sched = _settled_scheduler("a", "b", "c")
    assert dispatch_lane_action(sched, "retry_task", 2) == "c"
    assert sched._retry_requests == {"c"}


def test_dispatch_is_a_no_op_before_the_graph_exists():
    """Keys can be pressed between the run starting and the orchestrator
    returning a graph; there is no scheduler to address yet."""
    from relaycli.ui.live import dispatch_lane_action

    assert dispatch_lane_action(None, "drop_task", 0) is None


def test_dispatch_ignores_a_cursor_past_the_end_of_the_graph():
    from relaycli.ui.live import dispatch_lane_action

    sched = _settled_scheduler("a")
    assert dispatch_lane_action(sched, "drop_task", 7) is None
    assert sched._cancel_requests == set()


def test_dispatch_ignores_non_lane_actions():
    """Navigation keys must not reach the Scheduler at all."""
    from relaycli.ui.live import dispatch_lane_action

    sched = _settled_scheduler("a", "b")
    for action in ("next_lane", "focus", "back", "toggle_help", "none"):
        assert dispatch_lane_action(sched, action, 0) is None
    assert sched._cancel_requests == set() and sched._retry_requests == set()


def test_cursor_order_matches_the_rendered_lane_order():
    """dispatch indexes graph order; lane_views_for renders graph order.
    If these ever diverge, `x` would drop a lane other than the highlighted
    one — silent and destructive."""
    from relaycli.ui.live import dispatch_lane_action

    sched = _settled_scheduler("zebra", "alpha", "middle")
    lanes = lane_views_for(sched, selected=1)
    highlighted = next(l.task_id for l in lanes if l.focused)
    assert dispatch_lane_action(sched, "drop_task", 1) == highlighted


# --- LaneActivity: filling the lane list's long-empty tool/target column ----
def _Call(name, arguments=None):
    """A real ToolCall, not a stand-in.

    The fake this replaces stored `arguments` as a dict. The real
    ToolCall stores the model's raw JSON *string*, so the production
    reader — which asked whether arguments was a mapping — got "" for
    every call ever made and the target half of the tool column never
    rendered. The fake agreed with the code and disagreed with reality,
    which is the one thing a fake must never do.
    """
    return ToolCall(id="c1", name=name, arguments=json.dumps(arguments or {}))


class _Result:
    """Stands in for tools.base.ToolResult, which the reporter only ever
    reads `ok` and `summary` off."""

    def __init__(self, *, ok: bool, summary: str) -> None:
        self.ok = ok
        self.summary = summary


def test_lane_activity_tracks_the_open_tool_and_clears_it_on_completion():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    assert activity.current("t1") == ("", "")

    reporter.tool_start(_Call("edit_file", {"path": "src/app.py"}))
    assert activity.current("t1") == ("edit_file", "src/app.py")

    reporter.tool_end(_Call("edit_file"), None)
    assert activity.current("t1") == ("", ""), "a finished call must not linger in the column"


def test_lane_activity_keeps_tasks_apart():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    activity.reporter_for("t1", "backend").tool_start(_Call("edit_file", {"path": "a.py"}))
    activity.reporter_for("t2", "tester").tool_start(_Call("run_command", {"command": "pytest"}))
    assert activity.current("t1") == ("edit_file", "a.py")
    assert activity.current("t2") == ("run_command", "pytest")


def test_lane_activity_target_falls_back_across_argument_names():
    """Tools name their subject differently; the column should show
    something useful without this knowing every tool."""
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    for args, expected in [
        ({"path": "p.py"}, "p.py"),
        ({"file_path": "f.py"}, "f.py"),
        ({"pattern": "TODO"}, "TODO"),
        ({"command": "pytest -q"}, "pytest -q"),
        ({"unrecognised": "x"}, ""),
        ({}, ""),
    ]:
        reporter.tool_start(_Call("some_tool", args))
        assert activity.current("t1")[1] == expected


def test_lane_views_carry_the_current_tool_into_the_row():
    from relaycli.ui.live import LaneActivity

    sched = _settled_scheduler("a", "b")
    activity = LaneActivity()
    activity.reporter_for("a", "coder").tool_start(_Call("write_file", {"path": "out.py"}))

    lanes = lane_views_for(sched, activity=activity)
    by_id = {l.task_id: l for l in lanes}
    assert (by_id["a"].tool, by_id["a"].target) == ("write_file", "out.py")
    assert (by_id["b"].tool, by_id["b"].target) == ("", "")


def test_lane_views_without_activity_leave_the_column_empty():
    """Every existing caller passes no activity and must be unaffected."""
    assert all(l.tool == "" and l.target == "" for l in lane_views_for(_settled_scheduler("a")))


def test_frame_renders_the_tool_target_column():
    from relaycli.ui.live import LaneActivity

    sched = _settled_scheduler("a")
    activity = LaneActivity()
    activity.reporter_for("a", "coder").tool_start(_Call("edit_file", {"path": "deep/nested/mod.py"}))

    lines = render_frame_lines(sched, _console(), "dark", None, activity)
    body = "\n".join(line.plain for line in lines)
    assert "edit_file" in body
    # §4 truncation: directories are dropped before the column is clipped
    assert "mod.py" in body


def test_lane_activity_clears_when_an_agent_dies_inside_a_tool_call():
    """tool_end never fires if the agent raises mid-call, which left the
    lane advertising a command that had stopped running. make_run_task
    closes every reporter in a finally, so close() is the hook that covers
    the crash path — and the base Reporter has no close(), so it has to be
    declared explicitly or the hasattr guard skips it."""
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    reporter.tool_start(_Call("run_command", {"command": "pytest"}))
    assert activity.current("t1") == ("run_command", "pytest")

    assert hasattr(reporter, "close"), "make_run_task only calls close() if it exists"
    reporter.close()
    assert activity.current("t1") == ("", "")


def test_make_run_task_clears_lane_activity_on_a_crashing_agent():
    """End to end through the real make_run_task finally, not just the
    reporter in isolation."""
    from relaycli.agent.orchestrator import make_run_task
    from relaycli.agent.graph import Task
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()

    class _Boom:
        def run(self, goal, *, reporter=None):
            reporter.tool_start(_Call("edit_file", {"path": "a.py"}))
            raise RuntimeError("agent exploded mid-tool")

    class _Ctx:
        read_files = set()

    run_task = make_run_task(
        lambda role_id, task_id: (_Boom(), _Ctx()),
        activity.reporter_for,
    )
    with pytest.raises(RuntimeError, match="exploded"):
        asyncio.run(run_task(Task(id="t1", role_id="backend", goal="x")))

    assert activity.current("t1") == ("", ""), "a dead agent left its tool on the lane"


# --- the transcript: what the agents actually said and ran ------------------
def test_a_tool_call_and_its_result_land_on_one_line():
    """The design writes `read src/lease/queue.ts · 240 lines`, not two
    rows for one call."""
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    call = _Call("read", {"path": "src/lease/queue.ts"})
    reporter.tool_start(call)
    reporter.tool_end(call, _Result(ok=True, summary="240 lines"))

    entries = activity.transcript.entries()
    assert len(entries) == 1
    assert entries[0].tool == "read"
    assert entries[0].target == "src/lease/queue.ts"
    assert entries[0].text == "240 lines"


def test_a_long_result_earns_its_own_row():
    from relaycli.ui.live import RESULT_INLINE_MAX, LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    call = _Call("bash", {"command": "pytest"})
    reporter.tool_start(call)
    reporter.tool_end(call, _Result(ok=False, summary="x" * (RESULT_INLINE_MAX + 1)))

    kinds = [e.kind for e in activity.transcript.entries()]
    assert kinds == ["tool", "result"]


def test_a_tool_that_blew_up_is_still_recorded():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    call = _Call("edit", {"path": "a.py"})
    reporter.tool_start(call)
    reporter.tool_end(call, None)

    entry = activity.transcript.entries()[0]
    assert entry.text == "tool error"
    assert entry.ok is False


def test_assistant_prose_is_recorded_once_the_message_ends():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    for token in ("The existing queue ", "is LIFO."):
        reporter.assistant_token(token)
    assert activity.transcript.entries() == [], "a half-streamed message is not a line yet"

    reporter.assistant_end()
    assert activity.transcript.entries()[0].text == "The existing queue is LIFO."


def test_a_discarded_message_never_reaches_the_transcript():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    reporter.assistant_token("half a thought")
    reporter.assistant_discard()
    reporter.assistant_end()
    assert activity.transcript.entries() == []


def test_the_transcript_can_be_read_per_agent_or_merged():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    activity.reporter_for("t1", "backend").tool_start(_Call("read", {"path": "a.py"}))
    activity.reporter_for("t2", "tester").tool_start(_Call("bash", {"command": "pytest"}))

    assert len(activity.transcript.entries()) == 2
    assert [e.task_id for e in activity.transcript.entries("t2")] == ["t2"]


def test_the_transcript_is_bounded():
    """An hour-long run must not grow the log without limit."""
    from relaycli.ui.live import TranscriptLog
    from relaycli.ui.frame import TranscriptEntry

    log = TranscriptLog(limit=10)
    for i in range(100):
        log.append(TranscriptEntry(stamp="00:00:00", kind="text", text=str(i)))
    entries = log.entries()
    assert len(entries) == 10
    assert entries[-1].text == "99", "the newest lines are the ones kept"


def test_entries_are_copies_the_renderer_cannot_have_rewritten_underneath_it():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    call = _Call("read", {"path": "a.py"})
    reporter.tool_start(call)
    snapshot = activity.transcript.entries()
    reporter.tool_end(call, _Result(ok=True, summary="12 lines"))

    assert snapshot[0].text == "", "the renderer's copy changed after the fact"
    assert activity.transcript.entries()[0].text == "12 lines"


# --- the model column, which was also always empty --------------------------
def test_the_model_column_is_filled_from_what_the_agent_actually_ran():
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    activity.reporter_for("t1", "backend").model_start(1, "sonnet-4.5")
    assert activity.model("t1") == ("sonnet-4.5", False)


def test_escalation_is_observed_not_guessed():
    """§2's ▲ claims the router moved this task up a tier. It is set only
    because two different model names were actually seen."""
    from relaycli.ui.live import LaneActivity

    activity = LaneActivity()
    reporter = activity.reporter_for("t1", "backend")
    reporter.model_start(1, "llama3.1-8b")
    assert activity.model("t1")[1] is False
    reporter.model_start(2, "sonnet-4.5")
    assert activity.model("t1") == ("sonnet-4.5", True)


def test_an_unstarted_task_claims_no_model():
    from relaycli.ui.live import LaneActivity

    assert LaneActivity().model("never-ran") == ("", False)


# --- the target half of the tool column, against the real ToolCall ----------
def test_the_target_survives_a_real_tool_call():
    """The regression this guards: ToolCall.arguments is the model's raw
    JSON string, and reading it as a mapping returned "" for every call
    ever made."""
    from relaycli.ui.live import _target_of

    assert _target_of(ToolCall(id="1", name="edit",
                               arguments='{"path": "src/lease/queue.ts"}')) == "src/lease/queue.ts"


def test_malformed_tool_arguments_do_not_take_the_frame_down():
    from relaycli.ui.live import _target_of

    assert _target_of(ToolCall(id="1", name="edit", arguments="{not json")) == ""
    assert _target_of(ToolCall(id="1", name="edit", arguments="")) == ""
    assert _target_of(ToolCall(id="1", name="edit", arguments="[1, 2]")) == ""


# --- the row budget, which the lease sub-row can quietly blow -------------
def _lease_contended_scheduler(*task_ids, holder="not-a-lane"):
    """Every task claims the same path, and the lease is held by something
    outside the lane list, so *every* lane is blocked on it — the worst
    case for sub-rows. Holding it with one of the lanes instead would
    leave one lane un-blocked and quietly weaken the row-budget test by
    exactly the one row that makes it fail."""
    graph = _graph(*[Task(id=t, role_id="backend", goal=f"goal {t}",
                          path_claims=("src/shared.py",)) for t in task_ids])
    sched = Scheduler(graph, None)
    sched.leases.acquire(holder, ("src/shared.py",))
    sched.task_started_at[holder] = time.perf_counter() - 10
    return sched


def test_lease_sub_rows_cannot_push_the_lane_list_past_its_ceiling():
    """§4 caps the lane region at nine rows. Five lease-blocked lanes each
    bring a `└─ held by` line, which is ten — the ceiling is a row count,
    not a lane count."""
    from relaycli.ui.layout import LANE_LIST_MAX_ROWS
    from relaycli.ui.live import _lane_rows
    from relaycli.ui.layout import resolve_columns as _cols

    sched = _lease_contended_scheduler("a", "b", "c", "d", "e")
    lanes = lane_views_for(sched)
    # All five, not four: with only four sub-rows the list lands on exactly
    # nine rows and passes whether or not anything enforces the ceiling.
    assert all(lane.lease_holder for lane in lanes), "test needs every lane blocked"
    rows = _lane_rows(lanes, _cols(120), "dark", 120)
    assert len(rows) <= LANE_LIST_MAX_ROWS


def test_the_lane_list_gives_way_before_the_transcript_floor():
    """§4: "the lane list collapses to a strip first". Nine lanes plus the
    chrome would leave nothing to read, so the lanes are what shrink."""
    from relaycli.ui.layout import MIN_TRANSCRIPT_ROWS
    from relaycli.ui.live import LaneActivity

    sched = _lease_contended_scheduler(*[f"t{i}" for i in range(9)])
    console = Console(file=io.StringIO(), width=120, height=16, force_terminal=True)
    rows = render_frame_lines(sched, console, "dark", None, LaneActivity())
    body = [r.plain for r in rows]
    header = next(i for i, line in enumerate(body) if "transcript" in line)
    trailing_rules = 3          # rule + caret + key strip
    assert len(body) - header - 1 - trailing_rules >= MIN_TRANSCRIPT_ROWS


@pytest.mark.parametrize("height", [3, 5, 8, 12, 16, 24, 40])
@pytest.mark.parametrize("width", [80, 120])
def test_the_frame_fits_whatever_terminal_it_is_given(height, width):
    from relaycli.ui import keymap
    from relaycli.ui.live import LaneActivity

    sched = _lease_contended_scheduler("a", "b", "c", "d", "e", "f")
    activity = LaneActivity()
    console = Console(file=io.StringIO(), width=width, height=height, force_terminal=True)
    for state in (keymap.ViewState(), keymap.ViewState(merged=True),
                  keymap.ViewState(lane_list_collapsed=True)):
        rows = render_frame_lines(sched, console, "dark", state, activity)
        assert len(rows) <= height
        assert all(row.cell_len <= width for row in rows)


def test_the_key_strip_is_the_last_row_even_when_the_lanes_overflow():
    from relaycli.ui.live import LaneActivity

    sched = _lease_contended_scheduler(*[f"t{i}" for i in range(12)])
    console = Console(file=io.StringIO(), width=120, height=14, force_terminal=True)
    rows = render_frame_lines(sched, console, "dark", None, LaneActivity())
    assert "?" in rows[-1].plain


def test_a_terminal_too_short_for_the_chrome_keeps_the_bottom_not_the_middle():
    """What you can scroll back to is the middle; the caret and the spend
    are the two things that have to stay on screen."""
    from relaycli.ui.live import LaneActivity

    sched = _lease_contended_scheduler("a", "b", "c")
    console = Console(file=io.StringIO(), width=120, height=4, force_terminal=True)
    rows = render_frame_lines(sched, console, "dark", None, LaneActivity())
    assert len(rows) <= 4
    assert "relaycli" in rows[0].plain


# --- the spinner reaches the lane it is supposed to animate ---------------
def test_running_lanes_animate_and_settled_ones_do_not():
    from relaycli.ui import theme
    from relaycli.ui.layout import resolve_columns as _cols
    from relaycli.ui.lanes import LaneView, render_lane_row

    running = LaneView(task_id="a1", role_id="backend", status="running", goal="x")
    done = LaneView(task_id="a2", role_id="backend", status="done", goal="x")
    frame_char = theme.SPINNER_FRAMES[3]
    assert frame_char in render_lane_row(running, _cols(120), "dark", None, frame_char).plain
    assert frame_char not in render_lane_row(done, _cols(120), "dark", None, frame_char).plain


def test_reduced_motion_keeps_the_static_glyph():
    from relaycli.ui import theme
    from relaycli.ui.layout import resolve_columns as _cols
    from relaycli.ui.lanes import LaneView, render_lane_row

    running = LaneView(task_id="a1", role_id="backend", status="running", goal="x")
    row = render_lane_row(running, _cols(120), "dark", None, None).plain
    assert theme.TASK_STATE_GLYPHS["running"].symbol in row


def test_no_motion_stops_the_frame_animating(monkeypatch):
    from relaycli.ui import theme
    from relaycli.ui.live import LaneActivity

    sched = _lease_contended_scheduler("a", "b")
    console = Console(file=io.StringIO(), width=120, height=24, force_terminal=True)
    monkeypatch.setenv("NO_MOTION", "1")
    body = "\n".join(r.plain for r in render_frame_lines(sched, console, "dark",
                                                         None, LaneActivity()))
    assert not any(f in body for f in theme.SPINNER_FRAMES)


def test_the_lane_region_stays_bounded_even_on_a_tall_terminal():
    """§4/§8: bounded matters as much as pinned. A tall window has room
    for eighteen lane rows; the design gives them nine and the rest to the
    transcript, because an unbounded pinned region is just a second scroll
    region."""
    from relaycli.ui.layout import LANE_LIST_MAX_ROWS
    from relaycli.ui.live import LaneActivity

    sched = _lease_contended_scheduler(*[f"t{i}" for i in range(12)])
    console = Console(file=io.StringIO(), width=120, height=40, force_terminal=True)
    body = [r.plain for r in render_frame_lines(sched, console, "dark", None, LaneActivity())]
    first_rule = next(i for i, line in enumerate(body) if set(line.strip()) == {"─"})
    header = next(i for i, line in enumerate(body) if "transcript" in line)
    assert header - first_rule - 1 <= LANE_LIST_MAX_ROWS


# --- s (steer): dispatch, feedback, and the field the frame draws ----------
def _steerable_scheduler(*ids: str, running: set[str] | None = None) -> Scheduler:
    """A settled graph plus a steer sink that only accepts the ids in
    `running` — the real sink's behaviour, where a task that has already
    returned has no agent left to hand a note to."""
    accepted = running if running is not None else set(ids)
    sched = _settled_scheduler(*ids)
    sched.steered = []

    def sink(task_id, note):
        if task_id not in accepted:
            return False
        sched.steered.append((task_id, note))
        return True

    sched._steer = sink
    return sched


def test_steer_goes_to_the_lane_under_the_cursor():
    from relaycli.ui.live import dispatch_steer

    sched = _steerable_scheduler("a", "b", "c")
    assert dispatch_steer(sched, "also add a test", 1) == ("b", True)
    assert sched.steered == [("b", "also add a test")]


def test_steer_reports_a_lane_that_is_no_longer_running():
    """The note was typed while the lane was alive and arrived after it
    finished. Saying it landed would be a lie the user acts on."""
    from relaycli.ui.live import dispatch_steer

    sched = _steerable_scheduler("a", "b", running={"a"})
    assert dispatch_steer(sched, "hello", 1) == ("b", False)
    assert sched.steered == []


def test_steer_before_the_graph_exists_addresses_nothing():
    from relaycli.ui.live import dispatch_steer

    assert dispatch_steer(None, "hello", 0) == ("", False)


def test_steer_ignores_a_cursor_past_the_end_of_the_graph():
    from relaycli.ui.live import dispatch_steer

    assert dispatch_steer(_steerable_scheduler("a"), "hello", 7) == ("", False)


def test_steer_targets_the_same_lane_the_frame_highlights():
    """dispatch indexes graph order and lane_views_for renders graph
    order. If they diverged, a note would go to a lane other than the
    highlighted one — silent, and impossible to notice from the frame."""
    from relaycli.ui.live import dispatch_steer

    sched = _steerable_scheduler("zebra", "alpha", "middle")
    highlighted = next(l.task_id for l in lane_views_for(sched, selected=2) if l.focused)
    assert dispatch_steer(sched, "note", 2)[0] == highlighted


def test_a_delivered_steer_is_recorded_in_the_transcript():
    """A steer produces no visible change until the agent's next
    iteration. Without a line saying it was taken, the only feedback is
    the field emptying — which looks exactly like esc."""
    from relaycli.ui.live import LaneActivity, _send_steer

    sched = _steerable_scheduler("a", "b")
    activity = LaneActivity()
    _send_steer(sched, "also add a test", 1, activity)

    entry = activity.transcript.entries()[-1]
    assert entry.task_id == "b" and entry.role_id == "coder"
    assert entry.ok and "also add a test" in entry.text


def test_an_undelivered_steer_says_so_in_the_transcript():
    from relaycli.ui.live import LaneActivity, _send_steer

    sched = _steerable_scheduler("a", "b", running={"a"})
    activity = LaneActivity()
    _send_steer(sched, "hello", 1, activity)

    entry = activity.transcript.entries()[-1]
    assert entry.ok is False and "not delivered" in entry.text


def test_the_input_row_draws_what_is_being_typed():
    from relaycli.ui.keymap import ViewState

    sched = _settled_scheduler("a")
    console = Console(file=io.StringIO(), width=120, height=24, force_terminal=True)
    state = ViewState(steering=True, steer_text="also add a test")
    row = next(line for line in render_frame_lines(sched, console, "dark", state)
               if "also add a test" in line.plain)
    assert "watching" not in row.plain   # the idle placeholder is gone


def test_the_idle_row_keeps_its_placeholder():
    sched = _settled_scheduler("a")
    console = Console(file=io.StringIO(), width=120, height=24, force_terminal=True)
    plain = "\n".join(line.plain for line in render_frame_lines(sched, console, "dark"))
    assert "watching" in plain


def test_the_key_strip_names_the_lane_a_note_would_go_to():
    """`s` does not move the cursor and the lane list keeps redrawing
    while you type, so the strip has to say who is being addressed."""
    from relaycli.ui.keymap import ViewState

    sched = _settled_scheduler("zebra", "alpha")
    console = Console(file=io.StringIO(), width=120, height=24, force_terminal=True)
    lines = render_frame_lines(sched, console, "dark",
                               ViewState(steering=True, selected=1, steer_text="hi"))
    strip = lines[-1].plain
    assert "alpha" in strip and "send" in strip and "cancel" in strip
    assert "stop all" not in strip   # esc cancels the note here, not the run


def test_the_steer_field_never_overflows_the_frame():
    from relaycli.ui.keymap import STEER_MAX_CHARS, ViewState

    sched = _settled_scheduler("a")
    for width in (120, 200):
        console = Console(file=io.StringIO(), width=width, height=24, force_terminal=True)
        state = ViewState(steering=True, steer_text="x" * STEER_MAX_CHARS)
        for line in render_frame_lines(sched, console, "dark", state):
            assert line.cell_len <= width, f"{line.plain!r} overflowed {width} columns"


def test_the_steer_row_itself_fits_the_narrowest_terminal_the_frame_allows():
    """80 columns is the floor the whole frame is gated on, and a
    STEER_MAX_CHARS note is wider than that — so the row, not the cap, is
    what has to hold the line. Asserted against the renderer directly:
    the frame-level check above cannot run at 80 on a legacy Windows
    console, which reports one column fewer than it is given."""
    from relaycli.ui.frame import render_input_row
    from relaycli.ui.keymap import STEER_MAX_CHARS

    for width in (80, 120):
        row = render_input_row(resolve_columns(width), "dark", width,
                               placeholder="idle", typed="x" * STEER_MAX_CHARS)
        assert row.cell_len <= width


def test_a_clipped_field_keeps_the_end_the_user_is_typing():
    """Clipping from the left, not the right: the tail is where the
    cursor is, and a field that hides what you are typing right now is
    worse than one that hides what you typed a moment ago."""
    from relaycli.ui.frame import render_input_row

    typed = "".join(str(i % 10) for i in range(200))
    row = render_input_row(resolve_columns(80), "dark", 80, placeholder="idle", typed=typed)
    assert row.plain.rstrip("▌").endswith(typed[-10:])
