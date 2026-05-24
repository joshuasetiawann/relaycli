"""Stage 3 tests: the real coding tools + path safety + permission behaviour."""

from __future__ import annotations

import pytest

from relaycli.config import PermissionMode
from relaycli.tools import default_registry
from relaycli.tools.edit_file import EditFileArgs, edit_file
from relaycli.tools.read_file import ReadFileArgs, read_file
from relaycli.tools.run_command import RunCommandArgs, run_command
from relaycli.tools.search import SearchArgs, search
from relaycli.tools.write_file import WriteFileArgs, write_file

from tests.conftest import console_text, make_context


# --- read_file ---------------------------------------------------------
def test_read_file_ok(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path="app.py"), ctx)
    assert res.ok
    assert "def hello" in res.output


def test_read_file_blocks_path_traversal(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path="../outside.txt"), ctx)
    assert not res.ok
    assert "outside the project root" in res.output


def test_read_file_blocks_absolute_escape(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path="/etc/passwd"), ctx)
    assert not res.ok
    assert "outside the project root" in res.output


def test_read_file_refuses_secret(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path=".env"), ctx)
    assert not res.ok
    assert "secret" in res.output.lower()
    assert "topsecret" not in res.output  # contents never leak


def test_read_file_secret_with_human_approval(sample_project):
    # A secret read is allowed only when the HUMAN approves (never via a
    # model-supplied flag — the old `force` argument is gone).
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: True)
    res = read_file(ReadFileArgs(path=".env"), ctx)
    assert res.ok
    assert "API_SECRET" in res.output


def test_read_file_example_is_not_secret(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path=".env.example"), ctx)
    assert res.ok  # templates are safe


def test_read_file_refuses_gitignored(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path="ignored.txt"), ctx)
    assert not res.ok
    assert "ignore" in res.output.lower()


def test_read_file_refuses_binary(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path="binary.dat"), ctx)
    assert not res.ok
    assert "binary" in res.output.lower()


# --- search ------------------------------------------------------------
def test_search_finds_todos(sample_project):
    ctx = make_context(sample_project)
    res = search(SearchArgs(query="TODO"), ctx)
    assert res.ok
    assert "app.py" in res.output
    assert "README.md" in res.output


def test_search_excludes_secret_contents(sample_project):
    ctx = make_context(sample_project)
    res = search(SearchArgs(query="topsecret"), ctx)
    # The .env content must never appear in search results.
    assert "topsecret" not in res.output


# --- write_file --------------------------------------------------------
def test_write_file_declined_in_suggest(sample_project):
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: False)
    res = write_file(WriteFileArgs(path="new.txt", content="hello\n"), ctx)
    assert not res.ok
    assert not (sample_project / "new.txt").exists()
    # the diff was still shown before the prompt
    assert "hello" in console_text(ctx)


def test_write_file_applied_in_full_auto(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = write_file(WriteFileArgs(path="new.txt", content="hello\n"), ctx)
    assert res.ok
    assert (sample_project / "new.txt").read_text() == "hello\n"


def test_write_file_approved_in_suggest(sample_project):
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: True)
    res = write_file(WriteFileArgs(path="sub/dir/new.txt", content="x\n"), ctx)
    assert res.ok
    assert (sample_project / "sub" / "dir" / "new.txt").read_text() == "x\n"


def test_write_file_blocks_escape(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = write_file(WriteFileArgs(path="../evil.txt", content="x"), ctx)
    assert not res.ok
    assert not (sample_project.parent / "evil.txt").exists()


# --- edit_file ---------------------------------------------------------
def test_edit_file_applies(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(
        EditFileArgs(path="app.py", old_string="return 'hi'", new_string="return 'hello'"),
        ctx,
    )
    assert res.ok
    assert "return 'hello'" in (sample_project / "app.py").read_text()


def test_edit_file_not_found(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(
        EditFileArgs(path="app.py", old_string="nonexistent snippet", new_string="x"), ctx
    )
    assert not res.ok
    assert "not found" in res.output.lower()


def test_edit_file_ambiguous(sample_project):
    (sample_project / "dup.py").write_text("x = 1\nx = 1\n")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(EditFileArgs(path="dup.py", old_string="x = 1", new_string="x = 2"), ctx)
    assert not res.ok
    assert "occurs 2 times" in res.output


def test_edit_file_replace_all(sample_project):
    (sample_project / "dup.py").write_text("x = 1\nx = 1\n")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(
        EditFileArgs(path="dup.py", old_string="x = 1", new_string="x = 2", replace_all=True), ctx
    )
    assert res.ok
    assert (sample_project / "dup.py").read_text() == "x = 2\nx = 2\n"


# --- run_command -------------------------------------------------------
def test_run_command_captures_output(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(RunCommandArgs(command="echo hello-relay"), ctx)
    assert res.ok
    assert "hello-relay" in res.output
    assert "exit code: 0" in res.output


def test_run_command_blocked_without_approval_in_suggest(sample_project):
    marker = sample_project / "ran.txt"
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: False)
    res = run_command(RunCommandArgs(command=f"touch {marker.name}"), ctx)
    assert not res.ok
    assert not marker.exists()  # the command never ran


def test_run_command_nonzero_exit(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(RunCommandArgs(command="exit 3"), ctx)
    assert not res.ok
    assert "exit code: 3" in res.output


def test_run_command_runs_in_project_root(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(RunCommandArgs(command="pwd"), ctx)
    assert res.ok
    assert str(sample_project.resolve()) in res.output


# --- registry wiring ---------------------------------------------------
def test_default_registry_has_all_tools():
    reg = default_registry()
    assert set(reg.names()) == {"read_file", "search", "write_file", "edit_file", "run_command"}
    # the throwaway get_time tool is gone
    assert "get_time" not in reg.names()


def test_registry_dispatch(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    reg = default_registry()
    res = reg.run("read_file", {"path": "app.py"}, ctx)
    assert res.ok and "def hello" in res.output


def test_null_optional_args_use_defaults(sample_project):
    # Small models often emit explicit null for optional params; defaults must apply.
    ctx = make_context(sample_project, PermissionMode.full_auto)
    reg = default_registry()
    res = reg.run("search", {"query": "TODO", "path": None, "max_results": None}, ctx)
    assert res.ok
    assert "app.py" in res.output
