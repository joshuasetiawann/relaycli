"""Stage 3 tests: the real coding tools + path safety + permission behaviour."""

from __future__ import annotations

import pytest

from relaycli.config import PermissionMode
from relaycli.tools import default_registry
from relaycli.tools.create_folder import CreateFolderArgs, create_folder
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


# --- create_folder -----------------------------------------------------
def test_create_folder_applied_in_full_auto(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = create_folder(CreateFolderArgs(path="toko laptop"), ctx)
    assert res.ok
    assert (sample_project / "toko laptop").is_dir()
    assert "toko laptop" in res.output


def test_create_folder_accepts_folder_name_alias(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = create_folder(CreateFolderArgs(folder_name="toko laptop"), ctx)
    assert res.ok
    assert (sample_project / "toko laptop").is_dir()


def test_create_folder_blocks_escape(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = create_folder(CreateFolderArgs(path="../evil"), ctx)
    assert not res.ok
    assert not (sample_project.parent / "evil").exists()


def test_create_folder_declined_in_suggest(sample_project):
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: False)
    res = create_folder(CreateFolderArgs(path="toko laptop"), ctx)
    assert not res.ok
    assert not (sample_project / "toko laptop").exists()


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
    assert "--- current app.py ---" in res.output
    assert "def hello" in res.output


def test_edit_file_requires_prior_read_when_context_requests_it(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    ctx.require_read_before_edit = True
    res = edit_file(
        EditFileArgs(path="app.py", old_string="return 'hi'", new_string="return 'hello'"),
        ctx,
    )
    assert not res.ok
    assert "Read-before-edit is required" in res.output
    assert "return 'hi'" in res.output
    assert "app.py" in ctx.read_files
    assert "return 'hi'" in (sample_project / "app.py").read_text()

    res2 = edit_file(
        EditFileArgs(path="app.py", old_string="return 'hi'", new_string="return 'hello'"),
        ctx,
    )

    assert res2.ok
    assert "return 'hello'" in (sample_project / "app.py").read_text()


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
    assert set(reg.names()) == {"list_dir", "find_files", "read_file", "search",
                                "create_folder", "write_file", "edit_file", "run_command",
                                "run_background", "check_process", "stop_process",
                                "remember"}
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


# --- list_dir -----------------------------------------------------------
def test_list_dir_root_listing(sample_project):
    from relaycli.tools.list_dir import ListDirArgs, list_dir

    ctx = make_context(sample_project)
    res = list_dir(ListDirArgs(), ctx)
    assert res.ok
    assert "build/" in res.output          # dirs marked with a trailing slash
    assert "app.py" in res.output
    assert "README.md" in res.output


def test_list_dir_subdir_and_missing(sample_project):
    from relaycli.tools.list_dir import ListDirArgs, list_dir

    ctx = make_context(sample_project)
    res = list_dir(ListDirArgs(path="build"), ctx)
    assert res.ok and "out.txt" in res.output
    res2 = list_dir(ListDirArgs(path="nope"), ctx)
    assert not res2.ok


def test_list_dir_blocks_escape(sample_project):
    from relaycli.tools.list_dir import ListDirArgs, list_dir

    ctx = make_context(sample_project)
    res = list_dir(ListDirArgs(path=".."), ctx)
    assert not res.ok
    assert "outside the project root" in res.output


def test_list_dir_caps_entries(sample_project):
    from relaycli.tools.list_dir import _MAX_ENTRIES, ListDirArgs, list_dir

    many = sample_project / "many"
    many.mkdir()
    for i in range(_MAX_ENTRIES + 5):
        (many / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
    ctx = make_context(sample_project)
    res = list_dir(ListDirArgs(path="many"), ctx)
    assert res.ok
    assert "more entries" in res.output


def test_read_file_directory_hints_list_dir(sample_project):
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path="build"), ctx)
    assert not res.ok
    assert "list_dir" in res.output


# --- find_files ---------------------------------------------------------
def test_find_files_glob(sample_project):
    from relaycli.tools.find_files import FindFilesArgs, find_files

    ctx = make_context(sample_project)
    res = find_files(FindFilesArgs(pattern="**/*.py"), ctx)
    assert res.ok
    assert "app.py" in res.output


def test_find_files_skips_heavy_dirs(sample_project):
    from relaycli.tools.find_files import FindFilesArgs, find_files

    nm = sample_project / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x", encoding="utf-8")
    (sample_project / "main.js").write_text("x", encoding="utf-8")
    ctx = make_context(sample_project)
    res = find_files(FindFilesArgs(pattern="**/*.js"), ctx)
    assert res.ok
    assert "main.js" in res.output
    assert "node_modules" not in res.output


def test_find_files_no_match_and_cap(sample_project):
    from relaycli.tools.find_files import _MAX_RESULTS, FindFilesArgs, find_files

    ctx = make_context(sample_project)
    res = find_files(FindFilesArgs(pattern="**/*.zig"), ctx)
    assert res.ok and "No files match" in res.output
    for i in range(_MAX_RESULTS + 3):
        (sample_project / f"g{i:04d}.go").write_text("x", encoding="utf-8")
    res2 = find_files(FindFilesArgs(pattern="*.go"), ctx)
    assert "more matches" in res2.output


def test_navigation_tools_registered_for_all_roles():
    from relaycli.tools import planner_registry, reviewer_registry

    for reg in (default_registry(), planner_registry(), reviewer_registry()):
        names = {t.name for t in reg.tools()}
        assert {"list_dir", "find_files"} <= names


# --- background processes -------------------------------------------------
def _bg_ctx(root, mode=PermissionMode.full_auto):
    return make_context(root, mode)


def test_run_background_starts_and_logs(sample_project, tmp_path):
    import time

    from relaycli.tools.background import (
        BgArgs, CheckArgs, StopArgs, check_process, run_background, stop_process,
    )

    ctx = _bg_ctx(sample_project)
    res = run_background(BgArgs(command="echo hello-bg; sleep 30"), ctx)
    assert res.ok
    bg_id = res.meta["id"]
    assert bg_id.startswith("bg")

    # give the shell a moment to write the first line
    for _ in range(50):
        chk = check_process(CheckArgs(id=bg_id), ctx)
        if "hello-bg" in chk.output:
            break
        time.sleep(0.1)
    assert "hello-bg" in chk.output
    assert "running" in chk.output

    stop = stop_process(StopArgs(id=bg_id), ctx)
    assert stop.ok
    chk2 = check_process(CheckArgs(id=bg_id), ctx)
    assert "running" not in chk2.output


def test_run_background_reports_exit(sample_project):
    import time

    from relaycli.tools.background import BgArgs, CheckArgs, check_process, run_background

    ctx = _bg_ctx(sample_project)
    res = run_background(BgArgs(command="exit 7"), ctx)
    assert res.ok
    bg_id = res.meta["id"]
    for _ in range(50):
        chk = check_process(CheckArgs(id=bg_id), ctx)
        if "exited" in chk.output:
            break
        time.sleep(0.1)
    assert "exited" in chk.output and "7" in chk.output


def test_run_background_gated_in_suggest(sample_project):
    from relaycli.tools.background import BgArgs, run_background

    ctx = _bg_ctx(sample_project, PermissionMode.suggest)  # no prompter -> declined
    res = run_background(BgArgs(command="sleep 5"), ctx)
    assert not res.ok
    assert "not approved" in res.output


def test_check_and_stop_unknown_id(sample_project):
    from relaycli.tools.background import CheckArgs, StopArgs, check_process, stop_process

    ctx = _bg_ctx(sample_project)
    assert not check_process(CheckArgs(id="bg999"), ctx).ok
    assert not stop_process(StopArgs(id="bg999"), ctx).ok


def test_background_tools_registered():
    from relaycli.tools import reviewer_registry

    names = set(default_registry().names())
    assert {"run_background", "check_process", "stop_process"} <= names
    reviewer = set(reviewer_registry().names())
    assert "check_process" in reviewer
    assert "run_background" not in reviewer and "stop_process" not in reviewer
