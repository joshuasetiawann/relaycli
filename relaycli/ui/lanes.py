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
    model: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    focused: bool = False
    awaiting_you: bool = False


@dataclass(frozen=True)
class GroupSummary:
    """A collapsed row standing in for N settled (done/failed/cancelled)
    lanes once the list exceeds LANE_GROUPING_THRESHOLD agents — see
    `group_for_display`."""

    status: TaskStatus
    count: int


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
    """Adaptive precision so `$` + digits fits the 7-column budget that's
    "never cut" (§4) across the range a single task realistically costs.
    Beyond $10k the column simply widens rather than truncating a dollar
    figure — silently lying about spend is worse than a ragged column."""
    magnitude = abs(usd)
    if magnitude < 10:
        return f"${usd:.4f}"
    if magnitude < 100:
        return f"${usd:.3f}"
    if magnitude < 1000:
        return f"${usd:.2f}"
    if magnitude < 10_000:
        return f"${usd:.1f}"
    return f"${usd:.0f}"


def format_elapsed(seconds: float) -> str:
    """12s, 1m05s, 1h02m — fits the 7-column budget through multi-hour runs."""
    total = max(int(seconds), 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _pad(text: str, width: int) -> str:
    return text[:width].ljust(width)


# --- row rendering -----------------------------------------------------------
def render_lane_row(lane: LaneView, columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """One lane row, ready to print/update in the pinned list. `mode`
    selects dark/light palette colors, or NO_COLOR's glyph+bold+word
    fallback (§4: "no fact present in the color version is lost")."""
    palette = theme.palette_for(mode)
    row = Text()

    def field(text: str, width: int, *, style: str | None = None, right: bool = False) -> None:
        content = text[:width].rjust(width) if right else _pad(text, width)
        row.append(content, style=style)
        row.append(" ")  # §4 specifies widths, not inter-field gaps; without this,
        # right-justified numeric columns collide (e.g. "125.3k$1.2345").

    row.append(theme.MARKERS["focus_rail"].symbol if lane.focused else " ",
               style=palette.accent if palette else None)
    row.append(_pad("", columns.focus_rail - 1))

    if lane.awaiting_you:
        glyph, color = theme.AWAITING_YOU_GLYPH, theme.AWAITING_YOU_COLOR
    else:
        glyph, color = theme.TASK_STATE_GLYPHS[lane.status], theme.TASK_STATE_COLOR[lane.status]
    symbol = glyph.symbol if palette else glyph.ascii
    field(symbol, columns.state_glyph, style=(getattr(palette, color) if palette else None))

    mark = theme.ROLE_MARKS.get(lane.role_id)
    id_role = f"{lane.task_id[:6]}·{mark.code}" if mark else lane.task_id[: columns.id_role]
    field(id_role, columns.id_role, style=palette.muted if palette else None)

    style = "bold" if (palette and (lane.focused or lane.awaiting_you)) else None
    field(clip_goal(lane.goal, columns.goal), columns.goal, style=style)

    # DARK_SURFACE's text_secondary is documented as covering exactly this
    # ("secondary body text, captions, tool targets"); light has no such
    # extra token, so it falls back to the core muted token there.
    if mode == "dark":
        tool_target_style = theme.DARK_SURFACE["text_secondary"]
    elif palette:
        tool_target_style = palette.muted
    else:
        tool_target_style = None
    field(format_tool_target(lane.tool, lane.target, columns.tool_target),
          columns.tool_target, style=tool_target_style)

    if columns.model is not None:
        field(lane.model, columns.model, style=palette.muted if palette else None)
    if columns.tokens is not None:
        field(format_tokens(lane.tokens), columns.tokens, right=True,
              style=palette.muted if palette else None)
    field(format_cost(lane.cost_usd), columns.cost, right=True,
          style=palette.warning if palette else None)
    if columns.elapsed is not None:
        field(format_elapsed(lane.elapsed_s), columns.elapsed, right=True,
              style=palette.muted if palette else None)

    row.append(" " * max(columns.gutter - 1, 0))

    if not palette and (lane.awaiting_you or lane.status == "blocked"):
        word = theme.AWAITING_YOU_WORD if lane.awaiting_you else theme.TASK_STATE_WORD[lane.status]
        row.append(f" — {word}", style="bold")

    return row


def group_for_display(lanes: list[LaneView], *, max_rows: int) -> list[LaneView | GroupSummary]:
    """Above LANE_GROUPING_THRESHOLD agents, keep every still-active lane
    (pending/ready/running/blocked — the ones worth watching) expanded, and
    collapse settled ones (done/failed/cancelled) into one count row per
    status so the pinned list stays bounded (§4/§8: "bounded matters as
    much as pinned"). Below the threshold, every lane is shown as-is. This
    grouping rule is this implementation's own interpretation — the source
    design specifies *that* grouping happens above 5 agents, not the exact
    algorithm."""
    if len(lanes) <= LANE_GROUPING_THRESHOLD:
        return list(lanes)

    # A focused lane always stays expanded even once it settles — the
    # cursor must never point at a row that grouping has folded away.
    active = [l for l in lanes
              if l.status in ("pending", "ready", "running", "blocked") or l.focused]
    settled = [l for l in lanes
               if l.status in ("done", "failed", "cancelled") and not l.focused]

    counts: dict[TaskStatus, int] = {}
    for lane in settled:
        counts[lane.status] = counts.get(lane.status, 0) + 1
    groups = [GroupSummary(status=status, count=count) for status, count in counts.items()]

    result: list[LaneView | GroupSummary] = [*active, *groups]
    return result[:max_rows] if max_rows > 0 else result


def render_group_row(group: GroupSummary, columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """The collapsed summary row a GroupSummary renders as, e.g. `✓ done x12`."""
    palette = theme.palette_for(mode)
    glyph = theme.TASK_STATE_GLYPHS[group.status]
    symbol = glyph.symbol if palette else glyph.ascii
    word = theme.TASK_STATE_WORD[group.status].lower()
    row = Text()
    row.append(" " * columns.focus_rail)
    row.append(_pad(symbol, columns.state_glyph),
               style=(getattr(palette, theme.TASK_STATE_COLOR[group.status]) if palette else None))
    row.append(f"{word} ×{group.count}", style=palette.muted if palette else None)
    return row
