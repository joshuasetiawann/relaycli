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
