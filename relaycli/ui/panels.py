"""The full-screen panels — plan review, diff review, the permission band,
models & cost, roles & skills, and focus mode.

Source: `RelayCLI Terminal UI.dc.html` §06–§10, checked character for
character by tests/test_design_parity.py. Pure like frame.py and lanes.py:
every function takes plain data and returns rich Text rows, so a screen can
be asserted without a terminal, a Scheduler or a clock.

These are the screens §04's column table does not cover, so each one carries
its own column widths, read off the source's own `width:Nch` spans. They are
stated as module constants rather than inline numbers, because a panel's
columns are the one thing in it that must not drift row to row.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from relaycli.ui import theme
from relaycli.ui.frame import KeyHint, render_hint, render_hints, _pad_between
from relaycli.ui.lanes import format_cost, format_tokens, gutter_left, id_role_label
from relaycli.ui.layout import ColumnWidths


def _tint(mode: theme.ColorMode, token: str) -> str | None:
    return theme.style_for(mode, token) if theme.palette_for(mode) else None


def _row(columns: ColumnWidths, indent: int = 0) -> Text:
    """A row that starts where every other row in the frame starts."""
    row = Text()
    row.append(" " * (gutter_left(columns) + indent))
    return row


def _limit(columns: ColumnWidths, width: int) -> int:
    """The rightmost column a row may reach — §4's gutter, one col each side."""
    return max(width - (columns.gutter - gutter_left(columns)), 1)


def _field(row: Text, text: str, span: int, style: str | None = None,
           *, right: bool = False) -> None:
    """One fixed-width cell, clipped to its own column like the design's
    `overflow:hidden` flex items."""
    row.append(text[:span].rjust(span) if right else text[:span].ljust(span), style=style)


# --- shared panel chrome ------------------------------------------------------
def render_panel_header(title: str, subtitle: list[tuple[str, str]],
                        tail: Text | None, columns: ColumnWidths,
                        mode: theme.ColorMode, width: int) -> Text:
    """`▌relaycli  PLAN REVIEW  9 tasks · 4 parallel lanes · est $1.12`

    Every full-screen panel opens with this row: the brand mark, the panel's
    name in heading weight, and a subtitle of already-measured facts. The
    subtitle arrives as (text, token) fragments because the numbers in it are
    highlighted and the prose around them is not — and which is which is the
    panel's business, not this function's.
    """
    palette = theme.palette_for(mode)
    rail = theme.MARKERS["brand_rail"]
    row = _row(columns)
    row.append(f"{rail.symbol if palette else rail.ascii}relaycli",
               style=f"bold {_tint(mode, 'accent')}" if palette else "bold")
    row.append("  ", style=_tint(mode, "muted"))
    row.append(title, style=_tint(mode, "heading") if palette else "bold")
    if subtitle:
        row.append("  ", style=_tint(mode, "muted"))
        for text, token in subtitle:
            row.append(text, style=_tint(mode, token))
    return _pad_between(row, tail, _limit(columns, width)) if tail else row


def render_action_row(hints: list[KeyHint], columns: ColumnWidths,
                      mode: theme.ColorMode) -> Text:
    """A panel's footer of keys, indented under the body like the key strip."""
    row = _row(columns, 1)
    row.append_text(render_hints(hints, mode))
    return row


def render_box_top(label: list[tuple[str, str]], columns: ColumnWidths,
                   mode: theme.ColorMode, width: int, *, indent: int = 0,
                   token: str = "rule", closed: bool = False) -> Text:
    """`╭─ src/lease/queue.ts:81` — the opening line of the two things §12
    lets draw a box: the permission band and an inline diff preview.

    `closed=True` runs the rule out to the right edge and closes the corner,
    which is what the permission band does; the diff preview leaves it open,
    because it is a bracket around a few lines rather than a frame demanding
    an answer.
    """
    row = _row(columns, indent)
    row.append("╭─ ", style=_tint(mode, token))
    for text, tok in label:
        row.append(text, style=_tint(mode, tok))
    if closed:
        span = max(_limit(columns, width) - row.cell_len - 1, 0)
        row.append("─" * span, style=_tint(mode, token))
        row.append("╮", style=_tint(mode, token))
    return row


