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
    # §11's launch bar is the one screen that carries it (`▌relaycli 0.9.2`).
    # A running frame leaves it empty: the version does not change
    # mid-session, and those columns are worth more to the branch name.
    version: str = ""
    # §11 again: with nothing running the bar says `idle` where a live one
    # says `4 agents  128.4k tok`. Neither number means anything yet, and
    # printing `1 agent  0 tok` before you have typed claims otherwise.
    idle: bool = False


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
        rail = theme.MARKERS["brand_rail"]
        name = "relay" if compact else "relaycli"
        left.append(f"{rail.symbol if palette else rail.ascii}{name}",
                    style=f"bold {tint('accent')}" if palette else "bold")
        if data.version:
            # Kept even in the 80-column form. §04's cut order gives up
            # elapsed, tokens and the model — all of which a *running*
            # frame is showing you second by second — and this is the one
            # bar that is not running: it greets you once, and which build
            # greeted you is the fact a bug report needs.
            left.append(f" {data.version}", style=tint("muted"))

        cwd = short_path(data.cwd)
        if cwd:
            left.append(" " + (Path(cwd).name if basename_only else cwd), style=tint("text"))
        if data.branch:
            left.append(" " if compact else "  git:", style=tint("muted"))
            left.append(data.branch, style=tint("text"))
            if data.dirty:
                # NO_COLOR has no ± in its ASCII pass — §04's monochrome
                # screen spells the same fact "+3". It also keeps the space
                # before it that the coloured 80-column bar closes up: `±3`
                # reads as one token attached to the branch, but `+3` with
                # no colour to separate it would read as part of the name.
                sign = "±" if palette else "+"
                left.append(("" if (compact and palette) else " ") + f"{sign}{data.dirty}",
                            style=tint("warning"))
            elif not compact:
                left.append("  clean", style=tint("muted"))
        if data.permission_mode and with_mode:
            left.append("  mode:", style=tint("muted"))
            left.append(data.permission_mode, style=tint("text"))
        return left

    right = Text()
    if data.idle:
        right.append("idle", style=tint("muted"))
    elif compact:
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
                             *, label: str, merged: bool, subtitle: str = "",
                             compact: bool = False, tail: bool = True) -> Text:
    """`├─ a1 ▣ bnd transcript ────────────  focused·merged m` — names the
    stream you are reading and which of the two modes you are in, since
    the two look identical once you are three lines into either.

    `subtitle` replaces the word "transcript" with the design's merged-mode
    caption (`merged transcript · all 3 agents, role-prefixed`); `compact`
    is §04's 80-column form, which keeps only the lane id; `tail=False`
    drops the mode indicator, which is what the source does on screens
    where the mode is not in question.
    """
    palette = theme.palette_for(mode)

    def tint(token: str) -> str | None:
        return theme.style_for(mode, token) if palette else None

    row = Text()
    row.append(" " * gutter_left(columns))
    row.append("├", style=tint("rule"))
    row.append("─ ", style=tint("muted"))
    # A lane id is a place you can navigate to, so it takes the accent a key
    # takes; a mode name is not, so it stays body text.
    row.append(label, style=tint("text" if subtitle else "accent") if palette else "bold")
    if subtitle:
        row.append(f" · {subtitle} ", style=tint("muted"))
    elif compact:
        row.append(" ", style=tint("muted"))
    else:
        row.append(" transcript ", style=tint("muted"))

    mode_hint = Text()
    if tail and not compact:
        mode_hint.append(" focused", style=tint("muted" if merged else "text"))
        mode_hint.append("·", style=tint("muted"))
        mode_hint.append("merged ", style=tint("text" if merged else "muted"))
        mode_hint.append("m", style=tint("accent") if palette else "bold")

    span = max(width - columns.gutter - row.cell_len - mode_hint.cell_len
               + gutter_left(columns), 0)
    row.append("─" * span, style=tint("rule"))
    row.append_text(mode_hint)
    return row


