"""Tests for relaycli/ui/theme.py — the SLATE INSTRUMENT design tokens
(docs/design/DESIGN_TOKENS.md) that replace the old Claude-clay / web-blue
accents."""

from __future__ import annotations

import re

import pytest

from relaycli.agent.graph import TaskStatus
from relaycli.core.roles import BUILTIN_ROLES_BY_ID
from relaycli.ui import theme

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ALL_TASK_STATUSES: tuple[TaskStatus, ...] = (
    "pending", "ready", "running", "blocked", "done", "failed", "cancelled",
)


# --- color mode resolution --------------------------------------------------
def test_no_color_env_wins_over_any_preference():
    assert theme.resolve_color_mode("light", no_color_env=True) == "no_color"
    assert theme.resolve_color_mode(None, no_color_env=True) == "no_color"


@pytest.mark.parametrize("preference,expected", [
    ("light", "light"),
    ("no_color", "no_color"),
    ("dark", "dark"),
    (None, "dark"),
    ("", "dark"),
    ("solarized-plaid", "dark"),  # unvalidated free-text preference: never raises
])
def test_resolve_color_mode_falls_back_to_dark(preference, expected):
    assert theme.resolve_color_mode(preference) == expected


def test_current_color_mode_reads_no_color_from_real_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert theme.current_color_mode("light") == "no_color"
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert theme.current_color_mode("light") == "light"


def test_palette_for_each_mode():
    assert theme.palette_for("dark") is theme.DARK
    assert theme.palette_for("light") is theme.LIGHT
    assert theme.palette_for("no_color") is None


# --- palette shape -----------------------------------------------------------
@pytest.mark.parametrize("palette", [theme.DARK, theme.LIGHT])
def test_palette_tokens_are_valid_hex(palette):
    for field in ("accent", "running", "waiting", "success", "danger",
                   "warning", "muted", "rule", "text"):
        value = getattr(palette, field)
        assert _HEX_RE.match(value), f"{field}={value!r} is not #RRGGBB"


def test_dark_and_light_are_actually_different():
    assert theme.DARK != theme.LIGHT


# --- task-state glyphs/colors/words cover every real TaskStatus exactly ----
def test_task_state_glyphs_cover_every_status_exactly_once():
    assert set(theme.TASK_STATE_GLYPHS) == set(_ALL_TASK_STATUSES)


def test_task_state_glyphs_have_nonempty_symbol_and_ascii():
    for status, glyph in theme.TASK_STATE_GLYPHS.items():
        assert glyph.symbol, status
        assert glyph.ascii, status


def test_task_state_color_covers_every_status_with_a_real_palette_token():
    assert set(theme.TASK_STATE_COLOR) == set(_ALL_TASK_STATUSES)
    for status, token in theme.TASK_STATE_COLOR.items():
        assert hasattr(theme.DARK, token), f"{status} -> unknown token {token!r}"
        assert hasattr(theme.LIGHT, token), f"{status} -> unknown token {token!r}"


def test_task_state_word_covers_every_status():
    assert set(theme.TASK_STATE_WORD) == set(_ALL_TASK_STATUSES)
    for status, word in theme.TASK_STATE_WORD.items():
        assert word == word.upper(), f"{status} word {word!r} should be upper-case"


def test_awaiting_you_is_not_a_task_status():
    # Cross-cutting condition, not in Task.status's literal set — must stay
    # out of the per-status tables so it can never collide with a real state.
    assert "awaiting_you" not in theme.TASK_STATE_GLYPHS
    assert theme.AWAITING_YOU_GLYPH.symbol == "?"
    assert hasattr(theme.DARK, theme.AWAITING_YOU_COLOR)


# --- role taxonomy -----------------------------------------------------------
def test_role_marks_cover_every_real_role_exactly_once():
    assert set(theme.ROLE_MARKS) == set(BUILTIN_ROLES_BY_ID)


def test_role_marks_have_unique_three_letter_codes():
    codes = [mark.code for mark in theme.ROLE_MARKS.values()]
    assert len(codes) == len(set(codes)), codes
    for code in codes:
        assert len(code) == 3, code


def test_role_marks_use_one_of_the_five_families():
    families = {mark.family for mark in theme.ROLE_MARKS.values()}
    assert families == {"DISCOVER", "BUILD", "VERIFY", "OPERATE", "GOVERN"}


def test_reserved_role_codes_do_not_collide_with_live_roles():
    live_codes = {mark.code for mark in theme.ROLE_MARKS.values()}
    assert set(theme.RESERVED_ROLE_CODES).isdisjoint(live_codes)


# --- motion tokens -----------------------------------------------------------
def test_spinner_has_ten_single_width_frames():
    assert len(theme.SPINNER_FRAMES) == 10
    for frame in theme.SPINNER_FRAMES:
        assert len(frame) == 1
    assert len(set(theme.SPINNER_FRAMES)) == 10


