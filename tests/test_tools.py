"""Stage 3 tests: the real coding tools + path safety + permission behaviour."""

from __future__ import annotations

import asyncio

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
    assert (sample_project / "new.txt").read_text(encoding="utf-8") == "hello\n"


def test_write_file_approved_in_suggest(sample_project):
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: True)
    res = write_file(WriteFileArgs(path="sub/dir/new.txt", content="x\n"), ctx)
    assert res.ok
    assert (sample_project / "sub" / "dir" / "new.txt").read_text(encoding="utf-8") == "x\n"


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
    assert "return 'hello'" in (sample_project / "app.py").read_text(encoding="utf-8")


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
    assert "return 'hi'" in (sample_project / "app.py").read_text(encoding="utf-8")

    res2 = edit_file(
        EditFileArgs(path="app.py", old_string="return 'hi'", new_string="return 'hello'"),
        ctx,
    )

    assert res2.ok
    assert "return 'hello'" in (sample_project / "app.py").read_text(encoding="utf-8")


def test_edit_file_ambiguous(sample_project):
    (sample_project / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(EditFileArgs(path="dup.py", old_string="x = 1", new_string="x = 2"), ctx)
    assert not res.ok
    assert "occurs 2 times" in res.output


def test_edit_file_replace_all(sample_project):
    (sample_project / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(
        EditFileArgs(path="dup.py", old_string="x = 1", new_string="x = 2", replace_all=True), ctx
    )
    assert res.ok
    assert (sample_project / "dup.py").read_text(encoding="utf-8") == "x = 2\nx = 2\n"


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
    # `pwd` answers in its shell's own dialect — `/c/Users/…` under Git
    # Bash, `/home/…` on Linux — so asserting the native path string only
    # ever passed on POSIX. The claim under test is "it ran in the project
    # root", and the trailing components prove that in either dialect.
    tail = "/".join(sample_project.resolve().parts[-3:])
    assert tail in res.output.replace("\\", "/")


def test_run_command_scrubs_any_api_key_suffix(monkeypatch, sample_project):
    """Regression: a security review flagged that swapping the live
    run_command implementation (from the now-deleted shell.py, whose scrub
    was a broad 'key'/'secret'/'token'/... substring match) to this module's
    explicit-list-only scrub narrowed what got stripped from a spawned
    command's environment. This is the fix — a name-pattern check that
    covers any current or future *_API_KEY var, not just the explicitly
    named ones — verified end-to-end through a real spawned command rather
    than just unit-testing _scrubbed_env in isolation. Echoes the one
    variable directly (rather than dumping the whole `env`) so the
    per-stream output cap can't produce a false negative by truncation.
    """
    monkeypatch.setenv("SOME_NEW_PROVIDER_API_KEY", "leaked-if-this-fails")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(
        RunCommandArgs(command="echo VALUE=${SOME_NEW_PROVIDER_API_KEY:-UNSET}"), ctx
    )
    assert res.ok
    assert "VALUE=UNSET" in res.output
    assert "leaked-if-this-fails" not in res.output


def test_run_command_scrub_preserves_aws_and_github_credentials(monkeypatch, sample_project):
    """The *_API_KEY pattern must not catch these — a task legitimately
    using aws-cli or gh-cli needs them, and run_command.py's own
    _SENSITIVE_ENV docstring says so explicitly."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-should-survive")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-should-survive")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(
        RunCommandArgs(command="echo AWS=$AWS_SECRET_ACCESS_KEY GH=$GITHUB_TOKEN"), ctx
    )
    assert res.ok
    assert "AWS=aws-should-survive" in res.output
    assert "GH=gh-should-survive" in res.output


# --- registry wiring ---------------------------------------------------
def test_default_registry_has_all_tools():
    reg = default_registry()
    expected = {"list_dir", "find_files", "read_file", "search",
                "create_folder", "write_file", "edit_file", "run_command",
                "run_background", "check_process", "stop_process",
                "remember", "webfetch", "websearch", "question",
                "todo_add", "todo_update", "todo_list", "git",
                "apply_patch", "think", "use_skill"}
    assert set(reg.names()) == expected
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
    names = set(default_registry().names())
    assert {"run_background", "check_process", "stop_process"} <= names


def test_reviewer_gets_exec_tier_including_background_tools():
    """reviewer_registry() is derived from the "read"+"exec" capability
    tier (relaycli.tools.capabilities) rather than a hand-picked list —
    run_background/stop_process are exec-tier tools like run_command, so
    reviewer gets them too now. This doesn't widen what reviewer can do
    unsupervised: each of these still requires ctx.permissions.confirm()
    at the point of use in any mode but full-auto, same as run_command —
    the capability tier controls whether the tool is *offered*, not
    whether using it needs a human's sign-off."""
    from relaycli.tools import reviewer_registry
    from relaycli.tools.capabilities import TOOL_CAPABILITIES

    reviewer = set(reviewer_registry().names())
    assert "check_process" in reviewer
    assert "run_background" in reviewer and "stop_process" in reviewer
    assert not any(TOOL_CAPABILITIES.get(n) == "write" for n in reviewer)


# --- background tools THROUGH the registry, not by direct import -------
# Regression coverage: relaycli/tools/shell.py used to be what actually
# backed these three names in every registry, with no permission check on
# run_background/stop_process at all. Every prior test above calls
# relaycli.tools.background's functions directly, which never exercised
# the live dispatch path and so never caught it. These go through
# default_registry().run(...) specifically to close that blind spot.
def test_run_background_via_registry_requires_permission(sample_project):
    reg = default_registry()
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: False)
    res = reg.run("run_background", {"command": "sleep 30"}, ctx)
    assert not res.ok
    assert "not approved" in res.output


def test_stop_process_via_registry_requires_permission(sample_project):
    reg = default_registry()
    start_ctx = make_context(sample_project, PermissionMode.full_auto)
    started = reg.run("run_background", {"command": "sleep 30"}, start_ctx)
    assert started.ok
    bg_id = started.meta["id"]

    declining_ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: False)
    stop_res = reg.run("stop_process", {"id": bg_id}, declining_ctx)
    assert not stop_res.ok
    assert "not approved" in stop_res.output

    # Declined -> the process must still be running, not actually stopped.
    check = reg.run("check_process", {"id": bg_id}, start_ctx)
    assert "running" in check.output

    reg.run("stop_process", {"id": bg_id}, start_ctx)  # cleanup