def render_budget_note(fraction: float, limit_usd: float, columns: ColumnWidths,
                       mode: theme.ColorMode, stamp: str) -> Text:
    """`▲ 14:23:11 budget 90% of $3.00 — new spawns will be held at 100%.`

    The one transcript row that is about the run rather than about an agent.
    It states the threshold it will act on rather than only the number it
    has reached, because "90%" is not a decision and "new spawns hold at
    100%" is.
    """
    palette = theme.palette_for(mode)

    def tint(token: str) -> str | None:
        return theme.style_for(mode, token) if palette else None

    marker = theme.MARKERS["escalation"]
    row = Text()
    row.append(" " * gutter_left(columns))
    row.append(f"{marker.symbol if palette else marker.ascii} {stamp} ", style=tint("warning"))
    row.append(f"budget {round(fraction * 100)}% of ${limit_usd:.2f} — "
               "new spawns will be held at 100%. ", style=tint("text"))
    row.append("^b" if palette else "[^b]", style=tint("accent"))
    row.append(" to raise.", style=tint("text"))
    return row


@dataclass
class KeyHint:
    """One `tab lane` pair in the bottom strip: the key, then what it
    does. `badge` is the count the design hangs off `d diffs 1` and
    `y/n answer a3` — the thing that is waiting for you."""

    key: str
    label: str
    badge: str = ""


def render_hint(hint: KeyHint, mode: theme.ColorMode) -> Text:
    """One `tab lane` pair. Keys in accent, labels muted, badges warning —
    a badge is always something waiting on you, which is why it borrows the
    same hue as the `?` awaiting-you state rather than a new one.

    Shared by the bottom strip, the lane-list header, the input row's
    right-hand tail and every full-screen panel's footer, so a key never
    renders one way in one region and another way in the next.
    """
    palette = theme.palette_for(mode)
    piece = Text()
    piece.append(hint.key if palette else f"[{hint.key}]",
                 style=theme.style_for(mode, "accent") if palette else None)
    if hint.label:
        piece.append(f" {hint.label}", style=theme.style_for(mode, "muted") if palette else None)
    if hint.badge:
        piece.append(f" {hint.badge}",
                     style=theme.style_for(mode, "warning") if palette else "bold")
    return piece


def render_hints(hints: list[KeyHint], mode: theme.ColorMode, *, gap: int = 2) -> Text:
    """Several hints in a row, `gap` spaces apart."""
    out = Text()
    for index, hint in enumerate(hints):
        if index:
            out.append(" " * gap)
        out.append_text(render_hint(hint, mode))
    return out


def render_agents_header(total: int, breakdown: list[tuple[str, int]],
                         hints: list[KeyHint], columns: ColumnWidths,
                         mode: theme.ColorMode, width: int) -> Text:
    """`AGENTS 4 · 1 running · 1 blocked · 1 review · 1 done ──── tab focus …`

    §03's header over an ungrouped lane list. It answers the same question
    the band headers answer once the list groups — how many agents, and in
    what state — without spending a row per band, which is why the flat
    list can afford it and the six-agent screen cannot.

    `breakdown` is passed in rather than counted here: "review" is not a
    TaskStatus but a cross-cutting awaiting-you condition, so only the
    caller can say how the lanes divide up.
    """
    palette = theme.palette_for(mode)

    def tint(token: str) -> str | None:
        return theme.style_for(mode, token) if palette else None

    row = Text()
    row.append(" " * gutter_left(columns))
    row.append("AGENTS ", style=tint("muted") if palette else "bold")
    row.append(str(total), style=tint("text"))
    for word, count in breakdown:
        row.append(f" · {count} {word}", style=tint("muted"))

    tail = render_hints(hints, mode)
    limit = max(width - (columns.gutter - gutter_left(columns)), 1)
    span = limit - row.cell_len - tail.cell_len - 2
    if span > 0:
        row.append(" " + "─" * span, style=tint("rule"))
    row.append(" ")
    row.append_text(tail)
    return row


