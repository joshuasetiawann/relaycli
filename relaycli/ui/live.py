"""Live progress views for --experimental-parallel.

Two presentations, chosen once per run by `live_view_supported`:

- A real TTY, motion allowed, wide enough, AND permission_mode is
  full-auto: a rich.Live pinned frame — the design's whole screen, in
  §04's row order: status bar, rule, bounded lane list, transcript,
  rule, input caret, key strip. It redraws by pulling fresh state
  straight from the live Scheduler on every frame (§6's frame-ceiling /
  motion rules); LiveFrame implements Rich's __rich_console__ protocol
  so Live's own background-refresh thread regenerates it, rather than
  this module hand-rolling a redraw loop.
- Everything else: one printed line per actual task-status change, driven
  by Scheduler.on_tick — no control codes, safe under any permission
  mode, safe piped to a file (§6: "piped output prints one line per
  event").

The permission_mode gate is the load-bearing safety rule here: task
agents share the same Console as the live frame, and a permission prompt
(rich.prompt.Confirm.ask, in core/permissions.py) reads from the same
terminal a Live auto-refresh thread is concurrently repainting — a
combination Rich does not make safe on its own. full-auto is the only
mode that (almost) never prompts (core/permissions.py's
_ALWAYS_PROMPT_ACTIONS still prompts for `read_secret` even there — a
narrow, rare residual risk accepted rather than building the Live-pause
integration a general fix would need). Any other mode falls back to the
progress-lines presentation, which never repaints anything and so never
conflicts with a prompt.

The frame is deliberately NOT drawn on the alternate screen. Rendering
it there would match the mockups' framed panel a little more closely,
but it would also swallow that residual `read_secret` prompt completely
— the run would sit waiting on input the user cannot see. Bottom-pinned
costs the panel border and keeps the prompt visible, which is the right
side of that trade.

Still absent from the design, and named here so the gap is not mistaken
for an oversight: the permission band (§08), the diff review queue, the
plan-review screen, and focus mode's lease/graph strips. Each needs a
Scheduler or Agent capability that does not exist yet — a staged-edit
permission mode, a lease queue — not a renderer.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from rich.console import Console
from rich.live import Live
from rich.text import Text

from relaycli.core.config import PermissionMode
from relaycli.ui import frame, keymap, keyreader, theme
from relaycli.ui.lanes import (GroupHeader, GroupSummary, LaneView, group_for_display,
                               id_role_label, render_group_header, render_group_row,
                               render_lane_row, render_lease_row, tool_target)
from relaycli.ui.layout import (LANE_GROUPING_THRESHOLD, LANE_LIST_MAX_ROWS,
                                MIN_TRANSCRIPT_ROWS, RULE_ROWS, TRANSCRIPT_HEADER_ROWS,
                                TooNarrowError, resolve_columns, transcript_rows)

if TYPE_CHECKING:
    from rich.console import ConsoleOptions, RenderResult

    from relaycli.agent.scheduler import Scheduler, SchedulerResult
    from relaycli.core.config import Settings
    from relaycli.core.context import ProjectContext
    from relaycli.core.permissions import PermissionManager

# A tool result short enough to sit on the end of its own tool line
# ("read src/lease/queue.ts · 240 lines") rather than claiming a row of
# its own. Longer ones get their own line, like the design's test output.
RESULT_INLINE_MAX = 44


def _motion_enabled() -> bool:
    return "NO_MOTION" not in os.environ


def narrow_terminal_refusal(console: Console) -> str | None:
    """The exact §4 refusal line if the terminal is under 80 columns,
    else None. Only gates the boxed lane list — the progress-lines
    fallback doesn't need 80 columns and still runs either way."""
    try:
        resolve_columns(console.size.width)
    except TooNarrowError as exc:
        return str(exc)
    return None


