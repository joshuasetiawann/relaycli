"""Tests for relaycli/ui/lanes.py — the pure lane-list row renderer
(docs/design/DESIGN_TOKENS.md §2, §4, §9)."""

from __future__ import annotations

import pytest

from relaycli.ui.lanes import (
    GroupHeader,
    GroupSummary,
    LaneView,
    basename_target,
    clip_goal,
    format_cost,
    format_elapsed,
    format_model,
    format_tokens,
    format_tool_target,
    group_for_display,
    id_role_label,
    lane_detail,
    render_group_row,
    render_lane_row,
    render_lease_row,
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


@pytest.mark.parametrize("seconds,expected", [(0, "0m00s"), (45, "0m45s"), (65, "1m05s"),
                                               (72, "1m12s"), (174, "2m54s"), (3725, "1h02m")])
def test_format_elapsed(seconds, expected):
    assert format_elapsed(seconds) == expected


def test_format_elapsed_always_spells_the_minutes():
    # The design's own rows read 0m41s / 0m08s, never 41s / 8s: the column
    # has to be one shape so it scans vertically as a single duration.
    assert all(format_elapsed(s).count("m") == 1 for s in (0, 5, 41, 59))


def test_format_elapsed_never_started_is_an_em_dash_not_zero():
    # "0s" would claim the task ran and took no time.
    assert format_elapsed(None) == "—"


def test_format_elapsed_never_negative_for_negative_input():
    assert format_elapsed(-5) == "0m00s"


def test_format_cost_uses_the_two_decimals_the_design_shows():
    assert format_cost(0.42) == "$0.42"
    assert format_cost(1.87) == "$1.87"


def test_format_cost_never_prints_a_real_charge_as_free():
    # Two decimals cannot hold $0.004; rendering it "$0.00" would say the
    # task was free. Zero itself still reads "$0.00".
    assert format_cost(0.0) == "$0.00"
    assert format_cost(0.004) == "<$0.01"
    assert format_cost(0.0001) == "<$0.01"


def test_id_role_label_carries_the_family_glyph():
    assert id_role_label("a1", "backend") == "a1 ▣ bnd"
    assert id_role_label("a4", "tester") == "a4 ◈ tst"


def test_id_role_label_falls_back_to_the_bare_id_for_an_unknown_role():
    assert id_role_label("a9", "not-a-role") == "a9"


def test_format_model_keeps_the_escalation_marker_when_it_has_to_clip():
    # The ▲ is the part that changes the line's meaning, so the name is
    # what gives way, never the marker.
    result = format_model("a-very-long-model-name-indeed", escalated=True, width=14)
    assert result.endswith(" ▲")
    assert len(result) <= 14


def test_format_model_is_empty_when_no_model_has_been_observed():
    assert format_model("", escalated=False, width=14) == ""


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
    assert "$4.50" in row.plain


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
    result = group_for_display(lanes, max_rows=99)
    active = [r for r in result if isinstance(r, LaneView)]
    groups = {(g.status, g.count) for r in result if isinstance(r, GroupSummary)
              for g in [r]}
    assert len(active) == 2
    assert ("done", 4) in groups
    assert ("failed", 1) in groups


def test_above_threshold_the_bands_get_their_own_headers():
    lanes = ([_lane(task_id=f"r{i}", status="running") for i in range(3)]
             + [_lane(task_id="b1", status="blocked")]
             + [_lane(task_id="n1", status="running", awaiting_you=True)]
             + [_lane(task_id="d1", status="done")])
    labels = [r.label for r in group_for_display(lanes, max_rows=99)
              if isinstance(r, GroupHeader)]
    assert labels == ["RUNNING", "BLOCKED", "NEEDS YOU", "SETTLED"]


def test_every_lane_lands_in_exactly_one_band():
    # The bands are the whole list, not a filtered view of it: a lane that
    # fell out of all three would silently vanish from a pinned region.
    lanes = [_lane(task_id=f"t{i}", status=s) for i, s in enumerate(
        ["pending", "ready", "running", "blocked", "done", "failed", "cancelled"])]
    displayed = group_for_display(lanes, max_rows=99)
    seen = {l.task_id for l in displayed if isinstance(l, LaneView)}
    seen |= {l.task_id for g in displayed if isinstance(g, GroupSummary) for l in g.lanes}
    assert seen == {l.task_id for l in lanes}


def test_a_focused_settled_lane_stays_expanded():
    # The cursor must never point at a row grouping has folded away.
    lanes = ([_lane(task_id=f"r{i}", status="running") for i in range(5)]
             + [_lane(task_id="d1", status="done", focused=True)])
    displayed = group_for_display(lanes, max_rows=99)
    assert "d1" in {l.task_id for l in displayed if isinstance(l, LaneView)}


def test_grouping_respects_max_rows():
    lanes = [_lane(task_id=f"t{i}", status="running") for i in range(20)]
    result = group_for_display(lanes, max_rows=9)
    assert len(result) <= 9


def test_render_group_row_shows_count():
    row = render_group_row(GroupSummary(status="done", count=12), COLS_120, "dark")
    assert "12" in row.plain
    assert "done" in row.plain.lower()


def test_a_single_folded_lane_is_still_named():
    # A bare "1 done" hides the one thing you ask a folded row: which task?
    only = _lane(task_id="a4", role_id="tester", status="done",
                 goal="regression suite", tokens=31000, cost_usd=0.11)
    row = render_group_row(GroupSummary(status="done", count=1, lanes=(only,)),
                           COLS_120, "dark")
    assert "a4 ◈ tst" in row.plain
    assert "regression suite" in row.plain
    assert "$0.11" in row.plain


# --- the design's own rows, character for character --------------------------
def test_lane_row_fits_the_column_budget_exactly():
    # §4's widths are inclusive of their own trailing gap. Adding a
    # separator on top of them pushed the row to 121 characters, four wide
    # of a 120-column terminal, and every row wrapped.
    assert render_lane_row(_lane(), COLS_120, "dark").cell_len == 117
    assert render_lane_row(_lane(), COLS_80, "dark").cell_len == 75


def test_a_full_width_goal_still_leaves_a_gap_before_the_next_column():
    lane = _lane(goal="x" * COLS_120.goal, tool="edit", target="a.py")
    row = render_lane_row(lane, COLS_120, "dark").plain
    assert "xedit" not in row


def test_focused_row_paints_across_the_whole_line():
    row = render_lane_row(_lane(focused=True), COLS_120, "dark", width=120)
    assert row.cell_len == 120


def test_unfocused_row_has_no_background():
    row = render_lane_row(_lane(), COLS_120, "dark", width=120)
    assert "on " not in str(row.style)


def test_the_focus_rail_only_appears_on_the_focused_lane():
    assert "▌" in render_lane_row(_lane(focused=True), COLS_120, "dark").plain
    assert "▌" not in render_lane_row(_lane(), COLS_120, "dark").plain


def test_hero_row_matches_the_design():
    lane = LaneView(task_id="a1", role_id="backend", status="running",
                    goal="implement lease queue + fairness", tool="edit",
                    target="src/lease/queue.ts:88", model="sonnet-4.5",
                    tokens=14200, cost_usd=0.42, elapsed_s=72)
    row = render_lane_row(lane, COLS_120, "dark").plain
    assert "a1 ▣ bnd" in row
    assert "edit src/lease/queue.ts:88" in row
    assert "14.2k" in row
    assert "$0.42" in row
    assert "1m12s" in row


# --- the detail column -------------------------------------------------------
def test_a_lease_conflict_reads_as_blocked_not_ready():
    # The scheduler leaves such a task "ready" and skips it every round;
    # showing "ready" would name its bookkeeping, not the user's problem.
    lane = _lane(status="ready", lease_holder="a1",
                 lease_path="src/lease/queue.ts", lease_held_s=41)
    row = render_lane_row(lane, COLS_120, "dark").plain
    assert "⊘" in row
    assert "▥ lease queue.ts · a1 · 0m41s" in row


def test_lease_row_reports_only_what_is_measured():
    # No queue position and no estimate: LeaseManager has neither, and §8
    # rules out inventing them.
    lane = _lane(status="ready", lease_holder="a1",
                 lease_path="src/lease/queue.ts", lease_held_s=41)
    row = render_lease_row(lane, COLS_120, "dark").plain
    assert "held by a1" in row
    assert "0m41s" in row
    assert "queue pos" not in row
    assert "est" not in row


def test_a_running_lane_with_no_open_tool_says_so():
    # A blank column reads as a stalled agent.
    text, token = lane_detail(_lane(status="running"), "dark", 30)
    assert text == "running"
    assert token == "running"


def test_detail_never_ends_on_a_dangling_separator():
    lane = _lane(status="ready", lease_holder="a-very-long-holder-id",
                 lease_path="some/deep/path/queue.ts", lease_held_s=41)
    text, _ = lane_detail(lane, "dark", 22)
    assert not text.rstrip().endswith("·")


@pytest.mark.parametrize("status,token", [
    ("pending", "muted"), ("ready", "text"), ("running", "running"),
    ("blocked", "waiting"), ("done", "success"), ("failed", "danger"),
    ("cancelled", "muted"),
])
def test_each_state_colours_its_detail_column(status, token):
    assert lane_detail(_lane(status=status), "dark", 30)[1] == token


def test_settled_lanes_dim_but_keep_their_cost():
    row = render_lane_row(_lane(status="done", cost_usd=0.11), COLS_120, "dark")
    assert "$0.11" in row.plain
