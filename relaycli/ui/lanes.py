"""Pure lane-list rendering for the pinned-frame terminal UI.

Source: docs/design/DESIGN_TOKENS.md §2 (glyphs), §4 (columns + truncation),
§9 (role marks). Builds rich.text.Text rows from plain view-models — no
Console, no live terminal state — so every row shape here is testable
without a TTY. relaycli/ui/live.py (Stage 4d) is the only caller that
touches a real terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

from relaycli.agent.graph import TaskStatus
from relaycli.ui import theme
from relaycli.ui.layout import LANE_GROUPING_THRESHOLD, ColumnWidths


def tool_target(call) -> str:
    """The path-ish argument worth showing beside a tool name.

    ToolCall.arguments is the raw JSON *string* the model produced, not a
    dict — reading it as a mapping silently yielded "" for every call ever
    made, which is why this column rendered the tool name and never its
    target. Tools name the argument differently (path/file/pattern/
    command), so take the first that is present rather than teaching this
    about every tool.

    Lives here rather than in ui/live.py because both transcripts need it:
    the lane's `tool_target` column and the single-agent run's transcript
    row, and the latter must not drag the scheduler in to get one string.
    """
    parse = getattr(call, "parsed_arguments", None)
    args: object
    if callable(parse):
        try:
            args = parse()
        except (ValueError, TypeError):
            return ""
    else:
        args = getattr(call, "arguments", None)
    if not isinstance(args, dict):
        return ""
    for key in ("path", "file", "file_path", "pattern", "query", "command"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


@dataclass(frozen=True)
class LaneView:
    """Everything one lane row needs to render. Deliberately plain data —
    the caller (ui/live.py) is responsible for building this from real
    Task/TaskOutcome/Budget state; nothing here reaches back into
    Scheduler internals."""

    task_id: str
    role_id: str
    status: TaskStatus
    goal: str
    tool: str = ""
    target: str = ""
    # State-specific phrase for the right-hand half of the tool column:
    # the outcome summary when settled, the dependency list when pending,
    # the error when failed. Always a measured fact from Scheduler state,
    # never a guess — see `lane_detail`.
    detail: str = ""
    model: str = ""
    # The model changed mid-task, i.e. the router escalated a tier. Only
    # ever set from two observed model names, never inferred from the
    # model's name or price.
    escalated: bool = False
    tokens: int = 0
    cost_usd: float = 0.0
    # None means "never started", which the design renders as an em dash
    # rather than 0s — a task that has not run has no elapsed time, and
    # showing "0s" claims it ran instantly.
    elapsed_s: float | None = 0.0
    focused: bool = False
    awaiting_you: bool = False
    # Deps are satisfied but a path lease held by `lease_holder` is what
    # is actually keeping this task out of a slot.
    lease_holder: str = ""
    lease_path: str = ""
    lease_held_s: float | None = None


@dataclass(frozen=True)
class GroupHeader:
    """One of the design's RUNNING / BLOCKED / NEEDS YOU / SETTLED band
    headers, emitted above LANE_GROUPING_THRESHOLD agents."""

    label: str
    count: int
    color_token: str = "muted"


@dataclass(frozen=True)
class GroupSummary:
    """A collapsed row standing in for N settled (done/failed/cancelled)
    lanes once the list exceeds LANE_GROUPING_THRESHOLD agents — see
    `group_for_display`. Carries the folded lanes themselves so a group of
    exactly one can still name it, which is what the design's SETTLED row
    does."""

    status: TaskStatus
    count: int
    lanes: tuple[LaneView, ...] = ()


# --- value formatting (each keeps its column's fixed width, per §4) --------
def clip_goal(text: str, width: int) -> str:
    """Clip at width-1, no ellipsis (§4) — losing the last word cleanly
    beats spending a character on '…'."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(width - 1, 0)]


def basename_target(target: str) -> str:
    """`dir/sub/file.py:42` -> `file.py:42` — drop directories, keep
    basename:line (§4's truncation rule for the tool+target column)."""
    if not target:
        return target
    head, sep, tail = target.partition(":")
    return (Path(head).name + sep + tail) if head else target


def format_tool_target(tool: str, target: str, width: int) -> str:
    """Combine tool + target into the column, dropping directories before
    falling back to a hard clip (§4)."""
    full = f"{tool} {target}" if target else tool
    if len(full) <= width:
        return full
    short = f"{tool} {basename_target(target)}" if target else tool
    if len(short) <= width:
        return short
    return clip_goal(short, width)


