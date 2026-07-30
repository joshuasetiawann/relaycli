"""Tests for relaycli/ui/frame.py — the status bar, rules, transcript and
key strip that surround the lane list.

Source of truth is the Claude Design project's `RelayCLI Terminal UI.dc.html`
(§03 hero, §04 the same screen at 80 columns / light / NO_COLOR, §05
density) and its extraction in docs/design/DESIGN_TOKENS.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from relaycli.ui import theme
from relaycli.ui.frame import (
    BUDGET_CRITICAL,
    BUDGET_WARN,
    KeyHint,
    StatusBarData,
    TranscriptEntry,
    budget_fraction,
    budget_meter,
    budget_meter_ascii,
    render_key_strip,
    render_rule,
    render_status_bar,
    render_transcript,
    render_transcript_header,
    short_path,
)
from relaycli.ui.layout import resolve_columns

COLS_120 = resolve_columns(120)
COLS_80 = resolve_columns(80)


def _styles(text) -> set[str]:
    """The hexes actually applied to a rendered row. str(Text) is only the
    plain characters — asserting on that would pass no matter what colour
    the renderer chose, which is the whole thing under test here."""
    return {str(span.style) for span in text.spans}

HERO = StatusBarData(cwd=Path.home() / "src/relay-api", branch="feat/lease-queue", dirty=3,
                     permission_mode="auto-edit", agents=4, tokens=128_400,
                     spent_usd=1.87, limit_usd=3.00)


# --- the budget meter --------------------------------------------------------
def test_no_limit_means_no_meter():
    """§8: the meter is the one bar in the product *because* it has a real,
    user-set denominator. Without a limit there is no denominator."""
    assert budget_fraction(StatusBarData(spent_usd=5.0)) is None
    assert budget_fraction(StatusBarData(spent_usd=5.0, limit_usd=0)) is None


def test_meter_is_five_discrete_segments():
    assert all(len(budget_meter(f / 10)) == 5 for f in range(0, 11))


def test_meter_never_overstates_what_is_left():
    """A filled segment must mean "at least this fifth is gone" — the safe
    direction to be wrong in for a spend meter."""
    for tenth in range(0, 21):
        fraction = tenth / 10
        filled = budget_meter(fraction).count(theme.BUDGET_METER["filled"])
        assert filled / 5 <= max(fraction, 0.2), fraction


def test_any_spend_at_all_lights_the_first_segment():
    assert budget_meter(0.0).count(theme.BUDGET_METER["filled"]) == 0
    assert budget_meter(0.001).count(theme.BUDGET_METER["filled"]) == 1


def test_meter_clamps_past_the_limit_rather_than_growing():
    assert budget_meter(3.0) == theme.BUDGET_METER["filled"] * 5


def test_ascii_meter_is_the_no_color_spelling():
    assert budget_meter_ascii(0.62) == "[###--]"
    assert budget_meter_ascii(0.0) == "[-----]"


# --- the status bar ----------------------------------------------------------
def test_hero_status_bar_matches_the_design():
    bar = render_status_bar(HERO, COLS_120, "dark", 120).plain
    assert "▌relaycli" in bar
    assert "~/src/relay-api" in bar
    assert "git:feat/lease-queue" in bar
    assert "±3" in bar
    assert "mode:auto-edit" in bar
    assert "4 agents" in bar
    assert "128.4k tok" in bar
    assert "$1.87 / 3.00" in bar
    assert "▮▮▮▯▯ 62%" in bar


def test_a_clean_tree_says_clean_rather_than_nothing():
    clean = StatusBarData(cwd=Path.home() / "src/x", branch="main", dirty=0, agents=1)
    assert "clean" in render_status_bar(clean, COLS_120, "dark", 120).plain


def test_one_agent_is_not_pluralised():
    one = StatusBarData(agents=1)
    assert "1 agent " in render_status_bar(one, COLS_120, "dark", 120).plain + " "


def test_compact_bar_at_eighty_columns():
    """§04's narrow variant: the short app name, the directory's basename,
    no git: prefix, no mode, and no percentage beside the meter."""
    bar = render_status_bar(HERO, COLS_80, "dark", 80).plain
    assert "▌relay " in bar
    assert "relaycli" not in bar
    assert "relay-api" in bar
    assert "git:" not in bar
    assert "mode:" not in bar
    assert "4a" in bar
    assert "62%" not in bar


def test_no_color_bar_uses_ascii_throughout():
    bar = render_status_bar(HERO, COLS_80, "no_color", 80).plain
    assert bar.lstrip().startswith("|relay")
    assert "+3" in bar and "±3" not in bar
    assert "[###--]" in bar


@pytest.mark.parametrize("width", [80, 100, 120, 160, 200])
def test_status_bar_fits_its_terminal(width):
    columns = resolve_columns(width)
    long = StatusBarData(cwd=Path("/" + "deep/" * 40), branch="x" * 90, dirty=999,
                         permission_mode="auto-edit", agents=12, tokens=9_000_000,
                         spent_usd=1234.5, limit_usd=2000.0)
    for data in (HERO, long):
        assert render_status_bar(data, columns, "dark", width).cell_len <= width


def test_the_meter_turns_amber_before_it_matters_and_flags_the_ceiling():
    warn = StatusBarData(agents=1, spent_usd=BUDGET_WARN, limit_usd=1.0)
    calm = StatusBarData(agents=1, spent_usd=BUDGET_WARN / 2, limit_usd=1.0)
    critical = StatusBarData(agents=1, spent_usd=BUDGET_CRITICAL, limit_usd=1.0)

    assert theme.DARK.warning in _styles(render_status_bar(warn, COLS_120, "dark", 120))
    assert theme.DARK.success in _styles(render_status_bar(calm, COLS_120, "dark", 120))
    assert theme.MARKERS["escalation"].symbol in render_status_bar(
        critical, COLS_120, "dark", 120).plain


def test_short_path_contracts_home():
    assert short_path(Path.home() / "src/x") == "~/src/x"
    assert short_path(Path("/etc/hosts")) == "/etc/hosts"
    assert short_path(None) == ""


# --- rules and the transcript header ----------------------------------------
def test_rule_leaves_a_gutter_on_both_sides():
    row = render_rule(COLS_120, "dark", 120)
    assert row.cell_len == 119        # 1 column of gutter each side of 118
    assert row.plain.startswith(" ─")


def test_transcript_header_names_the_stream_and_the_mode():
    row = render_transcript_header(COLS_120, "dark", 120, label="a1 ▣ bnd", merged=False)
    assert row.plain.lstrip().startswith("├─ a1 ▣ bnd transcript")
    assert row.plain.rstrip().endswith("focused·merged m")
    assert row.cell_len <= 120


# --- the key strip -----------------------------------------------------------
HINTS = [KeyHint("tab", "lane"), KeyHint("1-9", "jump"), KeyHint("enter", "focus"),
         KeyHint("m", "merged"), KeyHint("^k", "collapse"), KeyHint("x", "drop"),
         KeyHint("R", "retry"), KeyHint("esc", "stop all"), KeyHint("?", "keys")]


@pytest.mark.parametrize("width", [80, 90, 100, 120, 200])
def test_key_strip_never_overflows(width):
    columns = resolve_columns(width)
    assert render_key_strip(HINTS, columns, "dark", width).cell_len <= width


def test_the_help_key_survives_a_strip_that_had_to_be_cut():
    """If hints are being dropped, `?` is precisely the one that tells you
    what was dropped."""
    narrow = render_key_strip(HINTS, COLS_80, "dark", 80).plain
    assert "? keys" in narrow
    assert len(narrow) < len(render_key_strip(HINTS, COLS_120, "dark", 120).plain)


def test_a_badge_rides_along_with_its_key():
    row = render_key_strip([KeyHint("y/n", "answer", "a3"), KeyHint("?", "keys")],
                           COLS_120, "dark", 120).plain
    assert "y/n answer a3" in row


def test_no_color_key_strip_brackets_the_keys():
    row = render_key_strip(HINTS, COLS_80, "no_color", 80).plain
    assert "[tab] lane" in row


def test_empty_hints_do_not_crash():
    assert render_key_strip([], COLS_120, "dark", 120).plain.strip() == ""


# --- the transcript ----------------------------------------------------------
def _entry(**kw):
    base = dict(stamp="14:22:31", kind="tool", task_id="a1", role_id="backend",
                tool="read", target="src/lease/queue.ts")
    base.update(kw)
    return TranscriptEntry(**base)


def test_a_tool_line_shows_tool_target_and_result():
    rows = render_transcript([_entry(text="240 lines")], COLS_120, "dark", 120,
                             merged=False, max_rows=5)
    assert rows[0].plain.lstrip() == "14:22:31 read src/lease/queue.ts · 240 lines"


def test_prose_wraps_here_rather_than_at_the_terminal_edge():
    """§6: streaming text appends and never re-lays-out. A line the
    terminal wrapped would reflow the whole region on the next token."""
    long = "word " * 80
    rows = render_transcript([_entry(kind="text", text=long)], COLS_120, "dark", 120,
                             merged=False, max_rows=99)
    assert len(rows) > 1
    assert all(row.cell_len <= 120 for row in rows)


def test_wrapped_continuations_leave_the_timestamp_column_blank():
    rows = render_transcript([_entry(kind="text", text="word " * 80)], COLS_120, "dark",
                             120, merged=False, max_rows=99)
    assert rows[0].plain.lstrip().startswith("14:22:31")
    assert "14:22:31" not in rows[1].plain


def test_merged_mode_prefixes_every_line_with_its_agent():
    rows = render_transcript([_entry(), _entry(task_id="a4", role_id="tester")],
                             COLS_120, "dark", 120, merged=True, max_rows=9)
    assert "a1 ▣ bnd" in rows[0].plain
    assert "a4 ◈ tst" in rows[1].plain


def test_focused_mode_spends_no_columns_on_a_prefix():
    row = render_transcript([_entry()], COLS_120, "dark", 120,
                            merged=False, max_rows=9)[0]
    assert "a1 ▣ bnd" not in row.plain


def test_only_the_last_rows_survive_a_short_region():
    entries = [_entry(target=f"file{i}.ts") for i in range(20)]
    rows = render_transcript(entries, COLS_120, "dark", 120, merged=False, max_rows=3)
    assert len(rows) == 3
    assert "file19.ts" in rows[-1].plain


def test_a_region_with_no_rows_renders_nothing():
    assert render_transcript([_entry()], COLS_120, "dark", 120,
                             merged=False, max_rows=0) == []


def test_a_failed_result_is_not_coloured_like_a_passing_one():
    ok = render_transcript([_entry(kind="result", text="12 passed", ok=True)],
                           COLS_120, "dark", 120, merged=False, max_rows=1)[0]
    bad = render_transcript([_entry(kind="result", text="1 failed", ok=False)],
                            COLS_120, "dark", 120, merged=False, max_rows=1)[0]
    assert theme.DARK.success in _styles(ok)
    assert theme.DARK.danger in _styles(bad)
    assert theme.DARK.success not in _styles(bad)


@pytest.mark.parametrize("width", [80, 120])
def test_no_transcript_row_ever_overflows(width):
    columns = resolve_columns(width)
    entries = [_entry(target="x" * 300), _entry(kind="text", text="y" * 500),
               _entry(kind="result", text="z" * 300)]
    for merged in (False, True):
        rows = render_transcript(entries, columns, "dark", width,
                                 merged=merged, max_rows=99)
        assert all(row.cell_len <= width for row in rows)