def live_view_supported(console: Console, settings: "Settings") -> bool:
    """Whether the animated pinned frame is both possible (real TTY, wide
    enough, motion allowed) and safe (full-auto permission mode — see the
    module docstring). False falls back to progress-lines, not to
    silence."""
    if not console.is_terminal or not _motion_enabled():
        return False
    if settings.permission_mode is not PermissionMode.full_auto:
        return False
    return narrow_terminal_refusal(console) is None


def _stamp() -> str:
    """Wall-clock HH:MM:SS. The transcript is the record you scroll back
    through after the fact, where an elapsed counter means nothing."""
    return datetime.now().strftime("%H:%M:%S")


class TranscriptLog:
    """Bounded, append-only record of what every agent did, in real time.

    Written from task threads, read from the render thread, so every
    access takes the lock and `entries` hands out copies: a renderer must
    never be able to observe an entry being rewritten underneath it, and
    a frozen dataclass alone would not prevent handing out the same
    object the log later replaces.
    """

    def __init__(self, limit: int = 400) -> None:
        self._lock = threading.Lock()
        self._entries: list[frame.TranscriptEntry] = []
        self._limit = limit

    def append(self, entry: frame.TranscriptEntry) -> None:
        with self._lock:
            self._append_locked(entry)

    def _append_locked(self, entry: frame.TranscriptEntry) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._limit:
            del self._entries[: len(self._entries) - self._limit]

    def attach_result(self, task_id: str, summary: str, ok: bool) -> None:
        """Fold a tool's result onto that tool's own line when it is short
        enough, the way the design writes `read src/lease/queue.ts · 240
        lines`. A long result — a test run's output, say — earns its own
        row instead, which is also what the design does."""
        if not summary:
            return
        with self._lock:
            for index in range(len(self._entries) - 1, -1, -1):
                entry = self._entries[index]
                if entry.task_id != task_id:
                    continue
                if entry.kind == "tool" and not entry.text and len(summary) <= RESULT_INLINE_MAX:
                    self._entries[index] = replace(entry, text=summary, ok=ok)
                    return
                break
            last = self._entries[-1] if self._entries else None
            self._append_locked(frame.TranscriptEntry(
                stamp=_stamp(), kind="result", ok=ok, text=summary,
                task_id=task_id, role_id=last.role_id if last else ""))

    def entries(self, task_id: str | None = None) -> list[frame.TranscriptEntry]:
        with self._lock:
            source = self._entries if task_id is None else [
                e for e in self._entries if e.task_id == task_id]
            return [replace(e) for e in source]


