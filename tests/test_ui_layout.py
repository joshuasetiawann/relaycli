"""Tests for relaycli/ui/layout.py — pure column/row math for the pinned
frame (docs/design/DESIGN_TOKENS.md §4)."""

from __future__ import annotations

import pytest

from relaycli.ui.layout import (
    CANONICAL_WIDTH,
    LANE_LIST_MAX_ROWS,
    MIN_TRANSCRIPT_ROWS,
    MINIMUM_WIDTH,
    TooNarrowError,
    lane_list_rows,
    resolve_columns,
    transcript_rows,
)


def test_below_80_columns_refuses_with_the_exact_message():
    with pytest.raises(TooNarrowError) as exc_info:
        resolve_columns(64)
    assert str(exc_info.value) == "relaycli needs 80 columns (have 64)"
    assert exc_info.value.width == 64


def test_79_columns_still_refuses():
    with pytest.raises(TooNarrowError):
        resolve_columns(79)


def test_80_columns_is_the_narrow_layout():
    columns = resolve_columns(MINIMUM_WIDTH)
    assert columns.goal == 29
    assert columns.tool_target == 23
    assert columns.model is None
    assert columns.tokens is None
    assert columns.elapsed is None
    assert columns.cost == 7  # never cut


def test_120_columns_is_the_canonical_layout():
    columns = resolve_columns(CANONICAL_WIDTH)
    assert columns.goal == 34
    assert columns.tool_target == 30
    assert columns.model == 15
    assert columns.tokens == 8
    assert columns.elapsed == 7
    assert columns.cost == 7


def test_between_80_and_120_uses_the_narrow_layout():
    # No documented intermediate layout — §4 only gives 120 and 80.
    columns = resolve_columns(100)
    assert columns == resolve_columns(80)


def test_never_cut_fields_are_identical_at_both_widths():
    narrow, wide = resolve_columns(80), resolve_columns(120)
    for field in ("focus_rail", "state_glyph", "id_role", "cost", "gutter"):
        assert getattr(narrow, field) == getattr(wide, field), field


# --- vertical row budget -----------------------------------------------------
def test_lane_list_rows_caps_at_max():
    assert lane_list_rows(50, available_rows=24) == LANE_LIST_MAX_ROWS


def test_lane_list_rows_matches_agent_count_below_the_cap():
    assert lane_list_rows(3, available_rows=24) == 3


def test_lane_list_rows_is_zero_with_no_agents():
    assert lane_list_rows(0, available_rows=24) == 0


def test_lane_list_collapses_before_transcript_floor_is_violated():
    # A very short terminal: lane list must shrink so MIN_TRANSCRIPT_ROWS
    # still fits, rather than the transcript being starved.
    rows = lane_list_rows(9, available_rows=10)
    assert rows <= 10 - MIN_TRANSCRIPT_ROWS


def test_transcript_rows_subtracts_every_pinned_region():
    # 24 total, 5 lane rows, no permission band: 24 - 1(status) - 5(lanes) - 2(input) = 16
    assert transcript_rows(24, lane_rows=5, permission_band_open=False) == 16


def test_transcript_rows_accounts_for_open_permission_band():
    assert transcript_rows(24, lane_rows=5, permission_band_open=True) == 11


def test_transcript_rows_never_negative():
    assert transcript_rows(5, lane_rows=9, permission_band_open=True) == 0
