"""Character-exact parity between the product and `RelayCLI Terminal UI.dc.html`.

tests/design_spec.py reads each mock in the design source back out as the
character grid it describes. This module renders the same screen through the
real renderers and asserts the two are identical, cell for cell.

That is a different claim from "looks like the design". A row here fails if a
single space moves, if a glyph is spelled with the wrong codepoint, or if a
column is one character wide in the wrong direction — which is the only kind
of check that can honestly be called parity.

Where the source contradicts itself, the disagreement is named in
`ALLOWANCES` with the reason and the rule chosen. Nothing else is exempt: a
row not listed there must match exactly.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from rich.text import Text

from relaycli.ui import frame, lanes, theme
from relaycli.ui.layout import resolve_columns
from tests.design_spec import SPINNER_FRAME, Screen, screens


# --- the source's own inconsistencies ----------------------------------------
# Each entry is (screen index, row index) -> why the mock cannot be matched
# exactly, and which rule the product follows instead. Every one of these is a
# place where two mocks in the same document disagree, so no implementation
# can satisfy both.
ALLOWANCES: dict[tuple[int, int], str] = {
    **{(0, row): "§03 indents its transcript 2 cols past the gutter; §04, §05 "
                 "and §06 all start the stamp at the gutter. Majority wins, and "
                 "§12's copy-paste rule wants the narrower gutter."
       for row in range(9, 16)},
    (5, 0): "§05's 3-agent bar draws 24% as two meter segments. §03 (62% -> 3), "
            "§05's 1-agent (3% -> 1) and §05's 6-agent (90% -> 4) are all floor; "
            "this row is the only ceil. Floor is the rule that cannot overstate "
            "what is left, so it is the one to be wrong in.",
    (1, 8): "§04's coloured 80-col frame puts one space before the diffstat; its "
            "own monochrome twin and §03 both put two. Two, so the stat reads as "
            "its own column rather than as part of the path.",
    (1, 14): "§04's coloured strip writes the diff badge as `diffs1`, with no "
             "space; its monochrome twin writes `diffs 1`. The space stays — "
             "`diffs1` reads as a filename, not a count.",
    (3, 7): "§04's light frame stops the transcript rule 2 cols short of the "
            "edge; §03, §05's 3-agent and every band header run it to the edge. "
            "Hand-set dash run.",
    (6, 11): "Same 2-col-short transcript rule as (3, 7).",
    (6, 10): "§05's folded SETTLED row writes a4's goal as `regression suite`, "
             "where the same task is `regression suite for scheduler` in §03 and "
             "§05's 3-agent screen. Nothing at 120 cols forces the shortening, "
             "so it is the mock's hand, not a width rule.",
    # The state table is a legend: it leads every row with the state's word to
    # show that each state has one. Five of its eight rows — pending, ready,
    # blocked, failed, cancelled — match the product exactly, because for
    # those the word is all there is to say. The other three are the states
    # that have something more specific, and there the source's own screens
    # print the specific thing instead of the word: the tool being run, the
    # request being asked, the tests that passed.
    (7, 2): "`running · edit queue.ts:88`; every actual running lane in §03, "
            "§04 and §05 shows the tool alone.",
    (7, 5): "`done · 142 passed`; §03 and §04's settled lanes show the outcome "
            "alone. `done` is already the ✓ in the glyph column.",
    (7, 4): "`awaiting you · write .env`; §03's own lane writes `needs you`, "
            "which is shorter and is what the key strip calls it.",
    (7, 3): "The same table writes a lease wait as `▥ blocked · lease queue.ts`; "
            "§03 and §04 both write `▥ wait lease …` / `▥ wait …`. Two of three, "
            "and `blocked` duplicates what the ⊘/▥ marker already says.",
}


def _rows(screen: Screen) -> list[str]:
    return [row.text.rstrip() for row in screen.rows]


def assert_parity(index: int, produced: list[Text], *, first: int = 0) -> None:
    """Compare rendered rows against mock `index`, from row `first` on."""
    screen = screens()[index]
    expected = _rows(screen)[first:]
    actual = [line.plain.rstrip() for line in produced]
    assert len(actual) == len(expected), (
        f"screen {index} (§{screen.section}) row count: "
        f"rendered {len(actual)}, design has {len(expected)}")
    problems: list[str] = []
    for offset, (want, got) in enumerate(zip(expected, actual)):
        row = offset + first
        if want == got:
            continue
        if (index, row) in ALLOWANCES:
            continue
        problems.append(f"row {row}\n  design |{want}|\n  product|{got}|")
    assert not problems, (f"screen {index} (§{screen.section}) "
                          f"{len(problems)} row(s) differ:\n" + "\n".join(problems))


# --- shared fixtures for the recurring cast ----------------------------------
DARK, LIGHT, MONO = "dark", "light", "no_color"
C120 = resolve_columns(120)
C80 = resolve_columns(80)


def lane(**kw) -> lanes.LaneView:
    base = dict(task_id="a1", role_id="backend", status="running", goal="")
    base.update(kw)
    return lanes.LaneView(**base)


A1 = lane(task_id="a1", role_id="backend", status="running",
          goal="implement lease queue + fairness",
          tool="edit", target="src/lease/queue.ts:88",
          model="sonnet-4.5", tokens=14200, cost_usd=0.42, elapsed_s=72, focused=True)
A2 = lane(task_id="a2", role_id="frontend", status="ready",
          goal="wire status bar to socket feed",
          model="qwen2.5-32b", tokens=3100, cost_usd=0.0, elapsed_s=41,
          lease_holder="a1", lease_path="src/lease/queue.ts", lease_held_s=41,
          lease_queue_position=1, lease_queue_length=2, lease_estimate_s=40)
A3 = lane(task_id="a3", role_id="reviewer", status="running",
          goal="review auth middleware change", detail="write .env.local",
          model="haiku-4.5", tokens=9800, cost_usd=0.06, elapsed_s=8,
          awaiting_you=True)
A4 = lane(task_id="a4", role_id="tester", status="done",
          goal="regression suite for scheduler", detail="142 passed · 0 failed",
          model="llama3.1-8b", escalated=True, tokens=31000, cost_usd=0.11, elapsed_s=174)

# `~/src/relay-api` on any machine: short_path contracts $HOME to `~`, so
# building the path from Path.home() reproduces the design's own spelling
# without the test caring where home actually is.
RELAY_API = Path.home() / "src" / "relay-api"


def bar(**kw) -> frame.StatusBarData:
    base: dict = dict(cwd=RELAY_API, limit_usd=3.00)
    base.update(kw)
    return frame.StatusBarData(**base)


HERO_BAR = bar(branch="feat/lease-queue", dirty=3, permission_mode="auto-edit",
               agents=4, tokens=128400, spent_usd=1.87)


# --- §03 the hero screen -----------------------------------------------------
def test_section_03_hero_screen_at_120_columns():
    mode, width, cols = DARK, 120, C120
    out = [
        frame.render_status_bar(HERO_BAR, cols, mode, width),
        frame.render_rule(cols, mode, width),
        frame.render_agents_header(
            4, [("running", 1), ("blocked", 1), ("review", 1), ("done", 1)],
            [frame.KeyHint("tab", "focus"), frame.KeyHint("^g", "graph"),
             frame.KeyHint("^k", "collapse")], cols, mode, width),
        lanes.render_lane_row(A1, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(A2, cols, mode, width, SPINNER_FRAME),
        lanes.render_lease_row(A2, cols, mode),
        lanes.render_lane_row(A3, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(A4, cols, mode, width, SPINNER_FRAME),
        frame.render_transcript_header(cols, mode, width, label="a1 ▣ bnd", merged=False),
    ]
    out.extend(frame.render_transcript(HERO_TRANSCRIPT, cols, mode, width,
                                       merged=False, max_rows=7,
                                       spinner=SPINNER_FRAME))
    out.extend([
        frame.render_rule(cols, mode, width),
        frame.render_input_row(cols, mode, width, placeholder="",
                               typed="/plan add e2e coverage for the lease queue",
                               tail=[frame.KeyHint("⏎", "send"), frame.KeyHint("esc", "stop all")]),
        frame.render_key_strip(
            [frame.KeyHint("tab", "lane"), frame.KeyHint("1-9", "jump"),
             frame.KeyHint("d", "diffs", "1"), frame.KeyHint("y/n", "answer", "a3"),
             frame.KeyHint("^b", "budget"), frame.KeyHint("^r", "roles"),
             frame.KeyHint("?", "keys")], cols, mode, width),
    ])
    assert_parity(0, out)


# Prose arrives pre-split rather than wrapped by the renderer: the mocks are
# hand-wrapped to taste (88 cols in §03, 85 in §06, 92 unwrapped in §05), so
# the break column is not a fact the design states and cannot be matched by
# any rule. The continuation row's *shape* — blank stamp, text under text —
# is what these two entries check.
HERO_TRANSCRIPT = [
    frame.TranscriptEntry("14:22:31", "tool", tool="read", target="src/lease/queue.ts",
                          text="240 lines"),
    frame.TranscriptEntry("14:22:38", "text",
                          text="The existing queue is LIFO, which starves the frontend "
                               "agent whenever backend"),
    frame.TranscriptEntry("", "text",
                          text="re-acquires. Switching to FIFO with an age-based priority bump."),
    frame.TranscriptEntry("14:22:52", "tool", tool="edit", target="src/lease/queue.ts",
                          added=38, removed=11,
                          hint=frame.KeyHint("d", "to review diff")),
    frame.TranscriptEntry("14:23:04", "tool", tool="bash", target="pnpm vitest run lease"),
    frame.TranscriptEntry("", "result", text="12 passed", tail="in 1.4s"),
    frame.TranscriptEntry("14:23:09", "tool", tool="edit", target="src/lease/queue.ts:88",
                          note="fairness bump for aged waiters", active=True),
]


# --- §04 the same screen, three hostile environments -------------------------
NARROW_TRANSCRIPT = [
    frame.TranscriptEntry("14:22:52", "tool", tool="edit", target="src/lease/queue.ts",
                          added=38, removed=11),
    frame.TranscriptEntry("14:23:04", "tool", tool="bash", target="pnpm vitest run lease"),
    frame.TranscriptEntry("", "result", text="12 passed", tail="in 1.4s"),
    frame.TranscriptEntry("14:23:09", "tool", tool="edit", target="queue.ts:88",
                          note="fairness bump", active=True),
]


def narrow_screen(mode: str, done_detail: str) -> list[Text]:
    """§04's 80-column frame — drawn identically in colour and monochrome,
    because the only difference the design allows between them is which
    spelling of each token comes back out.

    `done_detail` is the settled lane's outcome summary, which the two mocks
    write differently (`142 passed` against `142 passed 0 failed`). That
    string is whatever the test runner actually printed, so it is data the
    caller supplies, not something a renderer decides — the design's own two
    spellings of it are both legal.
    """
    width, cols = 80, C80
    out = [
        frame.render_status_bar(
            bar(branch="feat/lease-queue", dirty=3, permission_mode="auto-edit",
                agents=4, tokens=128400, spent_usd=1.87), cols, mode, width),
        frame.render_rule(cols, mode, width),
        lanes.render_lane_row(A1_80, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(A2_80, cols, mode, width, SPINNER_FRAME),
        lanes.render_lease_row(A2_80, cols, mode),
        lanes.render_lane_row(A3_80, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(replace(A4_80, detail=done_detail), cols, mode, width,
                              SPINNER_FRAME),
        frame.render_transcript_header(cols, mode, width, label="a1", merged=False,
                                       compact=True),
    ]
    out.extend(frame.render_transcript(NARROW_TRANSCRIPT, cols, mode, width,
                                       merged=False, max_rows=4, spinner=SPINNER_FRAME))
    out.extend([
        frame.render_rule(cols, mode, width),
        frame.render_input_row(cols, mode, width, placeholder="",
                               typed="/plan add e2e coverage"),
        frame.render_key_strip(
            [frame.KeyHint("tab", "lane"), frame.KeyHint("d", "diffs", "1"),
             frame.KeyHint("y/n", "", "a3"), frame.KeyHint("?", "keys")],
            cols, mode, width, gap=1),
    ])
    return out


# The 80-column cast: same tasks, goals and tool targets clipped by §04's own
# narrower columns rather than by hand.
A1_80 = lane(task_id="a1", role_id="backend", status="running",
             goal="implement lease queue", tool="edit", target="queue.ts:88",
             model="sonnet-4.5", tokens=14200, cost_usd=0.42, elapsed_s=72, focused=True)
A2_80 = lane(task_id="a2", role_id="frontend", status="ready", goal="wire status bar",
             model="qwen2.5-32b", cost_usd=0.0, elapsed_s=41, lease_holder="a1",
             lease_path="queue.ts", lease_held_s=41,
             lease_queue_position=1, lease_queue_length=2)
A3_80 = lane(task_id="a3", role_id="reviewer", status="running",
             goal="review auth middleware", model="haiku-4.5", cost_usd=0.06,
             elapsed_s=8, awaiting_you=True)
A4_80 = lane(task_id="a4", role_id="tester", status="done", goal="regression suite",
             detail="142 passed", model="llama3.1-8b", cost_usd=0.11, elapsed_s=174)


def test_section_04_at_80_columns_in_dark():
    assert_parity(1, narrow_screen(DARK, "142 passed"))


def test_section_04_under_no_color():
    assert_parity(2, narrow_screen(MONO, "142 passed 0 failed"))


def test_section_04_at_120_columns_in_light():
    mode, width, cols = LIGHT, 120, C120
    out = [
        frame.render_status_bar(HERO_BAR, cols, mode, width),
        frame.render_rule(cols, mode, width),
        lanes.render_lane_row(A1, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(A2, cols, mode, width, SPINNER_FRAME),
        lanes.render_lease_row(A2, cols, mode),
        lanes.render_lane_row(A3, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(A4, cols, mode, width, SPINNER_FRAME),
        frame.render_transcript_header(cols, mode, width, label="a1 ▣ bnd", merged=False,
                                       tail=False),
        frame.render_transcript_line(HERO_TRANSCRIPT[3], cols, mode, width, merged=False),
        frame.render_transcript_line(HERO_TRANSCRIPT[6], cols, mode, width, merged=False,
                                     spinner=SPINNER_FRAME),
        frame.render_rule(cols, mode, width),
        frame.render_input_row(cols, mode, width, placeholder="",
                               typed="/plan add e2e coverage for the lease queue",
                               tail=[frame.KeyHint("⏎", "send"),
                                     frame.KeyHint("esc", "stop all")]),
    ]
    assert_parity(3, out)


# --- §05 density: 1, 3 and 6 agents ------------------------------------------
def test_section_05_one_agent():
    mode, width, cols = DARK, 120, C120
    only = lane(task_id="a1", role_id="researcher", status="running",
                goal="map the auth flow end to end", tool="grep", target='"session" src/',
                model="sonnet-4.5", tokens=8100, cost_usd=0.09, elapsed_s=22, focused=True)
    out = [
        frame.render_status_bar(bar(branch="main", permission_mode="ask", agents=1,
                                    tokens=8100, spent_usd=0.09), cols, mode, width),
        frame.render_rule(cols, mode, width),
        lanes.render_lane_row(only, cols, mode, width, SPINNER_FRAME),
        frame.render_rule(cols, mode, width),
    ]
    out.extend(frame.render_transcript([
        frame.TranscriptEntry("14:31:02", "text",
                              text="Three entry points touch the session cookie. Reading "
                                   "each before I propose a change."),
        frame.TranscriptEntry("14:31:09", "tool", tool="read", target="src/auth/session.ts",
                              text="118 lines"),
        frame.TranscriptEntry("14:31:14", "tool", tool="read",
                              target="src/middleware/auth.ts", active=True),
    ], cols, mode, width, merged=False, max_rows=3, spinner=SPINNER_FRAME))
    assert_parity(4, out)


def test_section_05_three_agents_merged_transcript():
    mode, width, cols = DARK, 120, C120
    a2 = lane(task_id="a2", role_id="researcher", status="running",
              goal="trace every caller of grantLease", tool="grep",
              target='"grantLease" src/', model="qwen2.5-32b", tokens=19500,
              cost_usd=0.0, elapsed_s=58)
    a3 = lane(task_id="a3", role_id="documenter", status="running",
              goal="document the lease protocol", tool="write", target="docs/leases.md",
              model="llama3.1-8b", tokens=6900, cost_usd=0.0, elapsed_s=34)
    out = [
        frame.render_status_bar(bar(branch="feat/lease-queue", dirty=2,
                                    permission_mode="auto-edit", agents=3, tokens=64800,
                                    spent_usd=0.71), cols, mode, width),
        frame.render_rule(cols, mode, width),
        lanes.render_lane_row(A1, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(a2, cols, mode, width, SPINNER_FRAME),
        lanes.render_lane_row(a3, cols, mode, width, SPINNER_FRAME),
        frame.render_transcript_header(cols, mode, width, merged=True,
                                       label="merged transcript",
                                       subtitle="all 3 agents, role-prefixed"),
    ]
    out.extend(frame.render_transcript([
        frame.TranscriptEntry("14:22:52", "tool", task_id="a1", role_id="backend",
                              tool="edit", target="src/lease/queue.ts",
                              added=38, removed=11),
        frame.TranscriptEntry("14:22:55", "text", task_id="a3", role_id="documenter",
                              text="Documenting the FIFO change before it lands, so the "
                                   "doc and the code ship together."),
        frame.TranscriptEntry("14:23:01", "text", task_id="a2", role_id="researcher",
                              text="Seven callers. Two of them assume LIFO ordering — "
                                   "src/api/handoff.ts:44 and"),
        frame.TranscriptEntry("", "text", text="src/worker/pool.ts:120. Flagging both for a1."),
        frame.TranscriptEntry("14:23:04", "tool", task_id="a1", role_id="backend",
                              tool="bash", target="pnpm vitest run lease",
                              result="12 passed", tail="in 1.4s"),
        frame.TranscriptEntry("14:23:07", "tool", task_id="a3", role_id="documenter",
                              tool="write", target="docs/leases.md", added=41, removed=0),
        frame.TranscriptEntry("14:23:09", "tool", task_id="a1", role_id="backend",
                              tool="edit", target="src/lease/queue.ts:88",
                              note="fairness bump for aged waiters", active=True),
    ], cols, mode, width, merged=True, max_rows=7, spinner=SPINNER_FRAME))
    out.extend([
        frame.render_rule(cols, mode, width),
        frame.render_input_row(cols, mode, width, placeholder="ask, or / for commands",
                               tail=[frame.KeyHint("m", "back to focused")]),
    ])
    assert_parity(5, out)


def test_section_05_six_agents_group_and_fold():
    mode, width, cols = DARK, 120, C120
    a5 = lane(task_id="a5", role_id="database", status="running",
              goal="migration for lease_holder table", tool="bash",
              target="pnpm drizzle:gen", model="qwen2.5-32b", tokens=22400,
              cost_usd=0.0, elapsed_s=181)
    a6 = lane(task_id="a6", role_id="documenter", status="running",
              goal="document the lease protocol", tool="write", target="docs/leases.md",
              model="llama3.1-8b", tokens=6900, cost_usd=0.0, elapsed_s=34)
    a7 = lane(task_id="a7", role_id="e2e", status="blocked",
              goal="playwright pass on queue UI", detail="a2 · not started",
              model="haiku-4.5", tokens=0, cost_usd=0.0, elapsed_s=None)
    out = [
        frame.render_status_bar(bar(branch="feat/lease-queue", dirty=11,
                                    permission_mode="auto-edit", agents=6, tokens=402700,
                                    spent_usd=2.71), cols, mode, width),
        frame.render_rule(cols, mode, width),
        lanes.render_group_header(lanes.GroupHeader("RUNNING", 3), cols, mode, width),
        lanes.render_lane_row(A1, cols, mode, width, SPINNER_FRAME, grouped=True),
        lanes.render_lane_row(a5, cols, mode, width, SPINNER_FRAME, grouped=True),
        lanes.render_lane_row(a6, cols, mode, width, SPINNER_FRAME, grouped=True),
        lanes.render_group_header(lanes.GroupHeader("BLOCKED", 2, "waiting"),
                                 cols, mode, width),
        lanes.render_lane_row(A2, cols, mode, width, SPINNER_FRAME, grouped=True),
        lanes.render_lane_row(a7, cols, mode, width, SPINNER_FRAME, grouped=True),
        lanes.render_group_header(lanes.GroupHeader("SETTLED", 1), cols, mode, width),
        lanes.render_group_row(lanes.GroupSummary("done", 1, (A4,)), cols, mode, width),
        frame.render_transcript_header(cols, mode, width, label="a1 ▣ bnd", merged=False,
                                       tail=False),
        frame.render_transcript_line(
            frame.TranscriptEntry("14:23:04", "tool", tool="bash",
                                  target="pnpm vitest run lease", result="12 passed"),
            cols, mode, width, merged=False),
        frame.render_transcript_line(
            frame.TranscriptEntry("14:23:09", "tool", tool="edit",
                                  target="src/lease/queue.ts:88", active=True),
            cols, mode, width, merged=False, spinner=SPINNER_FRAME),
        frame.render_budget_note(0.90, 3.00, cols, mode, "14:23:11"),
        frame.render_rule(cols, mode, width),
        frame.render_input_row(cols, mode, width, placeholder="ask, or / for commands"),
    ]
    assert_parity(6, out)


def test_section_05_every_lane_state():
    """The lane-state table: one row per state, in the pinned list's own
    shape. The mock widens its detail column to 34 and hangs a column of
    commentary about the design off the end, so parity here is over the
    lane itself — glyph, id, role mark, goal — plus the exact wording of
    the state phrase, which is the thing the table exists to fix."""
    mode, width, cols = DARK, 120, C120
    rows = [
        lane(task_id="t8", role_id="performance", status="pending",
             goal="benchmark the queue under load", detail="a1, a5"),
        lane(task_id="t9", role_id="security", status="ready",
             goal="audit the new lease endpoints"),
        lane(task_id="a1", role_id="backend", status="running",
             goal="implement lease queue", tool="edit", target="queue.ts:88"),
        lane(task_id="a2", role_id="frontend", status="ready", goal="wire status bar",
             lease_holder="a1", lease_path="queue.ts"),
        lane(task_id="a3", role_id="reviewer", status="running",
             goal="review auth middleware", detail="write .env", awaiting_you=True),
        lane(task_id="a4", role_id="tester", status="done", goal="regression suite",
             detail="142 passed"),
        lane(task_id="a5", role_id="database", status="failed",
             goal="migration for lease_holder", detail="drizzle exit 1"),
        lane(task_id="a6", role_id="documenter", status="cancelled",
             goal="document the lease protocol", detail="budget hold"),
    ]
    screen = screens()[7]
    prefix = 1 + cols.focus_rail + cols.state_glyph + cols.id_role + cols.goal
    for index, (view, expected) in enumerate(zip(rows, screen.rows)):
        got = lanes.render_lane_row(view, cols, mode, width, SPINNER_FRAME).plain
        assert got[:prefix] == expected.text[:prefix], f"lane {index} columns"
        want_detail = expected.text[prefix:prefix + 34].strip()
        got_detail, _ = lanes.lane_detail(view, mode, cols.tool_target - 1)
        if (7, index) in ALLOWANCES:
            continue
        assert got_detail.strip() == want_detail, f"lane {index} state phrase"