class LaneActivity:
    """What each task is doing right now — the tool it has open, the model
    it is actually running on, and whether the router escalated it — plus
    the shared transcript every lane writes into.

    The tool/target column has existed since the lane renderer was written
    (§4 gives it a fixed width and its own truncation rule) but nothing
    ever filled it: parallel task agents were created without a Reporter,
    so no caller learned which tool a task had open. The model column had
    the same problem for the same reason.

    Written from task threads and read from the render thread, so it takes
    a lock; the values are plain strings, replaced whole, never mutated.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: dict[str, tuple[str, str]] = {}
        self._models: dict[str, tuple[str, str]] = {}  # task -> (first, latest)
        self.transcript = TranscriptLog()

    def reporter_for(self, task_id: str, role_id: str):
        from relaycli.agent.reporter import Reporter

        activity = self

        class _LaneReporter(Reporter):
            def __init__(self) -> None:
                self._buffer: list[str] = []

            def model_start(self, n: int, model: str) -> None:
                activity._note_model(task_id, model)

            def assistant_token(self, text: str) -> None:
                self._buffer.append(text)

            def assistant_discard(self) -> None:
                self._buffer.clear()

            def assistant_end(self) -> None:
                text = " ".join("".join(self._buffer).split())
                self._buffer.clear()
                if text:
                    activity.transcript.append(frame.TranscriptEntry(
                        stamp=_stamp(), kind="text", task_id=task_id,
                        role_id=role_id, text=text))

            def tool_start(self, call) -> None:
                target = tool_target(call)
                activity._set(task_id, call.name, target)
                activity.transcript.append(frame.TranscriptEntry(
                    stamp=_stamp(), kind="tool", task_id=task_id, role_id=role_id,
                    tool=call.name, target=target))

            def tool_end(self, call, result) -> None:
                activity._clear(task_id)
                summary = (result.summary if result is not None else "tool error")
                activity.transcript.attach_result(
                    task_id, summary, bool(result is not None and result.ok))

            def close(self) -> None:
                # tool_end never fires if the agent dies inside a tool call,
                # which would leave the lane advertising a command that is
                # not running any more. make_run_task closes every reporter
                # in a finally, so this is the one hook that fires on the
                # crash path too. The base Reporter has no close(), and that
                # hasattr guard is why this must be declared explicitly.
                activity._clear(task_id)

        return _LaneReporter()

    def _set(self, task_id: str, tool: str, target: str) -> None:
        with self._lock:
            self._current[task_id] = (tool, target)

    def _clear(self, task_id: str) -> None:
        with self._lock:
            self._current.pop(task_id, None)

    def _note_model(self, task_id: str, model: str) -> None:
        with self._lock:
            first, _ = self._models.get(task_id, (model, model))
            self._models[task_id] = (first, model)

    def current(self, task_id: str) -> tuple[str, str]:
        with self._lock:
            return self._current.get(task_id, ("", ""))

    def model(self, task_id: str) -> tuple[str, bool]:
        """(model, escalated). Escalation is observed, never guessed: it
        is true only because two different model names were actually seen
        on this task, which is exactly what §2's ▲ claims."""
        with self._lock:
            first, latest = self._models.get(task_id, ("", ""))
        return latest, bool(first and latest and first != latest)


def _elapsed_for(scheduler: "Scheduler", task_id: str, status: str) -> float | None:
    """Seconds this task has been running, or has run for. None means it
    never started, which the lane renders as an em dash — `0s` there would
    claim it ran instantly."""
    started = scheduler.task_started_at.get(task_id)
    if started is None:
        return None
    ended = scheduler.task_ended_at.get(task_id)
    if ended is not None:
        return ended - started
    if status == "running":
        return time.perf_counter() - started
    return None


def _lease_conflict(scheduler: "Scheduler", task) -> tuple[str, str, float | None]:
    """(holder, contended path, seconds held) for a task whose graph deps
    are satisfied but whose path claims collide with a running task's
    lease, else ("", "", None).

    The scheduler leaves such a task in "ready" and simply skips it each
    round, so without this the lane would read as an idle agent rather
    than a blocked one — §8's "a user who can't see why an agent is idle
    assumes the tool is broken".
    """
    from relaycli.agent.leases import claims_overlap

    if task.status != "ready" or not task.path_claims:
        return "", "", None
    holder = scheduler.leases.conflicts_with_running(task.path_claims)
    if holder is None:
        return "", "", None
    held_paths = scheduler.leases.paths_held_by(holder)
    contended = next((claim for claim in task.path_claims
                      if claims_overlap((claim,), held_paths)), task.path_claims[0])
    started = scheduler.task_started_at.get(holder)
    held_for = (time.perf_counter() - started) if started is not None else None
    return holder, contended, held_for


def _detail_for(task, outcome) -> str:
    """The state-specific phrase for the lane's tool column. Every branch
    reports something the Scheduler actually recorded — a dependency list,
    an outcome summary, an error — so the column can never be describing a
    state the run is not in."""
    if task.status in ("pending", "blocked"):
        return ", ".join(task.depends_on)
    if outcome is None:
        return ""
    if task.status == "done":
        return outcome.summary or ""
    if task.status in ("failed", "cancelled"):
        return (outcome.error or outcome.summary or "").splitlines()[0] if (
            outcome.error or outcome.summary) else ""
    return ""