def format_tokens(n: int) -> str:
    """Compact token count: 999, 12.3k, 1.2M — always well under the
    8-column budget."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def format_cost(usd: float) -> str:
    """`$0.42` — two decimals, as every screen in the design shows it.

    Two decimals cannot represent a sub-cent cost, and a real `$0.004`
    printed as `$0.00` would read as free. The design accepts `$0.00` for
    a genuinely free local model, so zero keeps that spelling and anything
    non-zero that would round to it becomes `<$0.01` instead: still true,
    still inside the 7-column budget §4 says is "never cut".
    """
    magnitude = abs(usd)
    if magnitude == 0:
        return "$0.00"
    if magnitude < 0.005:
        return "<$0.01"
    if magnitude < 1000:
        return f"${usd:.2f}"
    # Past $1000 the cents stop mattering and the column would overflow;
    # dropping them beats truncating a digit off the dollars.
    return f"${usd:.0f}"


def format_elapsed(seconds: float | None) -> str:
    """`1m12s`, `0m41s`, `1h02m` — the design always spells the minutes,
    even at zero, so the column reads as one aligned duration rather than
    alternating between two shapes. None is "never started", which prints
    as an em dash: a task that has not run has no elapsed time, and `0s`
    would claim it ran instantly."""
    if seconds is None:
        return "—"
    total = max(int(seconds), 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s"


def id_role_label(task_id: str, role_id: str) -> str:
    """`a1 ▣ bnd` — id, family glyph, three-letter code (§2/§9). The glyph
    groups, the code identifies; the id is truncated rather than the code,
    which is fixed at three characters and so never needs clipping."""
    mark = theme.ROLE_MARKS.get(role_id)
    if mark is None:
        return task_id
    return f"{task_id} {mark.family_glyph} {mark.code}"


def format_model(model: str, *, escalated: bool, width: int) -> str:
    """The model column, with the §2 escalation marker appended when the
    router actually moved this task up a tier. The marker is protected
    from truncation — it is the part that changes the meaning of the
    line — so the name is clipped to make room for it."""
    if not model:
        return ""
    if not escalated:
        return model[:width]
    suffix = f" {theme.MARKERS['escalation'].symbol}"
    return (model[: max(width - len(suffix), 0)] + suffix)[:max(width, 0)]


def _pad(text: str, width: int) -> str:
    return text[:width].ljust(width)


def gutter_left(columns: ColumnWidths) -> int:
    """How much of §4's 2-column gutter sits on the left: "1 col each
    side, no vertical frame line". Every row in the frame — lanes, group
    headers, rules, the status bar — starts at this offset, which is what
    keeps the columns of a lane row aligned with everything above it."""
    return columns.gutter // 2


# --- row rendering -----------------------------------------------------------
def _fit(text: str, width: int) -> str:
    """Clip to the column, dropping directories from any path-ish token
    before resorting to a hard cut (§4's truncation rule). A cut that
    lands on a separator loses it too — a column ending in " ·" reads as
    a rendering fault rather than as truncation."""
    if len(text) <= width:
        return text
    shortened = " ".join(basename_target(part) for part in text.split(" "))
    if len(shortened) <= width:
        return shortened
    return clip_goal(shortened, width).rstrip(" ·")


def lane_detail(lane: LaneView, mode: theme.ColorMode, width: int) -> tuple[str, str]:
    """The tool+target column's text and its palette token.

    §4 gives this column a fixed width; §2 gives it a marker per state. It
    is the only column that changes meaning with the lane's state, so the
    whole per-state table lives here rather than being spread through
    `render_lane_row`. Under NO_COLOR the state's word is spelled into the
    text itself (WAIT / NEEDS YOU / …) instead of being carried by the
    colour, which is what makes that mode lossless.
    """
    plain = theme.palette_for(mode) is None

    def out(text: str, token: str) -> tuple[str, str]:
        return _fit(text, width), token

    if lane.awaiting_you:
        head = theme.AWAITING_YOU_WORD if plain else "needs you"
        return out(f"{head} · {lane.detail}" if lane.detail else head, "warning")

    if lane.lease_holder:
        # A held path lease, not the dependency graph, is what is keeping
        # this lane out of a slot — the design makes that its own state
        # rather than letting it read as an idle agent.
        head = "WAIT lease" if plain else f"{theme.MARKERS['lease_waiting'].symbol} lease"
        parts = [f"{head} {basename_target(lane.lease_path)}" if lane.lease_path else head]
        parts.append(lane.lease_holder)
        if lane.lease_held_s is not None:
            parts.append(format_elapsed(lane.lease_held_s))
        return out(" · ".join(parts), "waiting")

    status = lane.status
    if status == "running":
        if lane.tool:
            return out(f"{lane.tool} {lane.target}".strip(), "text_secondary")
        # No tool call open right now — the model is thinking. Saying so
        # beats a blank column, which reads as a stalled agent.
        return out("RUNNING" if plain else "running", "running")

    if status == "blocked":
        # The NO_COLOR word comes from theme.TASK_STATE_WORD rather than a
        # literal here, so the lane column and the §4 vocabulary can never
        # disagree about what this state is called.
        glyph = "" if plain else f"{theme.TASK_STATE_GLYPHS['blocked'].symbol} "
        head = f"{glyph}{theme.TASK_STATE_WORD['blocked'] if plain else 'blocked'}"
        deps = f" · dep {lane.detail}" if lane.detail else ""
        return out(f"{head}{deps}", "waiting")

    if status == "pending":
        head = "PENDING" if plain else "pending"
        return out(f"{head} · waits on {lane.detail}" if lane.detail else head, "muted")

    if status == "ready":
        head = "READY" if plain else "ready"
        return out(f"{head} · queued for a slot", "text")

    if status == "done":
        return out(lane.detail or ("DONE" if plain else "done"), "success")

    if status == "failed":
        head = "FAILED" if plain else "failed"
        return out(f"{head} · {lane.detail}" if lane.detail else head, "danger")

    head = "CANCELLED" if plain else "cancelled"
    return out(f"{head} · {lane.detail}" if lane.detail else head, "muted")


def _row_tokens(lane: LaneView) -> tuple[str, str, str]:
    """(id/goal, model+tokens, cost) tokens for this lane's weight.

    A settled or not-yet-eligible lane steps one shade dimmer across the
    whole row — the design's "dims to metadata weight, keeps its costs".
    """
    if lane.status == "cancelled":
        return "muted", "muted", "muted"
    if lane.status in ("done", "pending"):
        return "text_secondary", "muted", "text_secondary"
    return "text", "text_secondary", "text"


def render_lane_row(lane: LaneView, columns: ColumnWidths, mode: theme.ColorMode,
                    width: int | None = None, spinner: str | None = None) -> Text:
    """One lane row, ready to print/update in the pinned list. `mode`
    selects dark/light palette colors, or NO_COLOR's glyph+bold+word
    fallback (§4: "no fact present in the color version is lost").

    `width` is the terminal width; passing it lets a focused row paint its
    background across the whole line the way the design does. `spinner` is
    this frame's braille character, shared by every lane so they tick in
    unison (§6); None keeps §2's static ◆, which is what reduced motion
    and every non-animated caller want. The column
    widths in §4 are inclusive of their own trailing gap — the values are
    left- or right-justified inside a fixed span, and the space between
    columns is what is left over. Adding a separator on top of them
    overflowed a 120-column terminal by four characters.
    """
    palette = theme.palette_for(mode)
    primary, numeric, cost_token = _row_tokens(lane)
    emphasis = "bold" if (lane.focused or lane.awaiting_you) else None

    base = None
    if lane.focused and palette is not None:
        base = f"on {theme.style_for(mode, 'row_focused_bg')}"
    row = Text(style=base or "")

    def field(text: str, width_: int, *, style: str | None = None, right: bool = False) -> None:
        row.append(text[:width_].rjust(width_) if right else _pad(text, width_), style=style)

    row.append(" " * gutter_left(columns))
    row.append(theme.MARKERS["focus_rail"].symbol if lane.focused else " ",
               style=(theme.style_for(mode, "accent") if palette else "bold"))
    row.append(" " * max(columns.focus_rail - 1, 0))

    if lane.awaiting_you:
        glyph, color = theme.AWAITING_YOU_GLYPH, theme.AWAITING_YOU_COLOR
    elif lane.lease_holder:
        # Task.status is still "ready" — the graph is satisfied — but a
        # held path lease is what is actually keeping it out of a slot, and
        # §2 gives that the blocked glyph. Showing "ready" here would name
        # the scheduler's bookkeeping instead of the user's problem.
        glyph = theme.TASK_STATE_GLYPHS["blocked"]
        color = theme.TASK_STATE_COLOR["blocked"]
    else:
        glyph, color = theme.TASK_STATE_GLYPHS[lane.status], theme.TASK_STATE_COLOR[lane.status]
    symbol = glyph.symbol if palette else glyph.ascii
    if spinner and palette and lane.status == "running" and not lane.awaiting_you:
        symbol = spinner
    field(symbol, columns.state_glyph,
          style=(theme.style_for(mode, color) if palette else None))

    field(_fit(id_role_label(lane.task_id, lane.role_id), columns.id_role - 1),
          columns.id_role, style=(theme.style_for(mode, primary) if palette else emphasis))

    # The focused lane's goal is the one string the eye should land on
    # first. Dark separates it by hue alone; light's heading sits close
    # enough to body text that the design adds weight there as well.
    goal_style = theme.style_for(mode, "heading") if (lane.focused and palette) else theme.style_for(mode, primary)
    if lane.focused and mode == "light":
        goal_style = f"bold {goal_style}"
    elif not palette:
        goal_style = emphasis
    # Every column clips one short of its width so the gap to the next
    # one survives: a goal that exactly filled 34 columns used to run
    # straight into the tool column with no space between them.
    field(clip_goal(lane.goal, columns.goal - 1), columns.goal, style=goal_style)

    detail, detail_token = lane_detail(lane, mode, columns.tool_target - 1)
    detail_style = theme.style_for(mode, detail_token) if palette else (
        "bold" if lane.awaiting_you else None)
    field(detail, columns.tool_target, style=detail_style)

    if columns.model is not None:
        field(format_model(lane.model, escalated=lane.escalated, width=columns.model - 1),
              columns.model, style=theme.style_for(mode, numeric) if palette else None)
    if columns.tokens is not None:
        field(format_tokens(lane.tokens), columns.tokens, right=True,
              style=theme.style_for(mode, numeric) if palette else None)
    field(format_cost(lane.cost_usd), columns.cost, right=True,
          style=theme.style_for(mode, cost_token) if palette else None)
    if columns.elapsed is not None:
        field(format_elapsed(lane.elapsed_s), columns.elapsed, right=True,
              style=theme.style_for(mode, "muted") if palette else None)

    row.append(" " * max(columns.gutter - gutter_left(columns), 0))
    if base is not None and width is not None and row.cell_len < width:
        row.append(" " * (width - row.cell_len))
    return row


def render_lease_row(lane: LaneView, columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """The indented `└─ ▤ held by a1 for 41s` line under a lease-blocked
    lane (§8: "lease conflicts are a first-class state, not a log line").

    The design's version also carries a queue position, an estimate, and
    the l/p/r keys that resolve it. None of those exist yet: LeaseManager
    has no queue to have a position in, no history to estimate from, and
    no reassignment for p/r to call. Inventing them would be exactly the
    fake progress §8 rules out, so this line reports only what is measured
    — who holds the path, and for how long.
    """
    palette = theme.palette_for(mode)
    plain = palette is None
    row = Text()
    row.append(" " * (gutter_left(columns) + 4))
    row.append(f"{theme.CONNECTORS['last']} ", style=theme.style_for(mode, "muted") if palette else None)
    head = "HELD BY" if plain else f"{theme.MARKERS['lease_held'].symbol} held by"
    row.append(f"{head} {lane.lease_holder}",
               style=theme.style_for(mode, "waiting") if palette else "bold")
    if lane.lease_held_s is not None:
        row.append(f" for {format_elapsed(lane.lease_held_s)}",
                   style=theme.style_for(mode, "muted") if palette else None)
    if lane.lease_path:
        row.append(f" · {lane.lease_path}", style=theme.style_for(mode, "muted") if palette else None)
    return row


# --- grouping ----------------------------------------------------------------
_ACTIVE_STATES: tuple[TaskStatus, ...] = ("pending", "ready", "running", "blocked")
_SETTLED_STATES: tuple[TaskStatus, ...] = ("done", "failed", "cancelled")


def group_for_display(lanes: list[LaneView], *, max_rows: int) -> list:
    """Above LANE_GROUPING_THRESHOLD agents, sort the list into the
    design's RUNNING / BLOCKED / NEEDS YOU / SETTLED bands, each under its
    own header row, and fold the settled band into one summary line per
    status so the pinned list stays bounded (§4/§8: "bounded matters as
    much as pinned"). Below the threshold every lane is shown as-is, in
    graph order.

    A scrollable lane list would mean the thing you need can be off-screen
    while pinned — the worst of both — which is why this groups instead.
    """
    if len(lanes) <= LANE_GROUPING_THRESHOLD:
        return list(lanes)

    # A focused lane always stays expanded even once it settles — the
    # cursor must never point at a row that grouping has folded away.
    # Mutually exclusive and exhaustive over TaskStatus, so no lane can
    # land in two bands or fall out of the list entirely.
    needs_you = [l for l in lanes if l.awaiting_you]
    rest = [l for l in lanes if not l.awaiting_you]
    running = [l for l in rest if l.status == "running"]
    blocked = [l for l in rest if l.status in ("blocked", "pending", "ready")]
    settled = [l for l in rest if l.status in _SETTLED_STATES and not l.focused]
    settled_focused = [l for l in rest if l.status in _SETTLED_STATES and l.focused]

    result: list = []
    for label, members, token in (
        ("RUNNING", running, "muted"),
        ("BLOCKED", blocked, "waiting"),
        ("NEEDS YOU", needs_you, "warning"),
    ):
        if members:
            result.append(GroupHeader(label, len(members), token))
            result.extend(members)
    if settled or settled_focused:
        result.append(GroupHeader("SETTLED", len(settled) + len(settled_focused), "muted"))
        result.extend(settled_focused)
        by_status: dict[TaskStatus, list[LaneView]] = {}
        for lane in settled:
            by_status.setdefault(lane.status, []).append(lane)
        for status, members in by_status.items():
            result.append(GroupSummary(status=status, count=len(members),
                                       lanes=tuple(members)))
    return result[:max_rows] if max_rows > 0 else result


def render_group_header(header: GroupHeader, columns: ColumnWidths,
                        mode: theme.ColorMode, width: int | None = None) -> Text:
    """`RUNNING 3 ─────────` — the band label, its count, and a rule
    running out to the right edge."""
    palette = theme.palette_for(mode)
    row = Text()
    row.append(" " * gutter_left(columns))
    row.append(f"{header.label} ",
               style=theme.style_for(mode, header.color_token) if palette else "bold")
    row.append(str(header.count), style=theme.style_for(mode, "text") if palette else None)
    # Stop at the right-hand gutter, like every other row in the frame —
    # a rule that ran to the terminal edge would be one column wider than
    # the lane rows it sits above.
    limit = width - (columns.gutter - gutter_left(columns)) if width is not None else None
    if limit is not None and limit > row.cell_len + 1:
        row.append(" " + "─" * (limit - row.cell_len - 1),
                   style=theme.style_for(mode, "rule") if palette else None)
    return row


def render_group_row(group: GroupSummary, columns: ColumnWidths, mode: theme.ColorMode,
                     width: int | None = None) -> Text:
    """The folded settled band: `✓ 1 done · a4 ◈ tst regression suite ·
    31.0k · $0.11`. One lane still gets named — the count alone would hide
    which task it was, and that is the whole question you ask a folded
    row. More than one and only the totals survive, which is the point of
    folding them."""
    palette = theme.palette_for(mode)
    glyph = theme.TASK_STATE_GLYPHS[group.status]
    word = theme.TASK_STATE_WORD[group.status].lower()
    row = Text()
    row.append(" " * (gutter_left(columns) + columns.focus_rail + columns.state_glyph))
    row.append(f"{glyph.symbol if palette else glyph.ascii} {group.count} {word}",
               style=(theme.style_for(mode, theme.TASK_STATE_COLOR[group.status]) if palette
                      else "bold"))
    tail = ""
    if group.count == 1 and group.lanes:
        only = group.lanes[0]
        tail = (f" · {id_role_label(only.task_id, only.role_id)} "
                f"{clip_goal(only.goal, 30)}")
    if group.lanes:
        tokens = sum(l.tokens for l in group.lanes)
        cost = sum(l.cost_usd for l in group.lanes)
        tail += f" · {format_tokens(tokens)} · {format_cost(cost)}"
    row.append(tail, style=theme.style_for(mode, "muted") if palette else None)
    return row
