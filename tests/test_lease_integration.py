"""Tests that write_file/edit_file/create_folder/apply_patch actually
enforce path leases (Part A §3.3: "Write tools... check the lease and
fail loudly without it") — the tool-layer half of Stage 3's safety
mechanism; the lease bookkeeping itself is tested in
test_agent_leases.py, the scheduler's use of it in
test_agent_scheduler.py."""

from __future__ import annotations

from unittest.mock import patch as mock_patch

from relaycli.agent.leases import LeaseManager
from relaycli.config import PermissionMode
from relaycli.tools.apply_patch import ApplyPatchArgs, apply_patch
from relaycli.tools.create_folder import CreateFolderArgs, create_folder
from relaycli.tools.edit_file import EditFileArgs, edit_file
from relaycli.tools.write_file import WriteFileArgs, write_file

from tests.conftest import make_context


# --- no lease_manager at all: every pre-Stage-3 caller is unaffected ------
def test_write_file_unaffected_when_no_lease_manager(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    assert ctx.lease_manager is None
    res = write_file(WriteFileArgs(path="new.py", content="x = 1\n"), ctx)
    assert res.ok


def test_edit_file_unaffected_when_no_lease_manager(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(EditFileArgs(path="app.py", old_string="'hi'", new_string="'lo'"), ctx)
    assert res.ok


def test_create_folder_unaffected_when_no_lease_manager(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = create_folder(CreateFolderArgs(path="newdir"), ctx)
    assert res.ok


def test_apply_patch_unaffected_when_no_lease_manager(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    patch_text = "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n def hello():\n-    return 'hi'\n+    return 'lo'\n"
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        res = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    assert res.ok


# --- lease_manager present, no lease held: refused with a clear message ---
def test_write_file_refused_without_a_held_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    res = write_file(WriteFileArgs(path="new.py", content="x = 1\n"), ctx)
    assert not res.ok
    assert "lease" in res.output.lower()
    assert not (sample_project / "new.py").exists()


def test_edit_file_refused_without_a_held_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    original = (sample_project / "app.py").read_text()
    res = edit_file(EditFileArgs(path="app.py", old_string="'hi'", new_string="'lo'"), ctx)
    assert not res.ok
    assert (sample_project / "app.py").read_text() == original


def test_create_folder_refused_without_a_held_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    res = create_folder(CreateFolderArgs(path="newdir"), ctx)
    assert not res.ok
    assert not (sample_project / "newdir").exists()


def test_apply_patch_refused_without_a_held_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    original = (sample_project / "app.py").read_text()
    patch_text = "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n def hello():\n-    return 'hi'\n+    return 'lo'\n"
    res = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    assert not res.ok
    assert (sample_project / "app.py").read_text() == original


# --- lease held by the current task: allowed -------------------------------
def test_write_file_allowed_when_task_holds_the_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    ctx.lease_manager.acquire("t1", ("new.py",))
    res = write_file(WriteFileArgs(path="new.py", content="x = 1\n"), ctx)
    assert res.ok


def test_edit_file_allowed_when_task_holds_the_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    ctx.lease_manager.acquire("t1", ("app.py",))
    res = edit_file(EditFileArgs(path="app.py", old_string="'hi'", new_string="'lo'"), ctx)
    assert res.ok


def test_apply_patch_allowed_when_task_holds_the_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    ctx.lease_manager.acquire("t1", ("app.py",))
    patch_text = "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n def hello():\n-    return 'hi'\n+    return 'lo'\n"
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        res = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    assert res.ok


# --- lease held by a DIFFERENT task: refused, not silently allowed --------
def test_write_file_refused_when_a_different_task_holds_the_lease(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.lease_manager.acquire("other-task", ("new.py",))
    ctx.current_task_id = "t1"
    res = write_file(WriteFileArgs(path="new.py", content="x = 1\n"), ctx)
    assert not res.ok
    assert "other-task" in res.output


def test_apply_patch_checks_every_touched_file_before_applying_any(sample_project):
    """One file in a multi-file patch lacks a lease -> the whole patch is
    refused, not partially applied."""
    (sample_project / "b.py").write_text("y = 1\n")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.lease_manager = LeaseManager()
    ctx.current_task_id = "t1"
    ctx.lease_manager.acquire("t1", ("app.py",))  # app.py only, not b.py
    patch_text = (
        "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n def hello():\n-    return 'hi'\n+    return 'lo'\n"
        "--- b.py\n+++ b.py\n@@ -1 +1 @@\n-y = 1\n+y = 2\n"
    )
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        res = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    assert not res.ok
    assert (sample_project / "app.py").read_text() == "def hello():\n    return 'hi'\n\n# TODO: write tests\n"
    assert (sample_project / "b.py").read_text() == "y = 1\n"