def lane_views_for(scheduler: "Scheduler", selected: int | None = None,
                   activity: "LaneActivity | None" = None) -> list[LaneView]:
    """This instant's LaneView list, built straight from live Scheduler
    state. Called fresh every frame/tick, never cached — per-task
    tokens/cost only exist once a task's TaskOutcome lands in
    scheduler.outcomes (Agent.run() has no incremental usage reporting),
    so a still-running task shows 0 for both until it completes.

    `selected` marks one lane focused (the key map's cursor). It indexes
    this list, i.e. graph order, so grouping the display can never move
    the cursor out from under the user."""
    views = []
    for index, (task_id, task) in enumerate(scheduler.graph.tasks.items()):
        outcome = scheduler.outcomes.get(task_id)
        tool, target = activity.current(task_id) if activity else ("", "")
        model, escalated = activity.model(task_id) if activity else ("", False)
        holder, contended, held_for = _lease_conflict(scheduler, task)
        views.append(LaneView(
            task_id=task_id, role_id=task.role_id, status=task.status, goal=task.goal,
            tokens=outcome.usage.total_tokens if outcome else 0,
            cost_usd=outcome.usage.cost_usd if outcome else 0.0,
            elapsed_s=_elapsed_for(scheduler, task_id, task.status),
            focused=(selected == index),
            tool=tool, target=target, detail=_detail_for(task, outcome),
            model=model, escalated=escalated,
            lease_holder=holder, lease_path=contended, lease_held_s=held_for,
        ))
    return views


def status_bar_data(scheduler: "Scheduler", settings: "Settings | None" = None,
                    project: "ProjectContext | None" = None) -> frame.StatusBarData:
    """Assemble the status bar's inputs from the run's real state. The git
    read is cached (ui/gitinfo) because this is called on every frame."""
    from relaycli.ui import gitinfo

    root = project.root if project is not None else Path.cwd()
    repo = gitinfo.status(root)
    mode = ""
    if settings is not None and getattr(settings, "permission_mode", None) is not None:
        mode = getattr(settings.permission_mode, "value", str(settings.permission_mode))
    return frame.StatusBarData(
        cwd=root, branch=repo.branch, dirty=repo.dirty, permission_mode=mode,
        agents=len(scheduler.graph.tasks),
        tokens=scheduler.budget.spent_tokens_total,
        spent_usd=scheduler.budget.spent_usd_total,
        limit_usd=scheduler.budget.max_usd_total,
    )


def render_help_overlay(mode: theme.ColorMode) -> list[Text]:
    """The `?` overlay. Built from keymap.KEY_HELP so it cannot drift from
    the bindings themselves."""
    palette = theme.palette_for(mode)
    lines = [Text("keys", style=f"bold {palette.accent}" if palette else "bold")]
    width = max(len(keys) for keys, _ in keymap.KEY_HELP)
    for keys, description in keymap.KEY_HELP:
        row = Text()
        row.append(f"  {keys.ljust(width)}  ", style=palette.muted if palette else None)
        row.append(description, style=palette.text if palette else None)
        lines.append(row)
    return lines


def key_strip_hints(lanes: list[LaneView]) -> list[frame.KeyHint]:
    """The bottom strip, built from the bindings that actually work.

    The design's strip also offers `d diffs`, `y/n answer`, `^b budget`
    and `^r roles`. Those are not here because nothing behind them exists
    yet, and a key strip that advertises a key which does nothing is worse
    than a shorter strip. The awaiting-you badge is kept, because that one
    is real the moment a permission request opens."""
    waiting = [lane.task_id for lane in lanes if lane.awaiting_you]
    hints = [frame.KeyHint("tab", "lane"), frame.KeyHint("1-9", "jump"),
             frame.KeyHint("enter", "focus"), frame.KeyHint("m", "merged"),
             frame.KeyHint("^k", "collapse"), frame.KeyHint("s", "steer"),
             frame.KeyHint("x", "drop"), frame.KeyHint("R", "retry")]
    if waiting:
        hints.append(frame.KeyHint("y/n", "answer", ", ".join(waiting[:2])))
    hints.extend([frame.KeyHint("esc", "stop all"), frame.KeyHint("?", "keys")])
    return hints


