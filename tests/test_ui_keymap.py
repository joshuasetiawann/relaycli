"""Tests for relaycli/ui/keymap.py and keyreader.py — the live view's key
map (DESIGN_TOKENS.md §7).

keymap is pure, so every transition is asserted directly. keyreader owns
the terminal, so only its TTY guard and its ESC/escape-sequence
disambiguation are covered here — driving a real cbreak terminal is out
of reach for this suite (and ui/live.py's own frame tests already cover
what the state ends up rendering as)."""

from __future__ import annotations

from relaycli.ui import keymap, keyreader
from relaycli.ui.keymap import (KeyAction, ViewState, apply_action, handle_key, parse_key,
                                steer_key)


# --- parse_key ---------------------------------------------------------
def test_navigation_keys_parse():
    assert parse_key("\t") == KeyAction("next_lane")
    assert parse_key("\x1b[Z") == KeyAction("prev_lane")
    assert parse_key("\r") == KeyAction("focus")
    assert parse_key("\n") == KeyAction("focus")
    assert parse_key("\x1b") == KeyAction("back")
    assert parse_key("?") == KeyAction("toggle_help")
    assert parse_key("\x0b") == KeyAction("toggle_lane_list")


def test_digit_keys_jump_to_a_lane():
    for digit in range(1, 10):
        assert parse_key(str(digit)) == KeyAction("jump_lane", lane_number=digit)


def test_zero_is_not_a_lane_jump():
    """§7 says 1-9; lanes are 1-indexed on screen, so 0 addresses nothing."""
    assert parse_key("0") == KeyAction("none")


def test_unknown_keys_are_inert_rather_than_an_error():
    """A resize or mouse report can deliver a sequence this map has no
    binding for; swallowing it must not disturb the run."""
    for key in ("q", "\x1b[<0;1;1M", "\x7f", "", "ab"):
        assert parse_key(key) == KeyAction("none")


# --- lane navigation ---------------------------------------------------
def test_tab_advances_and_wraps():
    state = ViewState()
    state = handle_key(state, "\t", 3)
    assert state.selected == 1
    state = handle_key(state, "\t", 3)
    assert state.selected == 2
    state = handle_key(state, "\t", 3)
    assert state.selected == 0, "tab should wrap at the end of the list"


def test_shift_tab_goes_backwards_and_wraps():
    state = handle_key(ViewState(), "\x1b[Z", 3)
    assert state.selected == 2, "shift-tab from the first lane wraps to the last"


def test_digit_jump_selects_that_lane():
    assert handle_key(ViewState(), "3", 5).selected == 2


def test_out_of_range_jump_is_ignored_not_clamped():
    """Pressing 9 with three lanes should do nothing — silently landing on
    lane 3 would move the cursor somewhere the user didn't ask for."""
    state = ViewState(selected=1)
    assert handle_key(state, "9", 3).selected == 1


def test_navigation_is_safe_before_the_graph_exists():
    """lane_count is 0 between the run starting and the orchestrator
    returning a graph; a modulo there would raise ZeroDivisionError."""
    for key in ("\t", "\x1b[Z", "1", "\r"):
        assert handle_key(ViewState(), key, 0) == ViewState()


# --- focus / overlays --------------------------------------------------
def test_enter_focuses_the_selected_lane():
    assert handle_key(ViewState(selected=2), "\r", 4).focused is True


def test_help_overlay_toggles():
    state = handle_key(ViewState(), "?", 3)
    assert state.show_help is True
    assert handle_key(state, "?", 3).show_help is False


def test_lane_list_collapse_toggles():
    state = handle_key(ViewState(), "\x0b", 3)
    assert state.lane_list_collapsed is True
    assert handle_key(state, "\x0b", 3).lane_list_collapsed is False


# --- esc: the hierarchy that guards against stopping a run by accident --
def test_esc_closes_help_before_anything_else():
    state = ViewState(show_help=True, focused=True)
    state = handle_key(state, "\x1b", 3)
    assert state.show_help is False
    assert state.focused is True, "esc peels one layer at a time"
    assert state.stop_requested is False


def test_esc_leaves_focus_before_stopping():
    state = handle_key(ViewState(focused=True), "\x1b", 3)
    assert state.focused is False
    assert state.stop_requested is False


def test_esc_at_top_level_requests_stop():
    assert handle_key(ViewState(), "\x1b", 3).stop_requested is True


def test_esc_cannot_stop_the_run_while_an_overlay_is_open():
    """Three escs from "help open, lane focused" must peel help, then
    focus, and only then stop — never stop a live run under an overlay
    the user is still looking at."""
    state = ViewState(show_help=True, focused=True)
    stops = []
    for _ in range(3):
        state = handle_key(state, "\x1b", 3)
        stops.append(state.stop_requested)
    assert stops == [False, False, True]


# --- keyreader ---------------------------------------------------------
def test_reader_is_inert_without_a_tty(monkeypatch):
    """Piped output, CI and this test suite all have a non-tty stdin. The
    reader must not raise, and must never put a terminal it doesn't own
    into cbreak mode."""
    monkeypatch.setattr(keyreader, "stdin_is_interactive", lambda: False)
    calls = []
    stopper = keyreader.start(calls.append)
    stopper()  # must be callable and must not raise
    assert calls == []


def test_stdin_is_interactive_survives_a_closed_stdin(monkeypatch):
    class Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(keyreader.sys, "stdin", Closed())
    assert keyreader.stdin_is_interactive() is False


def test_stdin_is_interactive_survives_a_stdin_without_isatty(monkeypatch):
    monkeypatch.setattr(keyreader.sys, "stdin", object())
    assert keyreader.stdin_is_interactive() is False


