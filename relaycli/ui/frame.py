"""Pure rendering for the frame around the lane list — status bar, rules,
transcript, key strip.

Source: docs/design/DESIGN_TOKENS.md and the Claude Design source it was
extracted from, `RelayCLI Terminal UI.dc.html` §03 (hero screen), §04 (80
columns / light / NO_COLOR) and §05 (density). Pure like keymap.py and
lanes.py: everything takes plain data and returns rich Text, so each row
is assertable without a TTY. ui/live.py owns the terminal half and is the
only place that reads a Console, a Scheduler or the clock.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass, field, replace
from pathlib import Path

from rich.text import Text

from relaycli.ui import theme
from relaycli.ui.lanes import format_cost, format_tokens, gutter_left, id_role_label
from relaycli.ui.layout import ColumnWidths

BUDGET_SEGMENTS = 5

# Above this share of the budget the meter (and the spend figure with it)
# stops being reassuring and turns warning-coloured; at CRITICAL it also
# picks up §2's escalation marker. Both thresholds are read off the
# source's own screens: 24% renders green, 62% amber, 90% amber + ▲.
BUDGET_WARN = 0.60
BUDGET_CRITICAL = 0.90


@dataclass(frozen=True)
class StatusBarData:
    """Everything §03's status bar shows. Assembled by ui/live.py from the
    Scheduler, Settings and the repository; nothing is derived here that
    the caller could get wrong differently."""

    cwd: Path | None = None
    branch: str | None = None
    dirty: int = 0
    permission_mode: str = ""
    agents: int = 0
    tokens: int = 0
    spent_usd: float = 0.0
    limit_usd: float | None = None


def budget_fraction(data: StatusBarData) -> float | None:
    """Share of the session dollar budget spent, or None when the user set
    no limit. §8: the budget meter is the one bar in the product precisely
    because it has a real, user-set denominator — with no limit there is
    no denominator, so there is no bar."""
    if not data.limit_usd:
        return None
    return max(data.spent_usd / data.limit_usd, 0.0)


def budget_meter(fraction: float) -> str:
    """`▮▮▮▯▯` — five discrete fifths, never a fake percent (§2).

    A segment fills only once that whole fifth is spent, so a filled
    segment always means "at least this much is gone" and the meter can
    never overstate what is left. Any non-zero spend lights the first
    segment, because "you have started spending" is itself the fact the
    first fifth is there to carry. (One screen in the source draws 24% as
    two segments rather than one; the mockups are hand-set and disagree
    with each other on the rounding. The never-overstate rule is the one
    that is safe to be wrong in.)
    """
    filled = min(int(math.floor(max(fraction, 0.0) * BUDGET_SEGMENTS)), BUDGET_SEGMENTS)
    if fraction > 0:
        filled = max(filled, 1)
    return (theme.BUDGET_METER["filled"] * filled
            + theme.BUDGET_METER["empty"] * (BUDGET_SEGMENTS - filled))


def budget_meter_ascii(fraction: float) -> str:
    """`[###--]` — the NO_COLOR spelling from §04's monochrome pass."""
    filled = min(int(math.floor(max(fraction, 0.0) * BUDGET_SEGMENTS)), BUDGET_SEGMENTS)
    if fraction > 0:
        filled = max(filled, 1)
    return "[" + "#" * filled + "-" * (BUDGET_SEGMENTS - filled) + "]"


def short_path(path: Path | None) -> str:
    """`~/src/relay-api`. Absolute paths under $HOME contract to `~`; the
    status bar has one line to say where you are, and the home prefix is
    the part of it you already know.

    The contracted form stays POSIX all the way through — `as_posix()`, not
    `str()`. `~` is itself a POSIX spelling of home, so a Windows `str()`
    tail produced `~/src\\relay-api`: half one convention, half the other,
    and a path in neither. A path that is *not* under home is printed with
    the platform's own separator, because that one is a real absolute path
    you might paste into your own shell.
    """
    if path is None:
        return ""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def _pad_between(left: Text, right: Text, width: int) -> Text:
    """Left half, right half, and whatever space is left between them —
    the design's `flex:1` spacer.

    When the two halves cannot both fit, the left one gives way: it is a
    path and a branch name, which you can still recognise from a prefix,
    while the right one is the agent count and the spend, which you
    cannot. Overflowing instead would wrap the status bar onto a second
    line and push the whole frame down a row on every repaint.
    """
    out = Text()
    room = max(width - right.cell_len - 1, 0)
    if left.cell_len > room:
        left = left.copy()
        left.truncate(room)
    out.append_text(left)
    out.append(" " * max(width - left.cell_len - right.cell_len, 1))
    out.append_text(right)
    return out