def _lane_rows(lanes: list[LaneView], columns, mode: theme.ColorMode,
               width: int, spinner: str | None = None,
               budget: int = LANE_LIST_MAX_ROWS) -> list[Text]:
    """The pinned lane list: bands and their headers above
    LANE_GROUPING_THRESHOLD agents, a flat graph-ordered list below it.

    The `└─ held by a1` sub-row is drawn only in the flat list. Grouped,
    the row budget has no space for it — which is exactly what the
    design's own six-agent screen does, folding the holder into the lane's
    own tool column instead.

    `budget` is counted in *rows*, not lanes. Those are not the same
    number: five lease-blocked lanes each bring a sub-row, which is ten
    rows out of a region §4 caps at nine. Counting lanes here let the list
    grow past its own ceiling and eat the transcript.
    """
    grouped = len(lanes) > LANE_GROUPING_THRESHOLD
    rows: list[Text] = []
    for item in group_for_display(lanes, max_rows=budget):
        if isinstance(item, GroupHeader):
            rows.append(render_group_header(item, columns, mode, width))
        elif isinstance(item, GroupSummary):
            rows.append(render_group_row(item, columns, mode, width))
        else:
            rows.append(render_lane_row(item, columns, mode, width, spinner))
            if item.lease_holder and not grouped:
                rows.append(render_lease_row(item, columns, mode))
    # One slice, at the end, is the whole ceiling. An in-loop break as
    # well would read like a second guarantee while being unreachable
    # given this one — and an unreachable guard is a guard no test can
    # ever prove still works.
    return rows[:budget]


