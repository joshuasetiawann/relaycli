"""Tests for structured_diff() (relaycli/ui/render.py) and its wiring into
ToolResult.diff for write_file/edit_file/apply_patch (v2 Stage 1: "make
ToolResult carry typed data, not just a formatted string")."""

from __future__ import annotations

from unittest.mock import patch as mock_patch

from relaycli.config import PermissionMode
from relaycli.tools.apply_patch import ApplyPatchArgs, apply_patch
from relaycli.tools.edit_file import EditFileArgs, edit_file
from relaycli.tools.write_file import WriteFileArgs, write_file
from relaycli.ui.render import structured_diff

from tests.conftest import make_context


# --- structured_diff() itself -------------------------------------------
def test_structured_diff_reports_added_and_removed_matching_diff_stats():
    fd = structured_diff("a\nb\nc\n", "a\nX\nc\nd\n", "f.py")
    assert fd.path == "f.py"
    assert fd.added == 2  # X, d
    assert fd.removed == 1  # b


def test_structured_diff_no_changes_yields_no_hunks():
    fd = structured_diff("same\n", "same\n", "f.py")
    assert fd.hunks == []
    assert fd.added == 0
    assert fd.removed == 0


def test_structured_diff_marks_new_and_deleted_files():
    created = structured_diff("", "content\n", "new.py")
    assert created.is_new
    assert not created.is_deleted

    deleted = structured_diff("content\n", "", "gone.py")
    assert deleted.is_deleted
    assert not deleted.is_new


def test_structured_diff_splits_distant_changes_into_separate_hunks():
    old = "\n".join(f"line{i}" for i in range(1, 21)) + "\n"
    lines = old.splitlines()
    lines[1] = "CHANGED-near-top"
    lines[18] = "CHANGED-near-bottom"
    new = "\n".join(lines) + "\n"

    fd = structured_diff(old, new, "f.py")
    assert len(fd.hunks) == 2
    assert fd.added == 2
    assert fd.removed == 2
    for hunk in fd.hunks:
        assert hunk.text.startswith("@@ ")
        assert hunk.added + hunk.removed > 0


def test_structured_diff_hunk_header_fields_match_the_text():
    fd = structured_diff("a\nb\nc\n", "a\nZ\nc\n", "f.py")
    assert len(fd.hunks) == 1
    hunk = fd.hunks[0]
    assert hunk.text.startswith(f"@@ -{hunk.old_start},{hunk.old_lines} "
                                 f"+{hunk.new_start},{hunk.new_lines} @@")


# --- ToolResult.diff wiring -----------------------------------------------
def test_write_file_populates_diff_as_new_file(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = write_file(WriteFileArgs(path="new.py", content="x = 1\n"), ctx)
    assert res.ok
    assert res.diff == [structured_diff("", "x = 1\n", "new.py")]
    assert res.diff[0].is_new


def test_write_file_no_change_returns_empty_diff_list(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    original = (sample_project / "app.py").read_text(encoding="utf-8")
    res = write_file(WriteFileArgs(path="app.py", content=original), ctx)
    assert res.ok
    assert res.diff == []


def test_edit_file_populates_diff(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(
        EditFileArgs(path="app.py", old_string="'hi'", new_string="'yo'"), ctx
    )
    assert res.ok
    assert len(res.diff) == 1
    assert res.diff[0].path == "app.py"
    assert res.diff[0].added == 1
    assert res.diff[0].removed == 1


def test_apply_patch_fallback_populates_diff_per_file(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    patch_text = (
        "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n"
        " def hello():\n-    return 'hi'\n+    return 'sup'\n"
    )
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        result = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    assert result.ok
    assert len(result.diff) == 1
    assert result.diff[0].path == "app.py"
    assert result.diff[0].added == 1
    assert result.diff[0].removed == 1


def test_apply_patch_primary_path_leaves_diff_unset(sample_project):
    """The patch-subprocess path doesn't retrofit structured diffs (would
    need re-reading file content around the subprocess call) — documented
    scope boundary, not an oversight. Confirms it degrades to None rather
    than crashing or fabricating data."""
    ctx = make_context(sample_project, PermissionMode.full_auto)
    patch_text = (
        "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n"
        " def hello():\n-    return 'hi'\n+    return 'patched'\n"
    )
    result = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    if result.ok:  # only meaningful if the real `patch` binary is present
        assert result.diff is None