def test_sixteen_color_fallback_preserves_hue_order_for_core_states():
    # accent/running/waiting/success/danger/warning/muted, in that order,
    # per DESIGN_TOKENS.md §1 "hue order is preserved across the downgrade".
    assert list(theme.SIXTEEN_COLOR_FALLBACK) == [
        "accent", "running", "waiting", "success", "danger", "warning", "muted",
    ]


# --- import-cycle regression -------------------------------------------
@pytest.mark.parametrize("first_import", [
    "from relaycli.ui.theme import DARK",
    "from relaycli.ui import lanes",
    "from relaycli.ui import live",
    "from relaycli.config import menu",
])
def test_no_import_cycle_whatever_is_imported_first(first_import):
    """Regression: ui.theme imported agent.graph for a type that is only
    ever used in an annotation. With `from __future__ import annotations`
    that annotation is never evaluated, but the import was still real, and
    it closed a cycle:

        ui.theme -> agent.graph -> agent -> agent.loop -> skills
                 -> config -> config.menu -> ui.theme

    so `from relaycli.ui.theme import DARK` raised ImportError whenever it
    was the first relaycli import in a process. Must run in a subprocess:
    by the time this test executes, pytest has already imported everything,
    which is exactly what hides the bug.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", first_import + "; print('OK')"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"import cycle: {result.stderr[-400:]}"
    assert "OK" in result.stdout


def test_task_state_tables_are_intact_at_runtime():
    """The TYPE_CHECKING guard must not have cost us the real data."""
    from relaycli.ui.theme import TASK_STATE_COLOR, TASK_STATE_GLYPHS, TASK_STATE_WORD

    for status in ("pending", "ready", "running", "blocked", "done", "failed", "cancelled"):
        assert status in TASK_STATE_GLYPHS
        assert status in TASK_STATE_COLOR
        assert status in TASK_STATE_WORD


# --- surface tokens ---------------------------------------------------------
def test_light_has_the_surface_tokens_its_lane_rows_need():
    """DESIGN_TOKENS.md used to record light as having no surface-token
    equivalents. The source's own light mockup uses all four of these, and
    without them the light theme cannot draw a focused row at all."""
    for name in ("heading", "text_secondary", "row_focused_bg", "caret_idle"):
        assert theme.LIGHT_SURFACE[name].startswith("#")


def test_surface_tokens_are_distinct_from_the_body_text_they_sit_beside():
    assert theme.LIGHT_SURFACE["heading"] != theme.LIGHT.text
    assert theme.DARK_SURFACE["heading"] != theme.DARK.text


def test_style_for_falls_back_to_the_palette_when_a_theme_lacks_a_surface_token():
    # rule_dim has no light instance anywhere in the source, so it is not
    # invented — the core `rule` token stands in.
    assert theme.style_for("light", "rule_dim") == theme.LIGHT.rule
    assert theme.style_for("dark", "rule_dim") == theme.DARK_SURFACE["rule_dim"]


def test_style_for_resolves_plain_palette_tokens_too():
    assert theme.style_for("dark", "warning") == theme.DARK.warning
    assert theme.style_for("light", "warning") == theme.LIGHT.warning


def test_no_color_resolves_every_token_to_nothing():
    for name in ("accent", "heading", "row_focused_bg", "rule"):
        assert theme.style_for("no_color", name) is None


# --- the spinner ------------------------------------------------------------
def test_the_spinner_walks_every_frame_in_order():
    frames = [theme.spinner_frame(i * theme.SPINNER_INTERVAL_S)
              for i in range(len(theme.SPINNER_FRAMES))]
    assert tuple(frames) == theme.SPINNER_FRAMES


def test_the_spinner_wraps_rather_than_running_off_the_end():
    assert theme.spinner_frame(1e6) in theme.SPINNER_FRAMES
    assert theme.spinner_frame(0.0) == theme.SPINNER_FRAMES[0]


def test_every_lane_reads_the_same_clock():
    """§6: "one shared clock for every lane — spinners tick in unison".
    A per-lane counter would make four agents look like four unrelated
    things happening."""
    now = 12.345
    assert len({theme.spinner_frame(now) for _ in range(5)}) == 1


def test_the_spinner_frames_are_all_single_width():
    import unicodedata
    for frame in theme.SPINNER_FRAMES:
        assert len(frame) == 1
        assert unicodedata.east_asian_width(frame) not in ("W", "F")


def test_an_unknown_token_name_raises_instead_of_guessing():
    """A typo that resolved to a plausible hue would read as a deliberate
    design choice to everyone looking at the output."""
    with pytest.raises(KeyError):
        theme.style_for("dark", "not-a-token")


def test_a_missing_background_token_is_never_faked_from_a_foreground_one():
    # Both themes define row_focused_bg, so this only guards the fallback
    # table itself: painting a fill with body-text colour would make the
    # row it fills unreadable.
    assert "row_focused_bg" not in theme._SURFACE_FALLBACK
    assert "page_bg" not in theme._SURFACE_FALLBACK
