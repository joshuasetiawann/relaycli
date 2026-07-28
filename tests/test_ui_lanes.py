"""Tests for relaycli/ui/lanes.py — the pure lane-list row renderer
(docs/design/DESIGN_TOKENS.md §2, §4, §9)."""

from __future__ import annotations

import pytest

from relaycli.ui.lanes import (
    GroupSummary,
    LaneView,
    basename_target,
    clip_goal,
    format_cost,
    format_elapsed,
    format_tokens,
    format_tool_target,
    group_for_display,
    render_group_row,
    render_lane_row,
)
from relaycli.ui.layout import LANE_GROUPING_THRESHOLD, resolve_columns

COLS_120 = resolve_columns(120)
COLS_80 = resolve_columns(80)


# --- formatting helpers ------------------------------------------------------
def test_clip_goal_leaves_short_text_untouched():
    assert clip_goal("short goal", 34) == "short goal"


def test_clip_goal_clips_at_width_minus_one_with_no_ellipsis():
    text = "x" * 40
    result = clip_goal(text, 34)
    assert result == "x" * 33
    assert "…" not in result


def test_basename_target_drops_directories_but_keeps_line():
    assert basename_target("relaycli/agent/deep/nested/loop.py:120") == "loop.py:120"


def test_basename_target_handles_no_line_number():
    assert basename_target("relaycli/agent/loop.py") == "loop.py"


def test_basename_target_passes_through_empty():
    assert basename_target("") == ""


def test_format_tool_target_fits_as_is_when_short():
    assert format_tool_target("read_file", "a.py:1", 30) == "read_file a.py:1"


def test_format_tool_target_drops_dirs_before_hard_clipping():
    result = format_tool_target("edit_file", "src/very/deep/nested/module.py:42", 23)
    assert result == "edit_file module.py:42"
    assert len(result) <= 23


def test_format_tool_target_hard_clips_when_basename_still_too_long():
    result = format_tool_target("run_command", "a_very_long_filename_indeed.py:9999", 15)
    assert len(result) <= 15
    assert "…" not in result


@pytest.mark.parametrize("n,expected", [(0, "0"), (999, "999"), (1000, "1.0k"),
                                         (125_340, "125.3k"), (2_500_000, "2.5M")])
def test_format_tokens(n, expected):
    assert format_tokens(n) == expected


@pytest.mark.parametrize("usd", [0.0, 0.0001, 1.2345, 12.345, 123.45, 1234.5, 12345])
def test_format_cost_fits_seven_columns_across_realistic_range(usd):
    assert len(format_cost(usd)) <= 7


def test_format_cost_starts_with_dollar_sign():
    assert format_cost(1.5).startswith("$")


@pytest.mark.parametrize("seconds,expected", [(0, "0s"), (45, "45s"), (65, "1m05s"),
                                               (3725, "1h02m")])
def test_format_elapsed(seconds, expected):
    assert format_elapsed(seconds) == expected


def test_format_elapsed_never_negative_for_negative_input():
    assert format_elapsed(-5) == "0s"


# --- row rendering -----------------------------------------------------------
def _lane(**overrides) -> LaneView:
    defaults = dict(task_id="auth", role_id="backend", status="running", goal="wire up auth")
    defaults.update(overrides)
    return LaneView(**defaults)


@pytest.mark.parametrize("mode", ["dark", "light", "no_color"])
def test_render_lane_row_produces_a_single_line(mode):
    row = render_lane_row(_lane(), COLS_120, mode)
    assert "\n" not in row.plain


def test_numeric_columns_never_collide():
    row = render_lane_row(_lane(tokens=125_340, cost_usd=1.2345, elapsed_s=125), COLS_120, "dark")
    # The bug this guards: right-justified fields with no separator produce
    # "125.3k$1.2345" — token/cost/elapsed digits must never run together.
    assert "k$" not in row.plain
    assert "0$" not in row.plain


def test_unknown_role_id_does_not_crash():
    row = render_lane_row(_lane(role_id="some-future-role"), COLS_120, "dark")
    assert "auth" in row.plain


def test_no_color_mode_appends_word_for_blocked():
    row = render_lane_row(_lane(status="blocked"), COLS_120, "no_color")
    assert "WAIT" in row.plain


def test_no_color_mode_appends_needs_you_word():
    row = render_lane_row(_lane(awaiting_you=True), COLS_120, "no_color")
    assert "NEEDS YOU" in row.plain


def test_no_color_mode_uses_ascii_glyph_not_unicode():
    row = render_lane_row(_lane(status="done"), COLS_120, "no_color")
    assert "✓" not in row.plain
    assert "+" in row.plain


def test_dark_and_light_row_text_is_identical_only_colors_differ():
    dark = render_lane_row(_lane(), COLS_120, "dark").plain
    light = render_lane_row(_lane(), COLS_120, "light").plain
    assert dark == light


def test_narrow_layout_drops_model_tokens_elapsed_columns():
    lane = _lane(model="claude-sonnet-5", tokens=999, elapsed_s=30)
    row = render_lane_row(lane, COLS_80, "dark")
    assert "claude-sonnet-5" not in row.plain


def test_cost_survives_the_narrow_layout():
    row = render_lane_row(_lane(cost_usd=4.5), COLS_80, "dark")
    assert "$4.5000" in row.plain


# --- grouping ------------------------------------------------------------
def test_at_or_below_threshold_no_grouping_happens():
    lanes = [_lane(task_id=f"t{i}") for i in range(LANE_GROUPING_THRESHOLD)]
    assert group_for_display(lanes, max_rows=9) == lanes


def test_above_threshold_settled_lanes_collapse():
    lanes = (
        [_lane(task_id=f"active{i}", status="running") for i in range(2)]
        + [_lane(task_id=f"done{i}", status="done") for i in range(4)]
        + [_lane(task_id="failed0", status="failed")]
    )
    result = group_for_display(lanes, max_rows=9)
    active = [r for r in result if isinstance(r, LaneView)]
    groups = [r for r in result if isinstance(r, GroupSummary)]
    assert len(active) == 2
    assert GroupSummary(status="done", count=4) in groups
    assert GroupSummary(status="failed", count=1) in groups


def test_grouping_respects_max_rows():
    lanes = [_lane(task_id=f"t{i}", status="running") for i in range(20)]
    result = group_for_display(lanes, max_rows=9)
    assert len(result) <= 9


def test_render_group_row_shows_count():
    row = render_group_row(GroupSummary(status="done", count=12), COLS_120, "dark")
    assert "12" in row.plain
    assert "done" in row.plain.lower()