def render_key_strip(hints: list[KeyHint], columns: ColumnWidths,
                     mode: theme.ColorMode, width: int, *, gap: int = 2) -> Text:
    """§03's bottom strip. Keys in accent, labels muted, badges warning —
    a badge is always something waiting on you, which is why it borrows
    the same hue as the `?` awaiting-you state rather than a new one.

    `gap` is 2 at 120 columns and 1 at 80: the strip is the row with the
    most independent items on it, so closing the gaps buys back a hint that
    would otherwise be the one cut."""
    def one(hint: KeyHint) -> Text:
        return render_hint(hint, mode)

    row = Text()
    # Indented to sit under the input text, not under the caret: the strip
    # and the line above it are one block, and `❯ ` is two columns wide.
    row.append(" " * (gutter_left(columns) + 2))
    limit = max(width - columns.gutter, 1)
    # The last hint is `? keys`, the one that tells you what was left out,
    # so it is the one that survives when the strip has to be cut short.
    # Reserving its width up front is why the strip can never be the row
    # that overflows the terminal — which would wrap and take the whole
    # pinned frame with it.
    tail = one(hints[-1]) if hints else Text()
    for index, hint in enumerate(hints[:-1] if hints else []):
        piece = one(hint)
        lead = gap if index else 0
        if row.cell_len + lead + piece.cell_len + gap + tail.cell_len > limit:
            break
        if lead:
            row.append(" " * lead)
        row.append_text(piece)
    if hints:
        if row.cell_len > gutter_left(columns) + 2:
            row.append(" " * gap)
        row.append_text(tail)
    return row


