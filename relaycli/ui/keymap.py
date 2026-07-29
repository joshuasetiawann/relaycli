"""Key map for the live parallel view — DESIGN_TOKENS.md §7.

Pure, like layout.py and lanes.py: this module turns a keypress into an
Action and an Action into the next ViewState. It never touches a
terminal, a thread or a Scheduler, so every transition here is testable
without a TTY. ui/live.py owns the termios/reader-thread half.

Only the navigation subset of §7 is implemented (see KEY_HELP): the rest
of the map — steer, preempt/retarget, retry/drop, the diff queue and its
consent keys — needs Scheduler-level task steering that doesn't exist
yet, and would be a promise the UI can't keep.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

Action = Literal[
    "none", "next_lane", "prev_lane", "jump_lane", "focus", "back",
    "toggle_help", "toggle_lane_list",
]

# Raw byte sequences, already disambiguated by the reader (a bare ESC is
# only reported as ESC once it's clear no sequence follows it).
KEY_TAB = "\t"
KEY_SHIFT_TAB = "\x1b[Z"
KEY_ENTER = "\r"
KEY_ESC = "\x1b"
KEY_CTRL_K = "\x0b"

# Shown by `?`. Kept next to the mapping itself so the overlay can never
# drift from what the keys actually do.
KEY_HELP: tuple[tuple[str, str], ...] = (
    ("tab / shift-tab", "next / previous lane"),
    ("1-9", "jump to lane"),
    ("enter", "focus the selected lane"),
    ("esc", "back out one level (at top level: stop every agent)"),
    ("^k", "collapse / expand the lane list"),
    ("?", "toggle this overlay"),
)


@dataclass(frozen=True)
class KeyAction:
    action: Action
    lane_number: int | None = None


@dataclass(frozen=True)
class ViewState:
    """What the live frame draws on top of Scheduler state. `selected` is
    an index into the full lane list (graph order), not into the possibly
    grouped display list — grouping must not move the cursor."""

    selected: int = 0
    focused: bool = False
    show_help: bool = False
    lane_list_collapsed: bool = False
    stop_requested: bool = False


def parse_key(key: str) -> KeyAction:
    """Map one keypress to an Action. Unknown keys are "none" rather than
    an error — a stray escape sequence from a resize or a mouse must not
    take the run down."""
    if key == KEY_TAB:
        return KeyAction("next_lane")
    if key == KEY_SHIFT_TAB:
        return KeyAction("prev_lane")
    if key in (KEY_ENTER, "\n"):
        return KeyAction("focus")
    if key == KEY_ESC:
        return KeyAction("back")
    if key == "?":
        return KeyAction("toggle_help")
    if key == KEY_CTRL_K:
        return KeyAction("toggle_lane_list")
    if len(key) == 1 and key.isdigit() and key != "0":
        return KeyAction("jump_lane", lane_number=int(key))
    return KeyAction("none")


def apply_action(state: ViewState, key_action: KeyAction, lane_count: int) -> ViewState:
    """The next ViewState. `lane_count` can legitimately be 0 (the graph
    isn't built yet), so every index move guards against it rather than
    dividing by zero."""
    action = key_action.action

    if action == "next_lane" and lane_count:
        return replace(state, selected=(state.selected + 1) % lane_count)
    if action == "prev_lane" and lane_count:
        return replace(state, selected=(state.selected - 1) % lane_count)
    if action == "jump_lane" and key_action.lane_number is not None:
        index = key_action.lane_number - 1
        if 0 <= index < lane_count:
            return replace(state, selected=index)
        return state  # out of range: ignore, don't clamp to a lane the user didn't press
    if action == "focus" and lane_count:
        return replace(state, focused=True)
    if action == "toggle_help":
        return replace(state, show_help=not state.show_help)
    if action == "toggle_lane_list":
        return replace(state, lane_list_collapsed=not state.lane_list_collapsed)
    if action == "back":
        # §7: "back out one level (stop all agents at top level)". Peel the
        # transient layers first so esc can never stop a run while an
        # overlay the user was reading is still covering the screen.
        if state.show_help:
            return replace(state, show_help=False)
        if state.focused:
            return replace(state, focused=False)
        return replace(state, stop_requested=True)
    return state


def handle_key(state: ViewState, key: str, lane_count: int) -> ViewState:
    """parse + apply, the entry point ui/live.py's reader thread uses."""
    return apply_action(state, parse_key(key), lane_count)
