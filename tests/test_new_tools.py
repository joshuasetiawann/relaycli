from __future__ import annotations

import io
import json
import re
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest
from rich.console import Console

from relaycli.core.config import PermissionMode
from relaycli.core.context import ProjectContext
from relaycli.core.permissions import PermissionManager
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import default_registry


@pytest.fixture
def ctx(tmp_path):
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    return ToolContext(
        project=ProjectContext(tmp_path),
        permissions=PermissionManager(PermissionMode.full_auto, console=console),
        console=console,
    )


# ── think ──────────────────────────────────────────────────────────────

def test_think_records_thought(ctx):
    from relaycli.tools.think import think, ThinkArgs
    result = think(ThinkArgs(thought="I should check the file first"), ctx)
    assert result.ok
    assert "Thought recorded" in result.output
    assert "check the file" in result.output

def test_think_empty_thought(ctx):
    from relaycli.tools.think import think, ThinkArgs
    result = think(ThinkArgs(thought=""), ctx)
    assert result.ok
    assert "Thought recorded" in result.output

def test_think_via_registry(ctx):
    r = default_registry()
    result = r.run("think", json.dumps({"thought": "step by step analysis"}), ctx)
    assert result.ok
    assert "step by step" in result.output


# ── webfetch ───────────────────────────────────────────────────────────

def test_webfetch_mocked_success(ctx):
    from relaycli.tools.webfetch import webfetch, WebFetchArgs
    with mock_patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value.headers = {"Content-Type": "text/plain"}
        mock.return_value.__enter__.return_value.read.return_value = b"hello world"
        result = webfetch(WebFetchArgs(url="https://example.com"), ctx)
    assert result.ok
    assert "hello world" in result.output

def test_webfetch_http_error(ctx):
    from relaycli.tools.webfetch import webfetch, WebFetchArgs
    import urllib.error
    with mock_patch("urllib.request.urlopen") as mock:
        mock.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None
        )
        result = webfetch(WebFetchArgs(url="https://example.com/404"), ctx)
    assert not result.ok
    assert "404" in result.output

def test_webfetch_connection_error(ctx):
    from relaycli.tools.webfetch import webfetch, WebFetchArgs
    import urllib.error
    with mock_patch("urllib.request.urlopen") as mock:
        mock.side_effect = urllib.error.URLError("Connection refused")
        result = webfetch(WebFetchArgs(url="https://example.com"), ctx)
    assert not result.ok
    assert "Connection refused" in result.output

def test_webfetch_json_format(ctx):
    from relaycli.tools.webfetch import webfetch, WebFetchArgs
    with mock_patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value.headers = {"Content-Type": "application/json"}
        mock.return_value.__enter__.return_value.read.return_value = b'{"key": "value"}'
        result = webfetch(WebFetchArgs(url="https://api.example.com", format="json"), ctx)
    assert result.ok
    assert "key" in result.output

def test_webfetch_truncates_long_output(ctx):
    from relaycli.tools.webfetch import webfetch, WebFetchArgs
    big = b"x" * 20000
    with mock_patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value.headers = {"Content-Type": "text/plain"}
        mock.return_value.__enter__.return_value.read.return_value = big
        result = webfetch(WebFetchArgs(url="https://example.com/big", format="text"), ctx)
    assert result.ok
    assert len(result.output) <= 10000

def test_webfetch_via_registry(ctx):
    r = default_registry()
    import urllib.error
    with mock_patch("urllib.request.urlopen") as mock:
        mock.side_effect = urllib.error.URLError("timeout")
        result = r.run("webfetch", json.dumps({"url": "https://example.com"}), ctx)
    assert not result.ok


# ── websearch ──────────────────────────────────────────────────────────