def render_input_row(columns: ColumnWidths, mode: theme.ColorMode, width: int,
                     *, placeholder: str, typed: str | None = None,
                     tail: list[KeyHint] | None = None) -> Text:
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

    `tail` is the design's right-aligned hint pair (`⏎ send  esc stop all`,
    or `m back to focused`). It is reserved before the typed text is laid
    out, so a long note clips against the hints rather than over them.
    """
    palette = theme.palette_for(mode)
    caret = theme.MARKERS["prompt_caret"]
    row = Text()
    row.append(" " * gutter_left(columns))
    row.append(f"{caret.symbol if palette else caret.ascii} ",
               style=theme.style_for(mode, "accent") if palette else None)
    hints = render_hints(tail, mode) if tail else Text()
    limit = max(width - (columns.gutter - gutter_left(columns)), 1)
    if typed is None:
        row.append(placeholder, style=theme.style_for(mode, "caret_idle") if palette else None)
        return _pad_between(row, hints, limit) if tail else row
    cursor = theme.MARKERS["text_cursor"]
    cursor_text = cursor.symbol if palette else cursor.ascii
    reserved = hints.cell_len + (2 if tail else 0)
    room = max(limit - row.cell_len - len(cursor_text) - reserved, 0)
    row.append(typed[len(typed) - room:] if room else "",
               style=theme.style_for(mode, "text") if palette else None)
    row.append(cursor_text, style=theme.style_for(mode, "caret_idle") if palette else None)
    return _pad_between(row, hints, limit) if tail else row


@dataclass(frozen=True)
class TranscriptEntry:
    """One line of the transcript. `kind` picks the colour and the shape;
    `stamp` is wall-clock `HH:MM:SS` because the transcript is the record
    you scroll back through after the fact, where elapsed means nothing.

    The optional fields below are the fragments the design hangs off a tool
    row, each one a measured fact rather than a formatting choice: the
    diffstat an edit produced, the result a command returned, the note the
    agent gave for the call it is making right now, and the key that opens
    the thing the row is talking about.
    """

    stamp: str
    kind: str            # "tool" | "text" | "result" | "note"
    task_id: str = ""
    role_id: str = ""
    tool: str = ""
    target: str = ""
    text: str = ""
    ok: bool = True
    # `edit src/lease/queue.ts  +38 −11`
    added: int | None = None
    removed: int | None = None
    # `bash pnpm vitest run lease  ✓ 12 passed in 1.4s` — the outcome of
    # this call, inline. `tail` is the muted remainder after it, and is
    # shared with a standalone `kind="result"` row.
    result: str = ""
    tail: str = ""
    # `edit queue.ts:88 — fairness bump for aged waiters`
    note: str = ""
    # `· d to review diff`
    hint: KeyHint | None = None
    # This is the call in flight, so the row carries the spinner (§6's one
    # shared clock supplies the frame).
    active: bool = False


_ENTRY_TOKEN = {"tool": "text_secondary", "text": "text", "result": "success", "note": "warning"}


@dataclass
class TranscriptView:
    entries: list[TranscriptEntry] = field(default_factory=list)
    merged: bool = False


def render_transcript_line(entry: TranscriptEntry, columns: ColumnWidths,
                           mode: theme.ColorMode, width: int, *, merged: bool,
                           spinner: str | None = None) -> Text:
    """One transcript row. Merged mode spends a fixed 10-column prefix on
    `a1 ▣ bnd` and interleaves every agent by timestamp; focused mode
    drops the prefix and shows one stream. Both clip rather than wrap —
    §6's "streaming text appends, never re-lays-out": a wrapped line would
    reflow every row above it on the next token.

    The stamp column is a fixed STAMP_WIDTH whether the entry carries a
    stamp or not, so a continuation row's text lands under the text above
    it rather than under the clock.
    """
    palette = theme.palette_for(mode)
    row = Text()

    def tint(token: str) -> str | None:
        return theme.style_for(mode, token) if palette else None

    row.append(" " * gutter_left(columns))
    if merged:
        label = id_role_label(entry.task_id, entry.role_id)
        row.append(label[: columns.id_role].ljust(columns.id_role), style=tint("accent"))
    row.append(entry.stamp.ljust(STAMP_WIDTH - 1) + " ", style=tint("muted"))

    def outcome(text: str) -> Text:
        """`✓ 12 passed`, or `PASS 12 passed` with no colour to carry it."""
        glyph = theme.TASK_STATE_GLYPHS["done" if entry.ok else "failed"]
        lead = glyph.symbol if palette else theme.RESULT_WORD[entry.ok]
        piece = Text()
        piece.append(f"{lead} {text}", style=tint("success" if entry.ok else "danger"))
        return piece

    if entry.active:
        # §2's running glyph, animated by the one clock every lane shares.
        glyph = theme.TASK_STATE_GLYPHS["running"]
        row.append((spinner or glyph.symbol) if palette else glyph.ascii,
                   style=tint("running"))
        row.append(" ")

    if entry.kind == "tool":
        row.append(f"{entry.tool} ", style=tint("text_secondary"))
        row.append(entry.target, style=tint("text"))
        if entry.added is not None or entry.removed is not None:
            row.append("  ")
            if entry.added is not None:
                row.append(f"{theme.MARKERS['diff_add'].symbol}{entry.added}",
                           style=tint("success"))
            if entry.removed is not None:
                row.append(" " if entry.added is not None else "")
                row.append(f"{theme.MARKERS['diff_remove'].symbol}{entry.removed}",
                           style=tint("danger"))
        if entry.result:
            row.append("  ")
            row.append_text(outcome(entry.result))
        if entry.note:
            # At 80 columns the em dash goes and the note simply follows the
            # target: the separator is the first thing worth two columns.
            row.append(f" {entry.note}" if columns.model is None else f" — {entry.note}",
                       style=tint("muted"))
        if entry.text:
            row.append(f" · {entry.text}", style=tint("muted"))
    elif entry.kind == "result":
        row.append_text(outcome(entry.text))
    else:
        row.append(entry.text, style=tint(_ENTRY_TOKEN.get(entry.kind, "text")))

    if entry.tail:
        row.append(f" {entry.tail}", style=tint("muted"))
    if entry.hint is not None:
        row.append("  · ", style=tint("muted"))
        row.append_text(render_hint(entry.hint, mode))

    limit = max(width - columns.gutter, 1)
    if row.cell_len > limit:
        row.truncate(limit)
    return row


STAMP_WIDTH = 9  # "14:22:31 " — continuation lines align under the text, not the clock


def render_transcript(entries: list[TranscriptEntry], columns: ColumnWidths,
                      mode: theme.ColorMode, width: int, *,
                      merged: bool, max_rows: int,
                      spinner: str | None = None) -> list[Text]:
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
            rows.append(render_transcript_line(entry, columns, mode, width,
                                               merged=merged, spinner=spinner))
            continue
        chunks = textwrap.wrap(entry.text, width=body_width) or [""]
        for index, chunk in enumerate(chunks):
            head = replace(entry, text=chunk) if index == 0 else replace(
                entry, text=chunk, stamp="", task_id="", role_id="")
            rows.append(render_transcript_line(head, columns, mode, width,
                                               merged=merged, spinner=spinner))
    return rows[-max_rows:] if max_rows > 0 else []
