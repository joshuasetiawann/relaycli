"""Pure layout math for the pinned-frame terminal UI.

Source: docs/design/DESIGN_TOKENS.md §4 (column allocation + vertical row
budget). No terminal I/O here — everything takes plain ints in and returns
plain data out, so it's testable without a real Console or TTY.
"""

from __future__ import annotations

from dataclasses import dataclass

CANONICAL_WIDTH = 120
MINIMUM_WIDTH = 80

# Vertical row budget, 24-row terminal @ 120 cols (§4).
STATUS_BAR_ROWS = 1
LANE_LIST_MAX_ROWS = 9  # pinned; groups above LANE_GROUPING_THRESHOLD agents
INPUT_KEY_STRIP_ROWS = 2
PERMISSION_BAND_ROWS = 5  # 0 when idle, 5 when a permission/diff prompt is open
MIN_TRANSCRIPT_ROWS = 6  # floor — the lane list collapses to a strip first
LANE_GROUPING_THRESHOLD = 5  # >5 agents: settled tasks collapse to count rows


class TooNarrowError(Exception):
    """Raised by `resolve_columns` below MINIMUM_WIDTH. Carries the exact
    refusal line the design mandates (§4): a cramped lane list is explicitly
    considered worse than none, so callers should print this and stop
    rather than truncate further."""

    def __init__(self, width: int) -> None:
        self.width = width
        super().__init__(f"relaycli needs {MINIMUM_WIDTH} columns (have {width})")


@dataclass(frozen=True)
class ColumnWidths:
    """Resolved per-field widths for one lane row at a given terminal
    width. None means the field is dropped entirely at this width."""

    focus_rail: int
    state_glyph: int
    id_role: int
    goal: int
    tool_target: int
    model: int | None
    tokens: int | None
    cost: int
    elapsed: int | None
    gutter: int


# §4's table verbatim. Cut order at 80 columns is elapsed -> tokens -> model
# (cost is never cut); at exactly 80 all three are already gone, so there is
# no intermediate "cut two of three" width to represent separately.
_120 = ColumnWidths(focus_rail=2, state_glyph=2, id_role=10, goal=34, tool_target=30,
                     model=15, tokens=8, cost=7, elapsed=7, gutter=2)
_80 = ColumnWidths(focus_rail=2, state_glyph=2, id_role=10, goal=29, tool_target=23,
                    model=None, tokens=None, cost=7, elapsed=None, gutter=2)


def resolve_columns(width: int) -> ColumnWidths:
    """Pick the column layout for terminal `width`. Raises TooNarrowError
    below 80 columns — the caller must refuse to draw lanes entirely, not
    call this to get a further-shrunk layout."""
    if width < MINIMUM_WIDTH:
        raise TooNarrowError(width)
    if width >= CANONICAL_WIDTH:
        return _120
    return _80


def lane_list_rows(agent_count: int, *, available_rows: int) -> int:
    """Rows the pinned lane list should occupy: one per agent up to
    LANE_LIST_MAX_ROWS, floored so at least MIN_TRANSCRIPT_ROWS survives for
    the transcript once the other pinned regions are subtracted — "the lane
    list collapses to a strip first" (§4)."""
    wanted = min(max(agent_count, 0), LANE_LIST_MAX_ROWS)
    if wanted == 0:
        return 0
    ceiling = max(available_rows - MIN_TRANSCRIPT_ROWS, 1)
    return min(wanted, ceiling)


def transcript_rows(total_rows: int, *, lane_rows: int, permission_band_open: bool) -> int:
    """Rows left for the scrolling transcript after every pinned region
    (§4's budget table). Never negative — a terminal too short even for the
    floor is a refusal decision for the caller, not something to hide here."""
    used = (STATUS_BAR_ROWS + lane_rows + INPUT_KEY_STRIP_ROWS
            + (PERMISSION_BAND_ROWS if permission_band_open else 0))
    return max(total_rows - used, 0)