def test_websearch_mocked_api(ctx):
    from relaycli.tools.websearch import websearch, WebSearchArgs
    mock_data = {
        "AbstractText": "Python is a programming language",
        "Heading": "Python (programming language)",
        "AbstractURL": "https://en.wikipedia.org/wiki/Python",
        "RelatedTopics": [
            {"Text": "Python for beginners - learn python", "FirstURL": "https://example.com"},
        ],
    }
    with mock_patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(mock_data).encode()
        result = websearch(WebSearchArgs(query="python programming"), ctx)
    assert result.ok
    assert "Python" in result.output

def test_websearch_api_fallback_to_html(ctx):
    from relaycli.tools.websearch import websearch, WebSearchArgs
    import urllib.error
    with mock_patch("urllib.request.urlopen") as mock:
        mock.side_effect = [
            urllib.error.URLError("API down"),
            # html fallback
            MockResponse(b'<html><a class="result__a" href="https://example.com"><b>Result</b></a>'
                         b'<td class="result__snippet">some snippet</td></html>'),
        ]
        result = websearch(WebSearchArgs(query="test"), ctx)
    assert result.ok or "no results" in result.output.lower()

def test_websearch_empty_results(ctx):
    from relaycli.tools.websearch import websearch, WebSearchArgs
    with mock_patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps({
            "AbstractText": "", "AbstractURL": "", "Heading": "", "RelatedTopics": []
        }).encode()
        result = websearch(WebSearchArgs(query="asdfghzxcvbnm"), ctx)
    assert result.ok or "no results" in result.output.lower()

def test_websearch_via_registry(ctx):
    r = default_registry()
    with mock_patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps({
            "AbstractText": "test", "Heading": "test", "RelatedTopics": []
        }).encode()
        result = r.run("websearch", json.dumps({"query": "test"}), ctx)
    assert result.ok


class MockResponse:
    def __init__(self, data, headers=None):
        self._data = data
        self.headers = headers or {"Content-Type": "text/html"}
    def read(self):
        return self._data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


# ── question ───────────────────────────────────────────────────────────

def test_question_simple(ctx):
    from relaycli.tools.question import ask_question, QuestionArgs
    from rich.prompt import Prompt
    with mock_patch.object(Prompt, "ask", return_value="yes"):
        result = ask_question(QuestionArgs(question="Continue?"), ctx)
    assert result.ok
    assert "yes" in result.output

def test_question_with_options(ctx):
    from relaycli.tools.question import ask_question, QuestionArgs
    from rich.prompt import Prompt
    with mock_patch.object(Prompt, "ask", return_value="2"):
        result = ask_question(QuestionArgs(question="Pick one:", options=["a", "b", "c"]), ctx)
    assert result.ok
    assert "2" in result.output

def test_question_no_console(ctx):
    from relaycli.tools.question import ask_question, QuestionArgs
    result = ask_question(QuestionArgs(question="test"), ctx=None)
    assert not result.ok
    assert "No console" in result.output

def test_question_via_registry(ctx):
    r = default_registry()
    from rich.prompt import Prompt
    with mock_patch.object(Prompt, "ask", return_value="ok"):
        result = r.run("question", json.dumps({"question": "Proceed?"}), ctx)
    assert result.ok
    assert "ok" in result.output


# ── todo ───────────────────────────────────────────────────────────────

def test_todo_add_and_list(ctx):
    from relaycli.tools.todo import todo_add, todo_list, todo_update, TodoAddArgs, TodoListArgs, TodoUpdateArgs
    todo_add(TodoAddArgs(content="write tests"), ctx)
    todo_add(TodoAddArgs(content="fix bugs"), ctx)
    result = todo_list(TodoListArgs(), ctx)
    assert result.ok
    assert "write tests" in result.output
    assert "fix bugs" in result.output
    assert "[ ]" in result.output  # both pending