def render_box_bottom(label: list[tuple[str, str]], columns: ColumnWidths,
                      mode: theme.ColorMode, width: int, *, indent: int = 0,
                      token: str = "rule", closed: bool = False) -> Text:
    """`╰─ d full diff  u undo this edit`, or a closed `╰───────╯`."""
    row = _row(columns, indent)
    row.append("╰", style=_tint(mode, token))
    if closed:
        span = max(_limit(columns, width) - row.cell_len - 1, 0)
        row.append("─" * span, style=_tint(mode, token))
        row.append("╯", style=_tint(mode, token))
        return row
    row.append("─ ", style=_tint(mode, token))
    for text, tok in label:
        row.append(text, style=_tint(mode, tok))
    return row


def render_box_line(body: Text, columns: ColumnWidths, mode: theme.ColorMode,
                    width: int, *, indent: int = 0, token: str = "rule",
                    closed: bool = False, gap: int = 1) -> Text:
    """`│ bash  gh api …` — one line inside a box."""
    row = _row(columns, indent)
    row.append("│" + " " * gap, style=_tint(mode, token))
    row.append_text(body)
    if closed:
        span = max(_limit(columns, width) - row.cell_len - 1, 0)
        row.append(" " * span)
        row.append("│", style=_tint(mode, token))
    return row


# --- §06 focus mode -----------------------------------------------------------
@dataclass(frozen=True)
class OtherAgent:
    """One chip in the OTHERS strip: the state glyph, the lane id, and the
    single fact that says why you would switch to it."""

    task_id: str
    state: str            # a TaskStatus, or "awaiting_you"
    note: str = ""


def render_others_strip(others: list[OtherAgent], columns: ColumnWidths,
                        mode: theme.ColorMode, width: int, *,
                        spinner: str | None = None,
                        tail: list[KeyHint] | None = None) -> Text:
    """`OTHERS ⠋ a5 drizzle:gen 3m01s  ⊘ a2 lease queue.ts  ? a3 needs you`

    Focus mode gives one agent the whole width, so the others have to fit in
    one row — otherwise focusing would mean losing sight of the run, which is
    the one thing the pinned lane list exists to prevent.
    """
    palette = theme.palette_for(mode)
    row = _row(columns)
    row.append("OTHERS ", style=_tint(mode, "muted"))
    for index, other in enumerate(others):
        if index:
            row.append("  ", style=_tint(mode, "muted"))
        if other.state == "awaiting_you":
            glyph, token = theme.AWAITING_YOU_GLYPH, theme.AWAITING_YOU_COLOR
        else:
            glyph = theme.TASK_STATE_GLYPHS[other.state]
            token = theme.TASK_STATE_COLOR[other.state]
        symbol = glyph.symbol if palette else glyph.ascii
        if spinner and palette and other.state == "running":
            symbol = spinner
        row.append(f"{symbol} {other.task_id}", style=_tint(mode, token))
        if other.note:
            row.append(f" {other.note}", style=_tint(mode, "muted"))
    hints = render_hints(tail, mode) if tail else None
    return _pad_between(row, hints, _limit(columns, width)) if hints else row