def render_frame_lines(
    scheduler: "Scheduler", console: Console, mode: theme.ColorMode,
    state: "keymap.ViewState | None" = None,
    activity: "LaneActivity | None" = None,
    settings: "Settings | None" = None,
    project: "ProjectContext | None" = None,
) -> list[Text]:
    """The whole frame, in §04's row order: status bar, rule, lane list,
    transcript header, transcript, rule, input caret, key strip.

    The transcript is the one region that flexes; every other row is
    pinned, and the transcript is padded out with blank rows so the input
    caret and key strip stay on the bottom line whether there are two
    transcript entries or two hundred. That padding is what makes the
    strip *pinned* rather than merely last.

    `state` carries the key map's view state (cursor, help overlay,
    collapsed lane list, merged transcript); None renders the default
    view, which is what the non-interactive paths still want."""
    state = state or keymap.ViewState()
    width = console.size.width
    columns = resolve_columns(width)
    # One row short of the terminal: a renderable exactly as tall as the
    # screen makes Live scroll the whole frame up by a line on every
    # repaint, since it still needs somewhere to put the cursor.
    height = max(console.size.height - 1, 1)

    lines = [frame.render_status_bar(status_bar_data(scheduler, settings, project),
                                     columns, mode, width),
             frame.render_rule(columns, mode, width)]
    if state.show_help:
        # The overlay replaces the lane list rather than pushing it off the
        # bottom — §4's row budget has no room for both on a 24-row terminal.
        return lines + render_help_overlay(mode)

    lanes = lane_views_for(scheduler, selected=state.selected, activity=activity)
    if state.steering:
        target = lanes[state.selected] if 0 <= state.selected < len(lanes) else None
        # Naming the addressee in the strip, not just the row, is what
        # stops a note going to whichever lane the cursor happened to be
        # on: `s` does not move the cursor, and the lane list keeps
        # redrawing underneath the field while you type.
        strip = [frame.KeyHint("enter", "send", id_role_label(target.task_id, target.role_id)
                               if target else ""),
                 frame.KeyHint("esc", "cancel")]
    else:
        strip = key_strip_hints(lanes)
    bottom = [frame.render_rule(columns, mode, width),
              frame.render_input_row(
                  columns, mode, width,
                  placeholder="watching — esc stops every agent",
                  typed=state.steer_text if state.steering else None),
              frame.render_key_strip(strip, columns, mode, width)]

    # Lanes get whatever is left once the pinned chrome and §4's transcript
    # floor are subtracted, capped at the nine rows §4 allows them: "the
    # lane list collapses to a strip first". Sizing the list before drawing
    # it, rather than trimming afterwards, is what keeps the caret and key
    # strip on the bottom line of a short terminal instead of below it.
    fixed = len(lines) + len(bottom)
    lane_budget = min(LANE_LIST_MAX_ROWS,
                      max(height - fixed - MIN_TRANSCRIPT_ROWS - TRANSCRIPT_HEADER_ROWS, 0))
    if lane_budget == 0 and not state.lane_list_collapsed:
        # Too short for both regions: the lanes are the pinned one, so they
        # win and the transcript is what disappears.
        lane_budget = max(height - fixed, 0)

    # One clock for every lane, so the spinners tick together (§6). Reduced
    # motion drops back to the static ◆ rather than freezing on whichever
    # frame happened to be current.
    spinner = theme.spinner_frame(time.monotonic()) if _motion_enabled() else None
    lane_rows = [] if state.lane_list_collapsed else _lane_rows(
        lanes, columns, mode, width, spinner, lane_budget)
    lines.extend(lane_rows)

    available = transcript_rows(
        height, lane_rows=len(lane_rows), permission_band_open=False,
        chrome_rows=RULE_ROWS + TRANSCRIPT_HEADER_ROWS)
    if activity is not None and available > 0:
        focused = lanes[state.selected] if 0 <= state.selected < len(lanes) else None
        label = "all agents" if state.merged or focused is None else id_role_label(
            focused.task_id, focused.role_id)
        lines.append(frame.render_transcript_header(columns, mode, width,
                                                    label=label, merged=state.merged))
        entries = activity.transcript.entries(
            None if (state.merged or focused is None) else focused.task_id)
        body = frame.render_transcript(entries, columns, mode, width,
                                       merged=state.merged, max_rows=available)
        lines.extend(body)
        lines.extend(Text("") for _ in range(available - len(body)))

    lines.extend(bottom)
    if len(lines) > height:
        # Last resort, for a terminal too short even for the pinned rows
        # alone. Keep the status bar and the bottom of the frame; what goes
        # is the middle, which is the only part that can be scrolled back
        # to. Silently overflowing instead would push the frame down a row
        # on every repaint and turn the pinned view into a scrolling one.
        lines = lines[:1] + lines[-(height - 1):] if height > 1 else lines[:1]
    return lines


def dispatch_lane_action(scheduler: "Scheduler | None", action: str, selected: int) -> str | None:
    """Turn a lane action plus the cursor position into a Scheduler
    request. Returns the task id acted on, or None when there's nothing to
    act on (no scheduler yet, cursor past the end, or not a lane action).

    A module-level function rather than a closure so it can be tested
    against a real Scheduler: the cursor indexes graph order, which is the
    same order lane_views_for builds, so what the user sees selected is
    what gets addressed.
    """
    if scheduler is None or action not in keymap.LANE_ACTIONS:
        return None
    task_ids = list(scheduler.graph.tasks)
    if not (0 <= selected < len(task_ids)):
        return None
    task_id = task_ids[selected]
    if action == "drop_task":
        scheduler.request_cancel(task_id)
    elif action == "retry_task":
        scheduler.request_retry(task_id)
    return task_id


def dispatch_steer(scheduler: "Scheduler | None", note: str, selected: int) -> tuple[str, bool]:
    """Send a typed note to the lane under the cursor. Returns
    (task_id, delivered).

    Delivery is genuinely uncertain here in a way drop and retry are not:
    those queue a request the Scheduler applies whenever it next gets to
    it, while a steer needs an Agent that is running *now*. A task that
    finished while the note was being typed simply has nobody to hand it
    to, and the caller says so rather than letting the field look like it
    worked. An empty task id means there was nothing under the cursor at
    all.
    """
    if scheduler is None:
        return "", False
    task_ids = list(scheduler.graph.tasks)
    if not (0 <= selected < len(task_ids)):
        return "", False
    task_id = task_ids[selected]
    return task_id, scheduler.request_steer(task_id, note)