def test_run_background_via_registry_approved_starts_and_is_checkable(sample_project):
    reg = default_registry()
    ctx = make_context(sample_project, PermissionMode.full_auto)
    started = reg.run("run_background", {"command": "echo via-registry; sleep 30"}, ctx)
    assert started.ok
    bg_id = started.meta["id"]
    check = reg.run("check_process", {"id": bg_id}, ctx)
    assert "running" in check.output
    reg.run("stop_process", {"id": bg_id}, ctx)  # cleanup


# --- rate limiting (Phase 5e) -------------------------------------------
def _make_dummy_tool():
    """A trivial Tool whose func just counts invocations, for testing the
    @rate_limited decorator in isolation from any real tool's own logic."""
    from pydantic import BaseModel

    from relaycli.tools.registry import Tool

    class _Args(BaseModel):
        pass

    calls = {"n": 0}

    def _func(args, ctx):
        calls["n"] += 1
        return calls["n"]

    return Tool(name="dummy", description="test", args_model=_Args, func=_func), calls


def test_tool_context_default_budget(sample_project):
    from relaycli.tools.base import DEFAULT_MAX_CALLS_PER_SESSION

    ctx = make_context(sample_project)
    assert ctx.max_calls_per_session == DEFAULT_MAX_CALLS_PER_SESSION
    assert ctx.calls_made == 0


def test_rate_limited_sync_blocks_after_cap(sample_project):
    from relaycli.tools.base import ToolCallLimitError

    tool, calls = _make_dummy_tool()
    ctx = make_context(sample_project)
    ctx.max_calls_per_session = 2

    assert tool.run({}, ctx) == 1
    assert tool.run({}, ctx) == 2
    with pytest.raises(ToolCallLimitError, match=r"tool-call limit \(2\)"):
        tool.run({}, ctx)
    assert calls["n"] == 2  # the blocked call never reached the wrapped func
    assert ctx.calls_made == 2


def test_rate_limited_sync_ctx_none_bypasses_budget(sample_project):
    tool, calls = _make_dummy_tool()
    for _ in range(5):
        tool.run({}, None)
    assert calls["n"] == 5