def render_focus_header(task_id: str, role_id: str, goal: str, *, model: str,
                        provider: str, tokens_in: int, tokens_out: int,
                        cost_usd: float, elapsed: str, columns: ColumnWidths,
                        mode: theme.ColorMode, width: int) -> Text:
    """`▌ a1 ▣ bnd  implement lease queue + fairness … $0.42  1m12s` — the
    focused agent's own header row, carrying the numbers its lane row was
    carrying before it took the whole screen."""
    palette = theme.palette_for(mode)
    rail = theme.MARKERS["focus_rail"]
    row = _row(columns)
    row.append(f"{rail.symbol if palette else rail.ascii} "
               f"{id_role_label(task_id, role_id)}",
               style=f"bold {_tint(mode, 'accent')}" if palette else "bold")
    row.append(f"  {goal}", style=_tint(mode, "heading"))

    right = Text()
    right.append(f"{model} · {provider}", style=_tint(mode, "text_secondary"))
    right.append(f"  {format_tokens(tokens_in)}↑ {format_tokens(tokens_out)}↓  ",
                 style=_tint(mode, "muted"))
    right.append(format_cost(cost_usd), style=_tint(mode, "text"))
    right.append(f"  {elapsed}", style=_tint(mode, "muted"))
    return _pad_between(row, right, _limit(columns, width))


@dataclass(frozen=True)
class GraphNode:
    task_id: str
    role_code: str
    state: str
    note: str = ""


def render_graph_line(before: list[GraphNode], here: GraphNode,
                      after: list[GraphNode], columns: ColumnWidths,
                      mode: theme.ColorMode, *, spinner: str | None = None) -> Text:
    """`graph  ✓ t2 arc lease design ──▸ ⠋ t4 bnd (you are here) ──▸ ⊘ t6 fnd`

    §12 rejects a drawn DAG: one row of connectors says what six rows of
    box-and-arrow would, and it survives a task having two parents. This is
    that row for the focused task — what it waited on, and what waits on it.
    """
    palette = theme.palette_for(mode)
    row = _row(columns, 1)
    row.append("graph  ", style=_tint(mode, "muted"))

    def node(item: GraphNode, suffix: str = "") -> None:
        glyph = theme.TASK_STATE_GLYPHS[item.state]
        symbol = glyph.symbol if palette else glyph.ascii
        if spinner and palette and item.state == "running":
            symbol = spinner
        row.append(f"{symbol} {item.task_id} {item.role_code}",
                   style=_tint(mode, theme.TASK_STATE_COLOR[item.state]))
        if item.note:
            row.append(f" {item.note}", style=_tint(mode, "muted"))
        if suffix:
            row.append(suffix, style=_tint(mode, "muted"))

    for item in before:
        node(item, " ──▸ ")
    node(here, " ──▸ " if after else "")
    for index, item in enumerate(after):
        if index:
            row.append(", ", style=_tint(mode, "muted"))
        node(item)
    return row