def render_status_bar(data: StatusBarData, columns: ColumnWidths,
                      mode: theme.ColorMode, width: int) -> Text:
    """§03's status bar, or §04's compact 80-column variant.

    Which one is chosen follows the same width decision the lane row
    makes — `columns.model is None` is exactly "we are at 80 columns" —
    so the bar can never be drawing the wide form over narrow lanes.
    """
    palette = theme.palette_for(mode)
    compact = columns.model is None

    def tint(token: str) -> str | None:
        return theme.style_for(mode, token) if palette else None

    def build_left(*, basename_only: bool, with_mode: bool) -> Text:
        left = Text()
        left.append(" " * gutter_left(columns))
        rail = theme.MARKERS["focus_rail"]
        name = "relay" if compact else "relaycli"
        left.append(f"{rail.symbol if palette else rail.ascii}{name}",
                    style=f"bold {tint('accent')}" if palette else "bold")

        cwd = short_path(data.cwd)
        if cwd:
            left.append(" " + (Path(cwd).name if basename_only else cwd), style=tint("text"))
        if data.branch:
            left.append(" " if compact else "  git:", style=tint("muted"))
            left.append(data.branch, style=tint("text"))
            if data.dirty:
                # NO_COLOR has no ± in its ASCII pass — §04's monochrome
                # screen spells the same fact "+3".
                sign = "±" if palette else "+"
                left.append(("" if compact else " ") + f"{sign}{data.dirty}",
                            style=tint("warning"))
            elif not compact:
                left.append("  clean", style=tint("muted"))
        if data.permission_mode and with_mode:
            left.append("  mode:", style=tint("muted"))
            left.append(data.permission_mode, style=tint("text"))
        return left

    right = Text()
    if compact:
        right.append(f"{data.agents}a", style=tint("running"))
    else:
        right.append(f"{data.agents} agent{'' if data.agents == 1 else 's'}",
                     style=tint("running"))
        right.append(f"  {format_tokens(data.tokens)} tok", style=tint("muted"))

    fraction = budget_fraction(data)
    critical = fraction is not None and fraction >= BUDGET_CRITICAL
    right.append("  ")
    right.append(format_cost(data.spent_usd),
                 style=tint("warning") if critical else tint("text"))
    if data.limit_usd and not compact:
        right.append(f" / {data.limit_usd:.2f}", style=tint("muted"))
    if fraction is not None:
        meter = budget_meter(fraction) if palette else budget_meter_ascii(fraction)
        percent = "" if compact else f" {round(fraction * 100)}%"
        marker = f" {theme.MARKERS['escalation'].symbol}" if (critical and palette) else ""
        right.append(("  " if not compact else " ") + meter + percent + marker,
                     style=tint("warning") if fraction >= BUDGET_WARN else tint("success"))

    # Give up detail in the order it can be recovered from elsewhere: the
    # permission mode is a setting you chose, the working directory is
    # where you already are, and the branch and dirty count are neither.
    # `_pad_between`'s hard truncate is the last resort behind these.
    span = max(width - gutter_left(columns), 1)
    room = max(span - right.cell_len - 1, 0)
    for basename_only, with_mode in ((compact, not compact), (True, False), (True, False)):
        left = build_left(basename_only=basename_only, with_mode=with_mode)
        if left.cell_len <= room:
            break
    return _pad_between(left, right, span)


def render_rule(columns: ColumnWidths, mode: theme.ColorMode, width: int) -> Text:
    """The full-width `────` divider under the status bar and above the
    input. Drawn in `rule`, the token §1 reserves for dividers only."""
    palette = theme.palette_for(mode)
    span = max(width - columns.gutter, 1)
    row = Text()
    row.append(" " * gutter_left(columns))
    row.append("─" * span, style=theme.style_for(mode, "rule") if palette else None)
    return row