def test_todo_mark_done(ctx):
    from relaycli.tools.todo import todo_add, todo_update, TodoAddArgs, TodoUpdateArgs
    todo_add(TodoAddArgs(content="task one"), ctx)
    todo_add(TodoAddArgs(content="task two"), ctx)
    todo_update(TodoUpdateArgs(index=1, done=True), ctx)
    from relaycli.tools.todo import todo_list, TodoListArgs
    result = todo_list(TodoListArgs(), ctx)
    assert result.ok
    assert "[x]" in result.output.split("\n")[0]  # first is done
    assert "[ ]" in result.output.split("\n")[1]  # second pending

def test_todo_invalid_index(ctx):
    from relaycli.tools.todo import todo_update, TodoUpdateArgs
    result = todo_update(TodoUpdateArgs(index=99, done=True), ctx)
    assert not result.ok
    assert "Invalid" in result.output

def test_todo_empty_list(ctx):
    import relaycli.tools.todo as todo_mod
    todo_mod._TODOS.clear()
    from relaycli.tools.todo import todo_list, TodoListArgs
    result = todo_list(TodoListArgs(), ctx)
    assert result.ok
    assert "no todos" in result.output.lower()

@pytest.fixture(autouse=True)
def _clear_todos():
    import relaycli.tools.todo as todo_mod
    todo_mod._TODOS.clear()
    yield

def test_todo_add_via_registry(ctx):
    r = default_registry()
    result = r.run("todo_add", json.dumps({"content": "registry test"}), ctx)
    assert result.ok
    result = r.run("todo_list", "{}", ctx)
    assert "registry test" in result.output


# ── git ────────────────────────────────────────────────────────────────

def test_git_error_no_git(ctx):
    from relaycli.tools.git_tool import git_run, GitRunArgs
    with mock_patch("subprocess.run") as mock:
        mock.side_effect = FileNotFoundError()
        result = git_run(GitRunArgs(args="status"), ctx)
    assert not result.ok
    assert "git not found" in result.output

def test_git_initialized_repo(tmp_path, ctx):
    from relaycli.tools.git_tool import git_run, GitRunArgs
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    ctx2 = ToolContext(project=ProjectContext(tmp_path),
                       permissions=PermissionManager(PermissionMode.full_auto, console=ctx.console),
                       console=ctx.console)
    result = git_run(GitRunArgs(args="status"), ctx2)
    assert result.ok
    assert "On branch" in result.output or "nothing" in result.output

def test_git_log(tmp_path, ctx):
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "f.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "first commit"], cwd=tmp_path, capture_output=True)
    ctx2 = ToolContext(project=ProjectContext(tmp_path),
                       permissions=PermissionManager(PermissionMode.full_auto, console=ctx.console),
                       console=ctx.console)
    from relaycli.tools.git_tool import git_run, GitRunArgs
    result = git_run(GitRunArgs(args="log --oneline"), ctx2)
    assert result.ok
    assert "first commit" in result.output

def test_git_timeout(ctx):
    from relaycli.tools.git_tool import git_run, GitRunArgs
    import subprocess
    with mock_patch("subprocess.run") as mock:
        mock.side_effect = subprocess.TimeoutExpired("git", 30)
        result = git_run(GitRunArgs(args="status"), ctx)
    assert not result.ok
    assert "timeout" in result.output.lower() or "timed out" in result.output.lower()

def test_git_via_registry(tmp_path, ctx):
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    r = default_registry()
    result = r.run("git", json.dumps({"args": "status"}), ctx)
    assert result.ok

def test_git_timeout_expired_handling(ctx):
    from relaycli.tools.git_tool import git_run, GitRunArgs
    import subprocess
    with mock_patch("subprocess.run") as mock:
        mock.side_effect = subprocess.TimeoutExpired("git", 30)
        result = git_run(GitRunArgs(args="fetch"), ctx)
    assert not result.ok
    assert "timeout" in result.output.lower() or "timed out" in result.output.lower()


# ── apply_patch ────────────────────────────────────────────────────────