def test_rate_limited_none_cap_disables_budget(sample_project):
    tool, calls = _make_dummy_tool()
    ctx = make_context(sample_project)
    ctx.max_calls_per_session = None
    for _ in range(10):
        tool.run({}, ctx)
    assert calls["n"] == 10
    assert ctx.calls_made == 0


def test_rate_limited_async_blocks_after_cap(sample_project):
    from relaycli.tools.base import ToolCallLimitError

    tool, calls = _make_dummy_tool()
    ctx = make_context(sample_project)
    ctx.max_calls_per_session = 1

    async def _run():
        assert await tool.arun({}, ctx) == 1
        with pytest.raises(ToolCallLimitError, match=r"tool-call limit \(1\)"):
            await tool.arun({}, ctx)

    asyncio.run(_run())
    assert calls["n"] == 1


def test_registry_run_enforces_rate_limit_end_to_end(sample_project):
    """The decorator must be wired through ToolRegistry.run for real tools,
    not just exercised against the dummy Tool used in the tests above."""
    from relaycli.tools.base import ToolCallLimitError

    ctx = make_context(sample_project)
    ctx.max_calls_per_session = 1
    reg = default_registry()
    reg.run("list_dir", {}, ctx)
    with pytest.raises(ToolCallLimitError):
        reg.run("list_dir", {}, ctx)


# --- file cache integration (v2 Stage 1) --------------------------------
def test_read_file_hits_cache_on_unchanged_second_read(sample_project):
    ctx = make_context(sample_project)
    read_file(ReadFileArgs(path="app.py"), ctx)
    read_file(ReadFileArgs(path="app.py"), ctx)
    assert ctx.file_cache.hits == 1
    assert ctx.file_cache.misses == 1


def test_read_file_bypasses_cache_above_max_bytes(sample_project):
    """A file bigger than the requested cap must not be pulled fully into
    the cache — read_file's own memory-bounding must still apply."""
    big = sample_project / "big.txt"
    big.write_text("x" * 500, encoding="utf-8")
    ctx = make_context(sample_project)
    res = read_file(ReadFileArgs(path="big.txt", max_bytes=100), ctx)
    assert res.ok
    assert ctx.file_cache.hits == 0
    assert ctx.file_cache.misses == 0  # never went through the cache at all


def test_write_file_invalidates_cache_so_reread_sees_new_content(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    read_file(ReadFileArgs(path="app.py"), ctx)  # warm the cache
    write_file(WriteFileArgs(path="app.py", content="def hello():\n    return 'bye'\n"), ctx)
    res = read_file(ReadFileArgs(path="app.py"), ctx)
    assert "'bye'" in res.output
    assert ctx.file_cache.misses == 2  # warm read + forced re-read, no stale hit


def test_edit_file_invalidates_cache_so_reread_sees_new_content(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    read_file(ReadFileArgs(path="app.py"), ctx)  # warm the cache
    edit_file(EditFileArgs(path="app.py", old_string="'hi'", new_string="'bye'"), ctx)
    res = read_file(ReadFileArgs(path="app.py"), ctx)
    assert "'bye'" in res.output
    assert ctx.file_cache.misses == 2


def test_apply_patch_fallback_invalidates_cache(sample_project):
    from unittest.mock import patch as mock_patch

    from relaycli.tools.apply_patch import ApplyPatchArgs, apply_patch

    ctx = make_context(sample_project, PermissionMode.full_auto)
    read_file(ReadFileArgs(path="app.py"), ctx)  # warm the cache
    patch_text = (
        "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n"
        " def hello():\n-    return 'hi'\n+    return 'yo'\n"
    )
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        result = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    assert result.ok
    res = read_file(ReadFileArgs(path="app.py"), ctx)
    assert "'yo'" in res.output
    assert ctx.file_cache.misses == 2


def test_search_python_fallback_uses_the_shared_cache(sample_project):
    from relaycli.tools.search import _python_search

    ctx = make_context(sample_project)
    matches1 = _python_search(SearchArgs(query="hello"), sample_project, ctx.project, ctx.file_cache)
    matches2 = _python_search(SearchArgs(query="hello"), sample_project, ctx.project, ctx.file_cache)
    assert matches1 == matches2
    assert any("hello" in m for m in matches1)
    assert ctx.file_cache.hits >= 1  # app.py read once, reused on the second search
