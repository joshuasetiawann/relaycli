"""Stage 3 tests: permission gating across suggest / auto-edit / full-auto."""

from __future__ import annotations

import pytest

from relaycli.config import PermissionMode
from relaycli.permissions import PermissionManager


def _recording_prompter():
    calls: list[str] = []

    def prompter(text: str) -> bool:
        calls.append(text)
        return True

    return prompter, calls


@pytest.mark.parametrize("action", ["edit", "write", "command"])
def test_suggest_prompts_for_everything(action):
    prompter, calls = _recording_prompter()
    pm = PermissionManager(PermissionMode.suggest, prompter=prompter)
    decision = pm.confirm(action, prompt_text=f"do {action}?")
    assert decision.approved is True
    assert decision.auto is False
    assert len(calls) == 1  # the human was asked


@pytest.mark.parametrize("action", ["edit", "write"])
def test_auto_edit_auto_approves_edits(action):
    prompter, calls = _recording_prompter()
    pm = PermissionManager(PermissionMode.auto_edit, prompter=prompter)
    decision = pm.confirm(action, prompt_text="edit?")
    assert decision.approved is True
    assert decision.auto is True
    assert calls == []  # no prompt for edits in auto-edit


def test_auto_edit_still_prompts_for_commands():
    prompter, calls = _recording_prompter()
    pm = PermissionManager(PermissionMode.auto_edit, prompter=prompter)
    decision = pm.confirm("command", prompt_text="run?")
    assert decision.approved is True
    assert decision.auto is False
    assert len(calls) == 1  # commands still need approval


@pytest.mark.parametrize("action", ["edit", "write", "command"])
def test_full_auto_approves_without_prompt(action):
    prompter, calls = _recording_prompter()
    pm = PermissionManager(PermissionMode.full_auto, prompter=prompter)
    decision = pm.confirm(action, prompt_text="go?")
    assert decision.approved is True
    assert decision.auto is True
    assert calls == []


def test_denied_when_prompter_says_no():
    pm = PermissionManager(PermissionMode.suggest, prompter=lambda _t: False)
    decision = pm.confirm("command", prompt_text="run?")
    assert decision.approved is False


def test_set_mode_changes_behaviour():
    pm = PermissionManager(PermissionMode.suggest, prompter=lambda _t: False)
    assert pm.confirm("edit", prompt_text="?").approved is False
    pm.set_mode(PermissionMode.full_auto)
    assert pm.confirm("edit", prompt_text="?").approved is True


def test_mode_accepts_string():
    pm = PermissionManager("full-auto")
    assert pm.mode is PermissionMode.full_auto
