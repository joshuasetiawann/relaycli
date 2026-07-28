"""Tests for relaycli's desktop web console accessibility pass (WCAG 2.1
AA). Structural/content assertions against the served files — there's no
real DOM/browser available in this environment, so these check that the
right markup and JS wiring exist, not runtime behavior (screen reader
output, actual keyboard focus order) a human or a real AT would still
need to verify."""

from __future__ import annotations

import re

from relaycli.ui.web import APP_JS_PATH, UI_PATH


def _html() -> str:
    return UI_PATH.read_text()


def _js() -> str:
    return APP_JS_PATH.read_text()


def test_html_declares_a_language():
    assert re.search(r'<html\s+lang="en"', _html())


# --- custom toggle switches (the most serious pre-existing gap: plain
# divs with onclick, unreachable and unlabeled for keyboard/AT users) ---
_TOGGLE_IDS = ("themeTog", "reduceTog")
_TOGGLE_FLAGS = ("relay", "tasks", "explorer", "tester")


def test_every_toggle_switch_is_a_focusable_switch():
    html = _html()
    for tid in _TOGGLE_IDS:
        tag = re.search(rf'<div class="tog" id="{tid}"[^>]*>', html)
        assert tag, f"{tid} not found"
        assert 'role="switch"' in tag.group(0)
        assert 'tabindex="0"' in tag.group(0)
        assert "aria-checked" in tag.group(0)
    for flag in _TOGGLE_FLAGS:
        tag = re.search(rf'<div class="tog" data-flag="{flag}"[^>]*>', html)
        assert tag, f"data-flag={flag} not found"
        assert 'role="switch"' in tag.group(0)
        assert 'tabindex="0"' in tag.group(0)
        assert "aria-checked" in tag.group(0)


def test_toggle_switches_sync_aria_checked_in_js():
    js = _js()
    assert 'setAttribute("aria-checked"' in js
    # At minimum one call site per distinct toggle group (data-flag loop,
    # theme, reduce-motion) — three call sites, not just one leftover.
    assert js.count('setAttribute("aria-checked"') >= 3


def test_toggle_switches_are_keyboard_activatable():
    js = _js()
    assert "function bridgeKeyboard(" in js
    assert js.count("bridgeKeyboard(") >= 5  # 4 config flags + theme + reduce (+ swatches)


def test_accent_swatches_are_focusable_buttons_with_labels():
    js = _js()
    assert 'role="button"' in js
    assert 'tabindex="0"' in js
    assert "aria-pressed" in js
    assert "ACCENT_NAMES" in js


# --- form inputs: every input/select needs an accessible name ----------
_LABELED_INPUT_IDS = (
    "projectPath", "modelQ", "customModel", "ollamaModel", "input",
)


def test_every_bare_input_has_an_accessible_name():
    html = _html()
    for iid in _LABELED_INPUT_IDS:
        tag = re.search(rf'<input id="{iid}"[^>]*>', html)
        assert tag, f"input#{iid} not found"
        assert "aria-label=" in tag.group(0), f"input#{iid} has no aria-label"


# --- icon-only buttons: title alone is not a robust accessible name ----
_ICON_BUTTON_IDS = ("send", "zOut", "zIn", "termSmaller", "termLarger", "termToggle")


def test_icon_only_buttons_have_aria_label():
    html = _html()
    for bid in _ICON_BUTTON_IDS:
        tag = re.search(rf'<button[^>]*\bid="{bid}"[^>]*>', html)
        assert tag, f"button#{bid} not found"
        assert "aria-label=" in tag.group(0), f"button#{bid} has no aria-label"