def render_transcript_header(columns: ColumnWidths, mode: theme.ColorMode, width: int,
                             *, label: str, merged: bool) -> Text:
    """`├─ a1 ▣ bnd transcript ────────────  focused·merged m` — names the
    stream you are reading and which of the two modes you are in, since
    the two look identical once you are three lines into either."""
    palette = theme.palette_for(mode)
    row = Text()
    row.append(" " * gutter_left(columns))
    row.append("├", style=theme.style_for(mode, "rule") if palette else None)
    row.append("─ ", style=theme.style_for(mode, "muted") if palette else None)
    row.append(label, style=theme.style_for(mode, "accent") if palette else "bold")
    row.append(" transcript ", style=theme.style_for(mode, "muted") if palette else None)

    tail = Text()
    tail.append(" focused", style=theme.style_for(mode, "muted" if merged else "text") if palette else None)
    tail.append("·", style=theme.style_for(mode, "muted") if palette else None)
    tail.append("merged ", style=theme.style_for(mode, "text" if merged else "muted") if palette else None)
    tail.append("m", style=theme.style_for(mode, "accent") if palette else "bold")

    span = max(width - columns.gutter - row.cell_len - tail.cell_len + gutter_left(columns), 0)
    row.append("─" * span, style=theme.style_for(mode, "rule") if palette else None)
    row.append_text(tail)
    return row


@dataclass
class KeyHint:
    """One `tab lane` pair in the bottom strip: the key, then what it
    does. `badge` is the count the design hangs off `d diffs 1` and
    `y/n answer a3` — the thing that is waiting for you."""

    key: str
    label: str
    badge: str = ""


def render_key_strip(hints: list[KeyHint], columns: ColumnWidths,
                     mode: theme.ColorMode, width: int) -> Text:
    """§03's bottom strip. Keys in accent, labels muted, badges warning —
    a badge is always something waiting on you, which is why it borrows
    the same hue as the `?` awaiting-you state rather than a new one."""
    palette = theme.palette_for(mode)

    def one(hint: KeyHint) -> Text:
        piece = Text()
        piece.append(hint.key if palette else f"[{hint.key}]",
                     style=theme.style_for(mode, "accent") if palette else None)
        piece.append(f" {hint.label}", style=theme.style_for(mode, "muted") if palette else None)
        if hint.badge:
            piece.append(f" {hint.badge}",
                         style=theme.style_for(mode, "warning") if palette else "bold")
        return piece

    row = Text()
    row.append(" " * (gutter_left(columns) + 1))
    limit = max(width - columns.gutter, 1)
    # The last hint is `? keys`, the one that tells you what was left out,
    # so it is the one that survives when the strip has to be cut short.
    # Reserving its width up front is why the strip can never be the row
    # that overflows the terminal — which would wrap and take the whole
    # pinned frame with it.
    tail = one(hints[-1]) if hints else Text()
    for index, hint in enumerate(hints[:-1] if hints else []):
        piece = one(hint)
        gap = 2 if index else 0
        if row.cell_len + gap + piece.cell_len + 2 + tail.cell_len > limit:
            break
        if gap:
            row.append(" " * gap)
        row.append_text(piece)
    if hints:
        if row.cell_len > gutter_left(columns) + 1:
            row.append("  ")
        row.append_text(tail)
    return row


def render_input_row(columns: ColumnWidths, mode: theme.ColorMode, width: int,
                     *, placeholder: str, typed: str | None = None) -> Text:
    """`❯ ask, or / for commands` — the design's caret line.

    `typed=None` is the idle row: the caret and a placeholder saying what
    the line can actually do. The caret is drawn even then, because it is
    what tells you the run has not taken your terminal away from you.

    A string — including `""` — means the steer field is open, and the row
    draws what has been typed so far in the body style with a block
    cursor after it. Empty draws just the cursor, so opening the field is
    visible before the first character lands. The field is one line and
    keymap caps the note at STEER_MAX_CHARS, but a narrow terminal can
    still run out first, so the text is clipped from the *left*: the tail
    is where the cursor is, and a field that hides what you are currently
    typing is worse than one that hides what you typed a moment ago.
    """
    palette = theme.palette_for(mode)
    caret = theme.MARKERS["prompt_caret"]
    row = Text()
    row.append(" " * gutter_left(columns))
    row.append(f"{caret.symbol if palette else caret.ascii} ",
               style=theme.style_for(mode, "accent") if palette else None)
    if typed is None:
        row.append(placeholder, style=theme.style_for(mode, "caret_idle") if palette else None)
        return row
    cursor = theme.MARKERS["focus_rail"]
    cursor_text = cursor.symbol if palette else cursor.ascii
    room = max(width - row.cell_len - len(cursor_text), 0)
    row.append(typed[len(typed) - room:] if room else "",
               style=theme.style_for(mode, "text") if palette else None)
    row.append(cursor_text, style=theme.style_for(mode, "accent") if palette else None)
    return row