def render_leases_line(held: list[tuple[str, str]], waiters: list[str],
                       columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """`leases ▤ src/lease/queue.ts held 1m04s · … · 1 waiter (a2)` — what
    this agent is holding and who is behind it, which is the other half of
    the answer to "why is a2 not moving"."""
    row = _row(columns, 1)
    row.append("leases ", style=_tint(mode, "muted"))
    for index, (path, elapsed) in enumerate(held):
        if index:
            row.append(" · ", style=_tint(mode, "muted"))
        row.append(f"{theme.MARKERS['lease_held'].symbol} {path}"
                   if theme.palette_for(mode) else f"HELD {path}",
                   style=_tint(mode, "waiting"))
        row.append(f" held {elapsed}", style=_tint(mode, "muted"))
    if waiters:
        row.append(f" · {len(waiters)} waiter{'' if len(waiters) == 1 else 's'} "
                   f"({', '.join(waiters)})", style=_tint(mode, "muted"))
    return row


def render_branch_rule(columns: ColumnWidths, mode: theme.ColorMode, width: int) -> Text:
    """`├──────────` — an unlabelled transcript divider. Focus mode has
    already named the agent two rows up, so the label would be the third
    place on one screen saying `a1`."""
    row = _row(columns)
    row.append("├", style=_tint(mode, "rule"))
    row.append("─" * max(_limit(columns, width) - row.cell_len, 0),
               style=_tint(mode, "rule"))
    return row


DIFF_PREVIEW_INDENT = 9  # STAMP_WIDTH — the preview hangs under the tool line


def render_diff_preview(path: str, lines: list[tuple[str, str]],
                        hints: list[KeyHint], columns: ColumnWidths,
                        mode: theme.ColorMode, width: int) -> list[Text]:
    """The boxed few lines an edit changed, inline in the transcript.

    One of §12's two exceptions to "nothing draws a box", and it earns it the
    same way the permission band does: it is ephemeral, and it is asking
    whether to keep the change.
    """
    rows = [render_box_top([(f"{path}", "muted")], columns, mode, width,
                           indent=DIFF_PREVIEW_INDENT)]
    for marker, text in lines:
        body = Text()
        token = {"+": "success", "−": "danger", "-": "danger"}.get(marker, "text_secondary")
        body.append(f"{marker} " if marker.strip() else "  ", style=_tint(mode, token))
        body.append(text, style=_tint(mode, token))
        rows.append(render_box_line(body, columns, mode, width,
                                    indent=DIFF_PREVIEW_INDENT))
    rows.append(render_box_bottom([], columns, mode, width,
                                  indent=DIFF_PREVIEW_INDENT))
    rows[-1].append_text(render_hints(hints, mode))
    return rows


# --- §07 plan review ----------------------------------------------------------
# The source's own column spans. TASK is wide because it carries the tree
# connectors as well as the label, and indentation *is* the dependency (§12).
PLAN_TASK, PLAN_GOAL, PLAN_MODEL = 24, 40, 18
PLAN_TIER, PLAN_EST, PLAN_TIME = 10, 9, 9


@dataclass(frozen=True)
class PlanRow:
    """One task in the plan, before anything has run. Every number here is an
    estimate the router produced, which is why they are strings: a plan that
    prints `$0.00` for a local model and `~2m` for a cloud one is reporting
    two different kinds of guess, and rounding them into floats here would
    invite a caller to add them up."""

    task_id: str
    role_id: str
    goal: str
    model: str
    tier: str                 # "local" | "cloud" | "dropped"
    est_cost: str
    est_time: str
    depth: int = 0            # how deep in the DAG; 0 is a root
    connector: str = ""       # "branch" | "last" | "" for a root
    ancestors: tuple[bool, ...] = ()   # for each level above, is the trunk open
    flag: str = ""            # "" | "warning" | "cancelled"
    escalation_risk: bool = False


def _plan_prefix(row: PlanRow) -> str:
    """The tree drawing for one plan row: an open trunk for every ancestor
    that still has siblings below it, then this row's own connector."""
    out = "".join(f"{theme.CONNECTORS['pipe']}  " if open_ else "   "
                  for open_ in row.ancestors)
    if row.connector:
        out += f"{theme.CONNECTORS[row.connector]} "
    return out


def render_plan_columns(columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """`TASK  GOAL  MODEL  TIER  EST  TIME` — the only column-header row in
    the product, because the plan screen is the only table whose columns are
    not already named by the shape of what is in them."""
    row = _row(columns, 2)
    muted = _tint(mode, "muted")
    for label, span, right in (("TASK", PLAN_TASK, False), ("GOAL", PLAN_GOAL, False),
                               ("MODEL", PLAN_MODEL, False), ("TIER", PLAN_TIER, False),
                               ("EST", PLAN_EST, True), ("TIME", PLAN_TIME, True)):
        _field(row, label, span, muted, right=right)
    return row


def render_plan_row(item: PlanRow, columns: ColumnWidths, mode: theme.ColorMode,
                    width: int, *, focused: bool = False) -> Text:
    """One plan row. The flag column is 2 wide and carries `!` for a task the
    router expects trouble from and `╌` for one you dropped — dropped tasks
    stay visible in cancelled styling until you run, so you can see what you
    removed and put it back with the same key."""
    palette = theme.palette_for(mode)
    dropped = item.flag == "cancelled"
    body = "muted" if dropped else ("heading" if focused else "text")
    row = Text(style=f"on {theme.style_for(mode, 'row_focused_bg')}"
               if (focused and palette) else "")
    row.append(" " * gutter_left(columns))

    if focused:
        rail = theme.MARKERS["focus_rail"]
        row.append(rail.symbol if palette else rail.ascii, style=_tint(mode, "accent"))
        row.append(" ")
    elif item.flag == "warning":
        marker = theme.AWAITING_YOU_GLYPH if palette else theme.AWAITING_YOU_GLYPH
        row.append(f"{marker.symbol} ", style=_tint(mode, "warning"))
    elif dropped:
        glyph = theme.TASK_STATE_GLYPHS["cancelled"]
        row.append(f"{glyph.symbol if palette else glyph.ascii} ", style=_tint(mode, "muted"))
    else:
        row.append("  ")

    prefix = _plan_prefix(item)
    label = id_role_label(item.task_id, item.role_id)
    row.append(prefix[:PLAN_TASK], style=_tint(mode, "muted"))
    _field(row, label, max(PLAN_TASK - len(prefix), 0), _tint(mode, body))
    _field(row, item.goal, PLAN_GOAL, _tint(mode, body))
    _field(row, item.model, PLAN_MODEL,
           _tint(mode, "muted" if dropped else "text_secondary"))

    tier = item.tier
    tier_token = "muted" if dropped else ("success" if tier == "local" else "accent")
    if item.escalation_risk:
        tier = f"{tier} {theme.MARKERS['escalation'].symbol}?"
        tier_token = "warning"
    _field(row, tier, PLAN_TIER, _tint(mode, tier_token))
    _field(row, item.est_cost, PLAN_EST, _tint(mode, "muted" if dropped else "text"),
           right=True)
    _field(row, item.est_time, PLAN_TIME, _tint(mode, "muted"), right=True)
    if palette and focused and row.cell_len < width:
        row.append(" " * (width - row.cell_len))
    return row


def render_plan_note(text: str, columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """`▲ 8B model has failed this task type twice this week …` — the reason
    a plan row is flagged, indented under it. The forecast is the point: a
    blocked state later should be a prediction coming true, not a surprise."""
    marker = theme.MARKERS["escalation"]
    row = _row(columns, 4)
    row.append(f"{marker.symbol if theme.palette_for(mode) else marker.ascii} {text}",
               style=_tint(mode, "warning"))
    return row


def render_contention_forecast(conflict: str, consequence: str,
                              columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """`contention forecast: ▥ t3 and t5 both write src/lease/* — …`"""
    row = _row(columns, 1)
    row.append("contention forecast: ", style=_tint(mode, "muted"))
    row.append(f"{theme.MARKERS['lease_waiting'].symbol} {conflict}"
               if theme.palette_for(mode) else f"WAIT {conflict}",
               style=_tint(mode, "waiting"))
    row.append(f" — {consequence}", style=_tint(mode, "muted"))
    return row


def render_plan_total(running: int, dropped: int, total_usd: float, budget_usd: float,
                     columns: ColumnWidths, mode: theme.ColorMode) -> Text:
    """`8 tasks will run · 1 dropped · total $0.93 of the $3.00 session budget`

    Cost is estimated per task and totalled before a token is spent — which
    is the whole reason this screen exists rather than a y/n prompt.
    """
    row = _row(columns, 1)
    row.append(f"{running} task{'' if running == 1 else 's'} will run · "
               f"{dropped} dropped · total ", style=_tint(mode, "muted"))
    row.append(format_cost(total_usd), style=_tint(mode, "text"))
    row.append(" of the ", style=_tint(mode, "muted"))
    row.append(format_cost(budget_usd), style=_tint(mode, "text"))
    row.append(" session budget", style=_tint(mode, "muted"))
    return row