# --- the help overlay is generated from the bindings -------------------
def test_help_lists_every_documented_binding():
    """The overlay is built from KEY_HELP so it can't drift from the
    bindings; this pins the pairing so a new binding without a help line
    (or vice versa) is visible."""
    described = " ".join(keys for keys, _ in keymap.KEY_HELP)
    for fragment in ("tab", "1-9", "enter", "esc", "^k", "?"):
        assert fragment in described
    assert all(desc.strip() for _, desc in keymap.KEY_HELP)


# --- lane actions: x (drop) / R (retry) --------------------------------
def test_lane_action_keys_parse():
    assert parse_key("x") == KeyAction("drop_task")
    assert parse_key("R") == KeyAction("retry_task")


def test_lowercase_r_is_not_retry():
    """§7 gives `R` to retry and lowercase `r` to retarget-a-lease, which
    isn't built. Binding `r` to retry would make a typo do something the
    user didn't ask for."""
    assert parse_key("r") == KeyAction("none")


def test_lane_actions_leave_the_view_state_alone():
    """They address the Scheduler, not the view; the resulting status
    change arrives through the normal graph read on the next frame."""
    state = ViewState(selected=2, focused=True)
    for key in ("x", "R"):
        assert handle_key(state, key, 5) == state


def test_lane_actions_are_declared_for_the_dispatcher():
    """ui/live.py routes exactly these to the Scheduler; if a new lane
    action is added to the map without listing it here it would silently
    do nothing."""
    assert keymap.LANE_ACTIONS == {"drop_task", "retry_task"}
    for action in keymap.LANE_ACTIONS:
        assert action in keymap.Action.__args__


def test_help_covers_the_lane_actions_too():
    described = " ".join(keys for keys, _ in keymap.KEY_HELP)
    assert "x" in described and "R" in described


# --- s (steer): the one binding that opens a text field ----------------
def test_s_opens_the_steer_field():
    assert parse_key("s") == KeyAction("steer_task")
    state = apply_action(ViewState(selected=2), KeyAction("steer_task"), 5)
    assert state.steering and state.steer_text == ""
    assert state.selected == 2  # opening the field must not move the cursor


def test_s_does_nothing_before_the_graph_exists():
    """A field opened over zero lanes has nowhere to send to; enter would
    then be a keystroke that silently does nothing."""
    assert apply_action(ViewState(), KeyAction("steer_task"), 0).steering is False


def test_s_closes_the_help_overlay_it_would_otherwise_hide_behind():
    """The overlay replaces the lane list, so the field would open under a
    screen covering the lane it addresses."""
    state = apply_action(ViewState(show_help=True), KeyAction("steer_task"), 3)
    assert state.steering and state.show_help is False


def test_typing_accumulates_and_sends_on_enter():
    state = ViewState(steering=True)
    for char in "fix the tests":
        state, sent = steer_key(state, char)
        assert sent is None
    assert state.steer_text == "fix the tests"
    state, sent = steer_key(state, "\r")
    assert sent == "fix the tests"
    assert state.steering is False and state.steer_text == ""


def test_command_keys_are_plain_characters_while_typing():
    """`x` drops a task at top level. Inside a sentence it is a letter —
    if this regressed, typing "fix" would cancel the lane you were
    steering."""
    state = ViewState(steering=True, selected=1)
    for char in "xR?m":
        state = handle_key(state, char, 4)
    assert state.steer_text == "xR?m"
    assert state.selected == 1 and state.show_help is False and state.merged is False


def test_esc_cancels_the_note_instead_of_stopping_the_run():
    """At top level esc stops every agent. Backing out of a half-typed
    note must never reach that."""
    state, sent = steer_key(ViewState(steering=True, steer_text="never mind"), "\x1b")
    assert sent is None
    assert state.steering is False and state.steer_text == ""
    assert state.stop_requested is False


def test_backspace_deletes_the_last_character():
    for key in keymap.KEY_BACKSPACE:
        state, sent = steer_key(ViewState(steering=True, steer_text="abc"), key)
        assert state.steer_text == "ab" and sent is None


def test_backspace_on_an_empty_field_is_harmless():
    state, _ = steer_key(ViewState(steering=True), "\x7f")
    assert state.steer_text == ""


def test_enter_on_an_empty_field_sends_nothing_and_closes():
    state, sent = steer_key(ViewState(steering=True, steer_text="   "), "\r")
    assert sent is None and state.steering is False


def test_escape_sequences_and_control_characters_are_not_typed():
    """An arrow key arrives as a multi-byte sequence; typed literally it
    would put mojibake in the note and in the row."""
    for key in ("\x1b[Z", "\x1b[A", "\x0b", "\t"):
        state, sent = steer_key(ViewState(steering=True, steer_text="ok"), key)
        assert state.steer_text == "ok" and sent is None


def test_the_note_is_capped_rather_than_scrolling_the_field():
    state = ViewState(steering=True, steer_text="x" * keymap.STEER_MAX_CHARS)
    state, _ = steer_key(state, "y")
    assert state.steer_text == "x" * keymap.STEER_MAX_CHARS


def test_steer_is_not_a_lane_action():
    """LANE_ACTIONS are dispatched the instant the key is hit. Steer has
    nothing to send until enter, so listing it there would fire an empty
    note on every `s`."""
    assert "steer_task" not in keymap.LANE_ACTIONS
    assert "steer_task" in keymap.Action.__args__


def test_help_covers_steer():
    described = " ".join(keys for keys, _ in keymap.KEY_HELP)
    assert "s" in described.split()