@dataclass(frozen=True)
class TranscriptEntry:
    """One line of the transcript. `kind` picks the colour and the shape;
    `stamp` is wall-clock `HH:MM:SS` because the transcript is the record
    you scroll back through after the fact, where elapsed means nothing."""

    stamp: str
    kind: str            # "tool" | "text" | "result" | "note"
    task_id: str = ""
    role_id: str = ""
    tool: str = ""
    target: str = ""
    text: str = ""
    ok: bool = True


_ENTRY_TOKEN = {"tool": "text_secondary", "text": "text", "result": "success", "note": "warning"}


@dataclass
class TranscriptView:
    entries: list[TranscriptEntry] = field(default_factory=list)
    merged: bool = False


def render_transcript_line(entry: TranscriptEntry, columns: ColumnWidths,
                           mode: theme.ColorMode, width: int, *, merged: bool) -> Text:
    """One transcript row. Merged mode spends a fixed 10-column prefix on
    `a1 ▣ bnd` and interleaves every agent by timestamp; focused mode
    drops the prefix and shows one stream. Both clip rather than wrap —
    §6's "streaming text appends, never re-lays-out": a wrapped line would
    reflow every row above it on the next token."""
    palette = theme.palette_for(mode)
    row = Text()
    row.append(" " * gutter_left(columns))
    if merged:
        label = id_role_label(entry.task_id, entry.role_id)
        row.append(label[: columns.id_role].ljust(columns.id_role),
                   style=theme.style_for(mode, "accent") if palette else None)
    row.append(f"{entry.stamp} ", style=theme.style_for(mode, "muted") if palette else None)

    if entry.kind == "tool":
        row.append(f"{entry.tool} ", style=theme.style_for(mode, "text_secondary") if palette else None)
        row.append(entry.target, style=theme.style_for(mode, "text") if palette else None)
        if entry.text:
            row.append(f" · {entry.text}", style=theme.style_for(mode, "muted") if palette else None)
    elif entry.kind == "result":
        token = "success" if entry.ok else "danger"
        row.append(entry.text, style=theme.style_for(mode, token) if palette else None)
    else:
        token = _ENTRY_TOKEN.get(entry.kind, "text")
        row.append(entry.text, style=theme.style_for(mode, token) if palette else None)

    limit = max(width - columns.gutter, 1)
    if row.cell_len > limit:
        row.truncate(limit)
    return row


STAMP_WIDTH = 9  # "14:22:31 " — continuation lines align under the text, not the clock


def render_transcript(entries: list[TranscriptEntry], columns: ColumnWidths,
                      mode: theme.ColorMode, width: int, *,
                      merged: bool, max_rows: int) -> list[Text]:
    """The transcript region: the last `max_rows` rows of `entries`.

    Assistant prose is the one kind that wraps, and it wraps here rather
    than being left to the terminal: a terminal-wrapped line re-lays-out
    the whole region when the width changes, and §6 is explicit that
    streaming text appends and never re-lays-out. Continuation rows carry
    a blank stamp so the timestamps stay a readable column instead of
    repeating.
    """
    prefix = (columns.id_role if merged else 0) + gutter_left(columns)
    body_width = max(width - columns.gutter - prefix - STAMP_WIDTH, 20)

    rows: list[Text] = []
    for entry in entries:
        if entry.kind != "text":
            rows.append(render_transcript_line(entry, columns, mode, width, merged=merged))
            continue
        chunks = textwrap.wrap(entry.text, width=body_width) or [""]
        for index, chunk in enumerate(chunks):
            head = replace(entry, text=chunk) if index == 0 else replace(
                entry, text=chunk, stamp=" " * (STAMP_WIDTH - 1), task_id="", role_id="")
            rows.append(render_transcript_line(head, columns, mode, width, merged=merged))
    return rows[-max_rows:] if max_rows > 0 else []