def _send_steer(scheduler: "Scheduler | None", note: str, selected: int,
                activity: "LaneActivity | None") -> tuple[str, bool]:
    """dispatch_steer, plus the transcript line that records what happened.

    The record is not decoration. A steer produces no visible change until
    the agent's next iteration — which may be a whole model call away —
    so without a line saying the note was taken, the only feedback is the
    field emptying, which looks identical to esc.
    """
    task_id, delivered = dispatch_steer(scheduler, note, selected)
    if activity is None:
        return task_id, delivered
    role_id = ""
    if scheduler is not None and task_id in scheduler.graph.tasks:
        role_id = scheduler.graph.tasks[task_id].role_id
    activity.transcript.append(frame.TranscriptEntry(
        stamp=_stamp(), kind="note", task_id=task_id, role_id=role_id,
        text=(f"steer → {note}" if delivered else
              f"steer not delivered ({task_id or 'no lane'} is not running) → {note}"),
        ok=delivered,
    ))
    return task_id, delivered


class LiveFrame:
    """Rich __rich_console__ renderable: Live's own background-refresh
    thread calls this on every frame, so it must read live.scheduler
    fresh each time rather than freezing data at construction — the
    idiomatic Rich pattern for "renders current state," used here instead
    of this module driving its own redraw loop.

    `state_getter` is read the same way and for the same reason: the key
    reader thread replaces the ViewState between frames, so holding a
    reference to one instant's state would pin the cursor in place."""

    def __init__(self, scheduler: "Scheduler", mode: theme.ColorMode,
                 state_getter: "Callable[[], keymap.ViewState] | None" = None,
                 activity: "LaneActivity | None" = None,
                 settings: "Settings | None" = None,
                 project: "ProjectContext | None" = None) -> None:
        self.scheduler = scheduler
        self.mode = mode
        self.state_getter = state_getter
        self.activity = activity
        self.settings = settings
        self.project = project

    def __rich_console__(self, console: Console, options: "ConsoleOptions") -> "RenderResult":
        state = self.state_getter() if self.state_getter is not None else None
        yield from render_frame_lines(self.scheduler, console, self.mode, state,
                                      self.activity, self.settings, self.project)


async def _run_with_live_frame(
    settings: "Settings", request: str, *, console: Console,
    project: "ProjectContext | None", permissions: "PermissionManager | None",
) -> "SchedulerResult":
    from relaycli.agent.orchestrator import run_parallel
    from relaycli.appconfig import load_app_config

    mode = theme.current_color_mode(load_app_config().preference("theme"))
    holder: dict[str, "Scheduler"] = {}
    # Mutated by the key reader thread, read by Live's refresh thread. A
    # frozen dataclass swapped under a lock, rather than a mutable object
    # edited in place: a frame can then never observe a half-applied
    # transition (e.g. focused=True with the old selected index).
    state_lock = threading.Lock()
    state_box = {"state": keymap.ViewState()}
    activity = LaneActivity()

    def get_state() -> keymap.ViewState:
        with state_lock:
            return state_box["state"]

    def lane_count() -> int:
        scheduler = holder.get("scheduler")
        return len(scheduler.graph.tasks) if scheduler is not None else 0

    def on_key(key: str) -> None:
        with state_lock:
            current = state_box["state"]
            if current.steering:
                # Typing regime: every key is a character until enter or
                # esc closes the field. No lane action can fire from here,
                # which is the point — `x` in a sentence must type an x.
                state_box["state"], note = keymap.steer_key(current, key)
                action = None
            else:
                action = keymap.parse_key(key)
                state_box["state"] = keymap.apply_action(current, action, lane_count())
                note = None
        if note is not None:
            _send_steer(holder.get("scheduler"), note, current.selected, activity)
            return
        # Lane actions address the Scheduler, not the view. Requests are
        # queued there and applied on its own loop thread, so calling this
        # from the key reader thread is safe; the resulting status change
        # shows up through the normal graph read on the next frame.
        if action is not None:
            dispatch_lane_action(holder.get("scheduler"), action.action, current.selected)

    def should_stop() -> bool:
        return get_state().stop_requested

    def on_scheduler_ready(scheduler: "Scheduler") -> None:
        holder["scheduler"] = scheduler
        live.update(LiveFrame(scheduler, mode, get_state, activity, settings, project))

    def on_tick() -> None:
        # Live's own refresh_per_second timer repaints the current
        # renderable; on_tick doesn't need to do anything for the frame
        # to stay current, since LiveFrame reads scheduler state fresh on
        # every call. It exists so a future consumer of on_tick doesn't
        # have to special-case "the live-frame path passes None instead."
        pass

    stop_keys = keyreader.start(on_key)
    try:
        with Live(Text("relaycli parallel — starting…  (? for keys)", style="dim"),
                  console=console, refresh_per_second=15, transient=False) as live:
            return await run_parallel(
                settings, request, console=console, project=project, permissions=permissions,
                on_scheduler_ready=on_scheduler_ready, on_tick=on_tick,
                should_stop=should_stop, reporter_factory=activity.reporter_for,
            )
    finally:
        stop_keys()