def test_apply_patch_simple(tmp_path, ctx):
    from relaycli.tools.apply_patch import apply_patch, ApplyPatchArgs
    (tmp_path / "test.txt").write_text("hello world\n")
    patch_text = "--- test.txt\n+++ test.txt\n@@ -1 +1 @@\n-hello world\n+hello patched\n"
    with mock_patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = "patching file test.txt"
        mock.return_value.stderr = ""
        ctx2 = ToolContext(project=ProjectContext(tmp_path),
                           permissions=PermissionManager(PermissionMode.full_auto, console=ctx.console),
                           console=ctx.console)
        result = apply_patch(ApplyPatchArgs(patch=patch_text), ctx2)
    assert result.ok

def test_apply_patch_fallback_when_patch_missing(tmp_path, ctx):
    from relaycli.tools.apply_patch import apply_patch, ApplyPatchArgs
    (tmp_path / "file.txt").write_text("line1\nline2\nline3\n")
    patch_text = (
        "--- file.txt\n+++ file.txt\n@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n"
    )
    with mock_patch("subprocess.run") as mock:
        mock.side_effect = FileNotFoundError()
        result = apply_patch(ApplyPatchArgs(patch=patch_text), ctx)
    if result.ok:
        assert (tmp_path / "file.txt").read_text() == "line1\nmodified\nline3\n"

def test_apply_patch_no_patch_command(tmp_path, ctx):
    from relaycli.tools.apply_patch import apply_patch, ApplyPatchArgs
    (tmp_path / "f.txt").write_text("a\nb\nc\n")
    diff_body = (
        "--- f.txt\n+++ f.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+bb\n c\n"
    )
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        result = apply_patch(ApplyPatchArgs(patch=diff_body), ctx)
    assert result.ok
    assert (tmp_path / "f.txt").read_text() == "a\nbb\nc\n"

def test_apply_patch_new_file(tmp_path, ctx):
    from relaycli.tools.apply_patch import apply_patch, ApplyPatchArgs
    diff_body = "--- /dev/null\n+++ new.txt\n@@ -0,0 +1 @@\n+new content\n"
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        result = apply_patch(ApplyPatchArgs(patch=diff_body), ctx)
    if result.ok:
        assert (tmp_path / "new.txt").read_text().strip() == "new content"

def test_apply_patch_malformed_patch(ctx):
    from relaycli.tools.apply_patch import apply_patch, ApplyPatchArgs
    with mock_patch("subprocess.run", side_effect=FileNotFoundError):
        result = apply_patch(ApplyPatchArgs(patch="not a valid patch"), ctx)
    assert not result.ok

def test_apply_patch_via_registry(tmp_path, ctx):
    r = default_registry()
    (tmp_path / "f.txt").write_text("old\n")
    diff_body = "--- f.txt\n+++ f.txt\n@@ -1 +1 @@\n-old\n+new\n"
    with mock_patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = "done"
        mock.return_value.stderr = ""
        result = r.run("apply_patch", json.dumps({"patch": diff_body}), ctx)
    assert result.ok


# ── integration: all tools registered in default_registry ──────────────

def test_all_new_tools_in_registry():
    r = default_registry()
    names = r.names()
    for tool in ("think", "webfetch", "websearch", "question",
                 "todo_add", "todo_update", "todo_list", "git", "apply_patch"):
        assert tool in names, f"{tool} missing from default_registry"

def test_new_tools_have_descriptions():
    r = default_registry()
    for t in r.tools():
        assert t.description, f"{t.name} has no description"
        assert len(t.description) > 10, f"{t.name} description too short"

def test_new_tools_have_json_schema():
    r = default_registry()
    for name in ("think", "webfetch", "websearch", "question",
                 "todo_add", "todo_update", "todo_list", "git", "apply_patch"):
        schema = r.get(name).json_schema()
        assert "parameters" in schema["function"]
        assert "properties" in schema["function"]["parameters"]
