"""SLATE INSTRUMENT design tokens — palette, glyphs, role marks.

Source of truth: docs/design/DESIGN_TOKENS.md (extracted from the Claude
Design "Parallel orchestration design spec"). Replaces both prior brand
identities in this codebase — terminal's Claude-clay ``#D97757`` and web's
``#2D5BFF`` — with one palette shared by every surface (§0, §10).

This module only holds tokens (color, glyph, role-mark data) and the pure
functions to resolve them. It does not touch a Console or read the
environment other than the one explicit `current_color_mode` convenience
wrapper — everything else takes its inputs as arguments so it stays
trivially unit-testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    # Type-only: every use below is an annotation, and this module has
    # `from __future__ import annotations`, so none of them is evaluated at
    # runtime. Importing it for real created a genuine import cycle —
    # ui.theme -> agent.graph -> agent -> agent.loop -> skills -> config ->
    # config.menu -> ui.theme — which made `from relaycli.ui.theme import ...`
    # fail outright whenever it was the first relaycli import in a process.
    from relaycli.agent.graph import TaskStatus

ColorMode = Literal["dark", "light", "no_color"]


@dataclass(frozen=True)
class Palette:
    """The 9-token state table (DESIGN_TOKENS.md §1). Canonical — re-derive
    a tint toward the panel background for a one-off shade rather than
    adding a 10th token."""

    accent: str
    running: str
    waiting: str
    success: str
    danger: str
    warning: str
    muted: str
    rule: str
    text: str


DARK = Palette(
    accent="#5FA8DC", running="#45B3A0", waiting="#8E93CC", success="#6FB77F",
    danger="#D4695C", warning="#D6A05C", muted="#64798A", rule="#24323B", text="#C3CED6",
)

LIGHT = Palette(
    accent="#1C6C9C", running="#0F7367", waiting="#4E56A6", success="#2C7A4E",
    danger="#A93B2C", warning="#8A6312", muted="#5F7180", rule="#C2CDD4", text="#1B262E",
)

# §1's own base hex for each theme — the terminal surface never needs this
# as an explicit token (it inherits the real terminal background), but a
# non-terminal surface (the desktop web console) does.
DARK_BASE = "#0D1216"
LIGHT_BASE = "#EFF2F4"

# Extra surface tokens (§1) — the hues the state table doesn't cover.
DARK_SURFACE: dict[str, str] = {
    "page_bg": "#080C0F", "heading": "#E4EBF0", "text_secondary": "#8CA0AE",
    "rule_dim": "#1B252C", "row_focused_bg": "#131E26", "caret_idle": "#3B4A55",
    "link": "#5FA8DC", "link_hover": "#8CC6EC",
}

# The light equivalents. DESIGN_TOKENS.md used to say light had none, which
# was an extraction miss, not a fact about the design: the source's own
# "120 COLUMNS — LIGHT TERMINAL" mockup uses all four of the tokens that
# actually appear inside a lane row, and the terminal surface needs them to
# render the light theme at parity with dark. `page_bg`/`rule_dim`/`link`/
# `link_hover` genuinely have no light instance anywhere in the source, so
# they are absent here rather than invented — surface() falls back to a core
# palette token for those.
LIGHT_SURFACE: dict[str, str] = {
    "heading": "#0B141A", "text_secondary": "#43555F",
    "row_focused_bg": "#DEE7EC", "caret_idle": "#9BAAB4",
}


def surface_for(mode: ColorMode) -> dict[str, str]:
    """The extra (non-state-table) surface tokens for `mode`. Empty for
    no_color, which carries no hex at all."""
    if mode == "dark":
        return DARK_SURFACE
    if mode == "light":
        return LIGHT_SURFACE
    return {}


# Where a surface token goes when a theme has no instance of it. Only
# foreground tokens are listed: there is no sensible stand-in for a
# missing *background* (`page_bg`, `row_focused_bg`), and quietly painting
# one with a body-text hue would make the row it fills unreadable, so a
# theme that lacks a background must say so instead.
_SURFACE_FALLBACK: dict[str, str] = {
    "heading": "text", "text_secondary": "muted", "rule_dim": "rule",
    "caret_idle": "muted", "link": "accent", "link_hover": "accent",
}


def style_for(mode: ColorMode, name: str) -> str | None:
    """Resolve one token name — surface tokens first, then the 9-token
    state table — to a hex string, or None under no_color, which carries
    no hex at all. The single place the dark/light/no_color branch is
    spelled out, so no renderer has to repeat it.

    An unknown name raises rather than resolving to something plausible:
    a typo that silently returned the wrong hue would look like a design
    decision to everyone reading the output.
    """
    palette = palette_for(mode)
    if palette is None:
        return None
    surface = surface_for(mode)
    if name in surface:
        return surface[name]
    if hasattr(palette, name):
        return getattr(palette, name)
    fallback = _SURFACE_FALLBACK.get(name)
    if fallback is None:
        raise KeyError(f"no '{name}' token in the {mode} theme, and no fallback for it")
    return getattr(palette, fallback)


def resolve_color_mode(preference: str | None, *, no_color_env: bool = False) -> ColorMode:
    """Resolve the effective color mode. `NO_COLOR` always wins (the
    no-color.org convention); otherwise falls back to the `theme` config
    preference (default "dark" — see config/manager.py's
    DEFAULT_PREFERENCES), normalizing anything unrecognized to "dark"
    rather than erroring, since that preference has never been validated
    against an enum (set_runtime_option accepts any string)."""
    if no_color_env:
        return "no_color"
    if preference in ("light", "no_color"):
        return preference  # type: ignore[return-value]
    return "dark"


def current_color_mode(preference: str | None) -> ColorMode:
    """Convenience wrapper reading NO_COLOR from the real environment."""
    return resolve_color_mode(preference, no_color_env="NO_COLOR" in os.environ)


def palette_for(mode: ColorMode) -> Palette | None:
    """None for "no_color" — that mode carries no hex colors at all."""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return None


@dataclass(frozen=True)
class Glyph:
    symbol: str
    ascii: str


# Task states (§2) — keyed by the real relaycli.agent.graph.TaskStatus
# literals, not the design mockup's own prose, so this can never drift from
# what Task.status actually produces.
TASK_STATE_GLYPHS: dict[TaskStatus, Glyph] = {
    "pending": Glyph("○", "."),
    "ready": Glyph("◇", "o"),
    "running": Glyph("◆", "*"),  # animated as a spinner frame when motion is on — see ui/live.py
    "blocked": Glyph("⊘", "!"),
    "done": Glyph("✓", "+"),
    "failed": Glyph("✗", "x"),
    "cancelled": Glyph("╌", "-"),
}

# Not a Task.status — a cross-cutting condition (a *running* task whose
# agent has an open permission request underneath it). Callers that track
# that separately (ui/live.py) overlay this glyph instead of the state one.
AWAITING_YOU_GLYPH = Glyph("?", "?")

# Which palette token colors each glyph above (and AWAITING_YOU). Two states
# may legitimately share a color, since color is never the only signal —
# ``done``/``ready`` both being calm is fine as long as the glyph differs.
TASK_STATE_COLOR: dict[TaskStatus, str] = {
    "pending": "muted", "ready": "muted", "running": "running", "blocked": "waiting",
    "done": "success", "failed": "danger", "cancelled": "muted",
}
AWAITING_YOU_COLOR = "warning"

# Spelled-out words for NO_COLOR mode (§4: "no fact present in the color
# version is lost"). The design gives WAIT / HELD BY / NEEDS YOU / PASS as
# illustrative examples, not an exhaustive table; the remaining words are a
# reasonable continuation of that vocabulary, not literal spec text.
TASK_STATE_WORD: dict[TaskStatus, str] = {
    "pending": "PENDING", "ready": "READY", "running": "RUNNING", "blocked": "WAIT",
    "done": "DONE", "failed": "FAILED", "cancelled": "CANCELLED",
}
AWAITING_YOU_WORD = "NEEDS YOU"

# Markers & connectors (§2) — glyph, ascii fallback.
MARKERS: dict[str, Glyph] = {
    "lease_held": Glyph("▤", "[L]"),
    "lease_waiting": Glyph("▥", "[~]"),
    "diff_add": Glyph("+", "+"),
    "diff_remove": Glyph("−", "-"),
    "escalation": Glyph("▲", "^"),
    "escalation_light": Glyph("▵", "^"),
    "cost_prefix": Glyph("$", "$"),
    "focus_rail": Glyph("▌", "|"),
    "prompt_caret": Glyph("❯", ">"),
}
CONNECTORS = {"branch": "├─", "last": "└─", "pipe": "│"}
BUDGET_METER = {"filled": "▮", "empty": "▯"}

# Spinner (§6 Motion): 10 braille frames, one shared clock at 90ms so every
# lane ticks in unison. Reduced motion (NO_MOTION=1 or non-TTY) substitutes
# the static Glyph("◆", "*") from TASK_STATE_GLYPHS["running"] instead.
SPINNER_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
SPINNER_INTERVAL_S = 0.09


def spinner_frame(now: float) -> str:
    """The frame every running lane shows at `now`.

    Derived from the clock rather than from a per-lane counter, which is
    what §6 means by "one shared clock for every lane": spinners tick in
    unison. Independently advanced spinners read as several unrelated
    things happening, when the point of the lane list is that they are one
    run.
    """
    return SPINNER_FRAMES[int(now / SPINNER_INTERVAL_S) % len(SPINNER_FRAMES)]

# 16-color fallback (§1) — hue order preserved across the downgrade so
# muscle memory survives it. Rich auto-downgrades our truecolor hex tokens
# for lower-capability terminals; this table documents (and lets tests
# assert on) what that downgrade is supposed to land on.
SIXTEEN_COLOR_FALLBACK: dict[str, str] = {
    "accent": "blue", "running": "cyan", "waiting": "magenta", "success": "green",
    "danger": "red", "warning": "yellow", "muted": "bright_black",
}


@dataclass(frozen=True)
class RoleMark:
    family: str
    family_glyph: str
    code: str


# Role taxonomy — resolved (DESIGN_TOKENS.md §9). The design's own §2 table
# doesn't match core/roles.py's 16 roles; this is the reconciled mapping —
# use this, not §2, for implementation. Keys are core/roles.py's real
# RoleDef.id values.
ROLE_MARKS: dict[str, RoleMark] = {
    "orchestrator": RoleMark("DISCOVER", "◇", "orc"),
    "researcher": RoleMark("DISCOVER", "◇", "rsc"),
    "architect": RoleMark("DISCOVER", "◇", "arc"),
    "planner": RoleMark("DISCOVER", "◇", "pln"),
    "backend": RoleMark("BUILD", "▣", "bnd"),
    "frontend": RoleMark("BUILD", "▣", "fnd"),
    "database": RoleMark("BUILD", "▣", "dat"),
    "refactorer": RoleMark("BUILD", "▣", "rfc"),
    "coder": RoleMark("BUILD", "▣", "cod"),
    "tester": RoleMark("VERIFY", "◈", "tst"),
    "performance": RoleMark("VERIFY", "◈", "prf"),
    "debugger": RoleMark("VERIFY", "◈", "dbg"),
    "devops": RoleMark("OPERATE", "⊞", "dvo"),
    "reviewer": RoleMark("GOVERN", "⊙", "rev"),
    "security": RoleMark("GOVERN", "⊙", "sec"),
    "documenter": RoleMark("GOVERN", "⊙", "doc"),
}

# Reserved family slots the design covers but no current role uses (§9) —
# kept so a future role addition already has a spoken-for glyph and code.
RESERVED_ROLE_CODES: dict[str, str] = {
    "api": "BUILD", "e2e": "VERIFY", "infra": "OPERATE", "release": "OPERATE",
    "observability": "OPERATE",
}