def _progress_line(task_id: str, role_id: str, status: str, mode: theme.ColorMode) -> Text:
    palette = theme.palette_for(mode)
    glyph = theme.TASK_STATE_GLYPHS[status]
    mark = theme.ROLE_MARKS.get(role_id)
    line = Text()
    line.append(glyph.symbol if palette else glyph.ascii,
                style=(getattr(palette, theme.TASK_STATE_COLOR[status]) if palette else None))
    line.append(f" {task_id}", style=palette.text if palette else None)
    if mark:
        line.append(f" ({mark.code})", style=palette.muted if palette else None)
    word = status if palette else theme.TASK_STATE_WORD[status]
    line.append(f" {word}", style=None if palette else "bold")
    return line


async def _run_with_progress_lines(
    settings: "Settings", request: str, *, console: Console,
    project: "ProjectContext | None", permissions: "PermissionManager | None",
) -> "SchedulerResult":
    from relaycli.agent.orchestrator import run_parallel
    from relaycli.appconfig import load_app_config

    mode = theme.current_color_mode(load_app_config().preference("theme"))
    holder: dict[str, "Scheduler"] = {}
    last_status: dict[str, str] = {}

    def on_scheduler_ready(scheduler: "Scheduler") -> None:
        holder["scheduler"] = scheduler

    def on_tick() -> None:
        scheduler = holder.get("scheduler")
        if scheduler is None:
            return
        for task_id, task in scheduler.graph.tasks.items():
            if last_status.get(task_id) == task.status:
                continue
            last_status[task_id] = task.status
            console.print(_progress_line(task_id, task.role_id, task.status, mode))

    return await run_parallel(
        settings, request, console=console, project=project, permissions=permissions,
        on_scheduler_ready=on_scheduler_ready, on_tick=on_tick,
    )


async def run_parallel_with_view(
    settings: "Settings", request: str, *, console: Console,
    project: "ProjectContext | None" = None, permissions: "PermissionManager | None" = None,
) -> "SchedulerResult":
    """--experimental-parallel's real entry point: picks the live frame or
    the progress-lines fallback (see module docstring for the safety
    rule), prints the exact §4 refusal line once if the only thing
    blocking the live frame was a narrow terminal, then runs. Callers
    (cli.py) still print their own end-of-run summary afterward — this
    only covers *during* the run."""
    if live_view_supported(console, settings):
        return await _run_with_live_frame(
            settings, request, console=console, project=project, permissions=permissions,
        )
    refusal = narrow_terminal_refusal(console)
    if refusal is not None:
        console.print(refusal)
    return await _run_with_progress_lines(
        settings, request, console=console, project=project, permissions=permissions,
    )
