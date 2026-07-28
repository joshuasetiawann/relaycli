"""Live progress views for --experimental-parallel.

Two presentations, chosen once per run by `live_view_supported`:

- A real TTY, motion allowed, wide enough, AND permission_mode is
  full-auto: a rich.Live pinned frame (status bar + bounded lane list)
  that redraws by pulling fresh state straight from the live Scheduler on
  every frame (§6's frame-ceiling / motion rules) — LiveFrame implements
  Rich's __rich_console__ protocol so Live's own background-refresh
  thread regenerates it, rather than this module hand-rolling a redraw
  loop.
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

Neither presentation is the full pinned-frame vision from
DESIGN_TOKENS.md §4 (no scrolling transcript region sharing the frame, no
permission band, no focus mode, no diff review, no keymap) — this is the
status-bar + lane-list slice of it, wired to real Scheduler state.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.text import Text

from relaycli.core.config import PermissionMode
from relaycli.ui import theme
from relaycli.ui.lanes import GroupSummary, LaneView, group_for_display, render_group_row, render_lane_row
from relaycli.ui.layout import LANE_LIST_MAX_ROWS, TooNarrowError, resolve_columns

if TYPE_CHECKING:
    from rich.console import ConsoleOptions, RenderResult

    from relaycli.agent.scheduler import Scheduler, SchedulerResult
    from relaycli.core.config import Settings
    from relaycli.core.context import ProjectContext
    from relaycli.core.permissions import PermissionManager


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


def _elapsed_for(scheduler: "Scheduler", task_id: str, status: str) -> float:
    started = scheduler.task_started_at.get(task_id)
    if started is None:
        return 0.0
    ended = scheduler.task_ended_at.get(task_id)
    if ended is not None:
        return ended - started
    if status == "running":
        return time.perf_counter() - started
    return 0.0


def lane_views_for(scheduler: "Scheduler") -> list[LaneView]:
    """This instant's LaneView list, built straight from live Scheduler
    state. Called fresh every frame/tick, never cached — per-task
    tokens/cost only exist once a task's TaskOutcome lands in
    scheduler.outcomes (Agent.run() has no incremental usage reporting),
    so a still-running task shows 0 for both until it completes."""
    views = []
    for task_id, task in scheduler.graph.tasks.items():
        outcome = scheduler.outcomes.get(task_id)
        views.append(LaneView(
            task_id=task_id, role_id=task.role_id, status=task.status, goal=task.goal,
            tokens=outcome.usage.total_tokens if outcome else 0,
            cost_usd=outcome.usage.cost_usd if outcome else 0.0,
            elapsed_s=_elapsed_for(scheduler, task_id, task.status),
        ))
    return views


def render_status_bar(scheduler: "Scheduler", mode: theme.ColorMode) -> Text:
    palette = theme.palette_for(mode)
    done = sum(1 for t in scheduler.graph.tasks.values() if t.status == "done")
    total = len(scheduler.graph.tasks)
    text = Text()
    text.append("relaycli", style=f"bold {palette.accent}" if palette else "bold")
    text.append(f"  parallel  {done}/{total} done  ",
                style=palette.muted if palette else None)
    text.append(f"{scheduler.budget.spent_tokens_total} tokens  ",
                style=palette.muted if palette else None)
    text.append(f"${scheduler.budget.spent_usd_total:.4f}",
                style=palette.warning if palette else None)
    return text


def render_frame_lines(scheduler: "Scheduler", console: Console, mode: theme.ColorMode) -> list[Text]:
    """Status bar + bounded lane list as a flat list of rows — shared by
    LiveFrame (rendered inside a rich.Live pinned region) and anything
    else that wants the same content without the animation machinery
    (e.g. a future --plain snapshot)."""
    columns = resolve_columns(console.size.width)
    lines = [render_status_bar(scheduler, mode)]
    lanes = lane_views_for(scheduler)
    for lane in group_for_display(lanes, max_rows=LANE_LIST_MAX_ROWS):
        if isinstance(lane, GroupSummary):
            lines.append(render_group_row(lane, columns, mode))
        else:
            lines.append(render_lane_row(lane, columns, mode))
    return lines


class LiveFrame:
    """Rich __rich_console__ renderable: Live's own background-refresh
    thread calls this on every frame, so it must read live.scheduler
    fresh each time rather than freezing data at construction — the
    idiomatic Rich pattern for "renders current state," used here instead
    of this module driving its own redraw loop."""

    def __init__(self, scheduler: "Scheduler", mode: theme.ColorMode) -> None:
        self.scheduler = scheduler
        self.mode = mode

    def __rich_console__(self, console: Console, options: "ConsoleOptions") -> "RenderResult":
        yield from render_frame_lines(self.scheduler, console, self.mode)


async def _run_with_live_frame(
    settings: "Settings", request: str, *, console: Console,
    project: "ProjectContext | None", permissions: "PermissionManager | None",
) -> "SchedulerResult":
    from relaycli.agent.orchestrator import run_parallel
    from relaycli.appconfig import load_app_config

    mode = theme.current_color_mode(load_app_config().preference("theme"))
    holder: dict[str, "Scheduler"] = {}

    def on_scheduler_ready(scheduler: "Scheduler") -> None:
        holder["scheduler"] = scheduler
        live.update(LiveFrame(scheduler, mode))

    def on_tick() -> None:
        # Live's own refresh_per_second timer repaints the current
        # renderable; on_tick doesn't need to do anything for the frame
        # to stay current, since LiveFrame reads scheduler state fresh on
        # every call. It exists so a future consumer of on_tick doesn't
        # have to special-case "the live-frame path passes None instead."
        pass

    with Live(Text("relaycli parallel — starting…", style="dim"),
              console=console, refresh_per_second=15, transient=False) as live:
        return await run_parallel(
            settings, request, console=console, project=project, permissions=permissions,
            on_scheduler_ready=on_scheduler_ready, on_tick=on_tick,
        )


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
