"""Live end-to-end tests: the real agent loop against a real provider.

Skipped unless ``RELAYCLI_E2E_MODEL`` is set, e.g.::

    RELAYCLI_E2E_MODEL=openrouter/nvidia/nemotron-3-super-120b-a12b:free \
    OPENROUTER_API_KEY=sk-or-... pytest tests/test_e2e_live.py -v

These tests hit the network, consume tokens, and depend on provider
availability (free-tier models are often rate-limited upstream), so they
never run in the default ``pytest`` invocation.
"""

from __future__ import annotations

import io
import os

import pytest
from rich.console import Console

from relaycli.agent import Agent
from relaycli.config import PermissionMode, Settings
from relaycli.context import ProjectContext
from relaycli.permissions import PermissionManager

E2E_MODEL = os.environ.get("RELAYCLI_E2E_MODEL", "")

pytestmark = pytest.mark.skipif(
    not E2E_MODEL,
    reason="live E2E disabled; set RELAYCLI_E2E_MODEL=<model> (plus its provider key) to run",
)


def _agent(root) -> Agent:
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    settings = Settings(
        model=E2E_MODEL, permission_mode=PermissionMode.full_auto, max_iterations=15
    )
    permissions = PermissionManager(
        PermissionMode.full_auto, console=console, assume_yes=True
    )
    return Agent(
        settings, console=console, project=ProjectContext(root), permissions=permissions
    )


def test_live_create_folder_and_file(tmp_path):
    result = _agent(tmp_path).run(
        "Create a folder named 'pkg' containing a file 'mod.py' with a function "
        "ping() that returns the string 'pong'. Do not run or test it."
    )
    assert result.stopped_reason == "done", result.final_text
    mod = tmp_path / "pkg" / "mod.py"
    assert mod.is_file(), "pkg/mod.py was not created on disk"
    assert "pong" in mod.read_text(encoding="utf-8")


def test_live_edit_existing_file(tmp_path):
    (tmp_path / "config.py").write_text("DEBUG = False\n", encoding="utf-8")
    result = _agent(tmp_path).run(
        "Edit config.py so that DEBUG is True instead of False. Change nothing else."
    )
    assert result.stopped_reason == "done", result.final_text
    assert "DEBUG = True" in (tmp_path / "config.py").read_text(encoding="utf-8")


def test_live_run_command_creates_and_deletes(tmp_path):
    (tmp_path / "obsolete.txt").write_text("delete me\n", encoding="utf-8")
    result = _agent(tmp_path).run(
        "Create an empty directory named 'build', then delete the file "
        "obsolete.txt. Then stop."
    )
    assert result.stopped_reason == "done", result.final_text
    assert (tmp_path / "build").is_dir(), "build/ was not created"
    assert not (tmp_path / "obsolete.txt").exists(), "obsolete.txt was not deleted"
