"""Stage 4 tests: the agent loop with a MOCKED LLM (no real API calls)."""

from __future__ import annotations

import io
import json

from rich.console import Console

from relaycli.agent import Agent
from relaycli.config import PermissionMode, Settings
from relaycli.context import ProjectContext
from relaycli.llm import LLMResponse, ToolCall, Usage
from relaycli.permissions import PermissionManager
from relaycli.tools.base import ToolContext
from relaycli.tools.read_file import ReadFileArgs, read_file


class FakeLLM:
    """Returns scripted responses; never touches the network."""

    def __init__(self, responses, *, loop_last: bool = False) -> None:
        self._responses = list(responses)
        self._loop_last = loop_last
        self.calls: list[list[dict]] = []

    def complete(self, messages, *, tools=None, model=None, temperature=None,
                 stream=False, on_token=None):
        self.calls.append(list(messages))
        if self._responses:
            resp = self._responses.pop(0)
        elif self._loop_last:
            resp = self._last
        else:
            raise AssertionError("FakeLLM ran out of scripted responses")
        self._last = resp
        if on_token and resp.text:
            on_token(resp.text)
        return resp


def _usage() -> Usage:
    return Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8)


def _resp(text="", tool_calls=None) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=tool_calls or [], usage=_usage())


def _tc(name, args, call_id="c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(args))


def _build_agent(project_root, llm, mode=PermissionMode.full_auto, prompter=None):
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    settings = Settings(model="fake/model", permission_mode=mode, max_iterations=50)
    permissions = PermissionManager(mode, prompter=prompter, console=console)
    return Agent(
        settings,
        console=console,
        project=ProjectContext(project_root),
        permissions=permissions,
        llm=llm,
    )


def test_full_read_edit_run_cycle(sample_project):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("read_file", {"path": "app.py"})]),
        _resp(tool_calls=[_tc("edit_file", {"path": "app.py",
                                            "old_string": "return 'hi'",
                                            "new_string": "return 'hello'"})]),
        _resp(tool_calls=[_tc("run_command", {"command": "echo built"})]),
        _resp(text="All done."),
    ])
    agent = _build_agent(sample_project, llm)
    result = agent.run("improve app.py and verify")

    assert result.stopped_reason == "done"
    assert result.iterations == 4
    assert result.tool_calls == 3
    assert "return 'hello'" in (sample_project / "app.py").read_text()
    assert result.usage.total_tokens == 32  # 4 calls * 8


def test_agent_recovers_when_model_edits_before_reading(sample_project):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("edit_file", {
            "path": "app.py",
            "old_string": "missing",
            "new_string": "x",
        })]),
        _resp(tool_calls=[_tc("edit_file", {
            "path": "app.py",
            "old_string": "return 'hi'",
            "new_string": "return 'hello'",
        })]),
        _resp(text="Updated."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run("update app.py")

    assert result.stopped_reason == "done"
    assert "return 'hello'" in (sample_project / "app.py").read_text()
    tool_msgs = [m for m in llm.calls[1] if m.get("role") == "tool"]
    assert any("Read-before-edit is required" in (m.get("content") or "") for m in tool_msgs)
    assert any("return 'hi'" in (m.get("content") or "") for m in tool_msgs)


def test_text_tool_json_is_executed(sample_project):
    fake = (
        '```json\n'
        '{"name":"write_file","arguments":{"path":"mandarin/index.html","content":"<h1>你好</h1>"}}\n'
        '```'
    )
    llm = FakeLLM([
        _resp(text=fake),
        _resp(text="Created the Mandarin page."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run("buat web belajar mandarin")

    assert result.stopped_reason == "done"
    assert result.tool_calls == 1
    assert (sample_project / "mandarin" / "index.html").read_text() == "<h1>你好</h1>"
    tool_msgs = [m for m in llm.calls[-1] if m.get("role") == "tool"]
    assert any("Wrote" in (m.get("content") or "") for m in tool_msgs)


def test_text_create_folder_json_is_executed(sample_project):
    fake = (
        '```json\n'
        '{"name":"create_folder","arguments":{"folder_name":"toko laptop"}}\n'
        '```'
    )
    llm = FakeLLM([
        _resp(text=fake),
        _resp(text="Folder created."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run('buat satu folder bernama "toko laptop"')

    assert result.stopped_reason == "done"
    assert result.tool_calls == 1
    assert (sample_project / "toko laptop").is_dir()


def test_text_tool_array_json_executes_multiple_calls(sample_project):
    fake = (
        '```json\n'
        '[{"name":"create_folder","arguments":{"path":"shop"}},'
        '{"name":"write_file","arguments":{"path":"shop/index.html","content":"<h1>Shop</h1>"}}]\n'
        '```'
    )
    llm = FakeLLM([
        _resp(text=fake),
        _resp(text="Shop created."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run("buat web toko sederhana")

    assert result.stopped_reason == "done"
    assert result.tool_calls == 2
    assert (sample_project / "shop").is_dir()
    assert (sample_project / "shop" / "index.html").read_text() == "<h1>Shop</h1>"


def test_text_tool_alias_and_string_args_are_normalized(sample_project):
    fake = '```json\n{"tool":"mkdir","args":"toko laptop"}\n```'
    llm = FakeLLM([
        _resp(text=fake),
        _resp(text="Folder created."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run('buat satu folder bernama "toko laptop"')

    assert result.stopped_reason == "done"
    assert result.tool_calls == 1
    assert (sample_project / "toko laptop").is_dir()


def test_empty_response_is_retried_instead_of_marked_done(sample_project):
    llm = FakeLLM([
        _resp(text=""),
        _resp(tool_calls=[_tc("create_folder", {"path": "toko laptop"})]),
        _resp(text="Folder created."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run('buat satu folder bernama "toko laptop"')

    assert result.stopped_reason == "done"
    assert result.iterations == 3
    assert (sample_project / "toko laptop").is_dir()
    user_messages = [
        m.get("content", "") for m in llm.calls[1]
        if m.get("role") == "user"
    ]
    assert any("previous response was empty" in text for text in user_messages)


def test_actionable_frontend_task_retries_when_no_files_written(sample_project):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("create_folder", {"path": "toko laptop"})]),
        _resp(text="I'm sorry, but I don't have enough information to continue."),
        _resp(tool_calls=[_tc("write_file", {
            "path": "toko laptop/index.html",
            "content": "<h1>Toko Laptop</h1>",
        })]),
        _resp(text="Website created."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run('buatin website toko laptop nuansa gelap di folder "toko laptop"')

    assert result.stopped_reason == "done"
    assert result.tool_calls == 2
    assert (sample_project / "toko laptop" / "index.html").read_text() == "<h1>Toko Laptop</h1>"
    user_messages = [
        m.get("content", "") for m in llm.calls[2]
        if m.get("role") == "user"
    ]
    assert any("no files have been written" in text for text in user_messages)
    assert any("`toko laptop`" in text for text in user_messages)


def test_actionable_file_task_rejects_fake_created_file_claim(sample_project):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("create_folder", {"path": "smoke-folder"})]),
        _resp(text="<tool_response>\nCreated file 'smoke-folder/index.html'.\n</tool_response>"),
        _resp(text="Please provide me with the necessary information to complete the task."),
        _resp(text="<tool_response>\nCreated file 'smoke-folder/index.html'.\n</tool_response>"),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run(
        'buat satu folder bernama "smoke-folder", lalu buat file '
        'smoke-folder/ok.txt berisi OK'
    )

    assert result.stopped_reason == "error"
    assert "without writing or editing any files" in result.final_text
    assert not (sample_project / "smoke-folder" / "ok.txt").exists()


def test_actionable_file_task_rejects_clarification_without_write(sample_project):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("create_folder", {"path": "smoke-folder"})]),
        _resp(text="Please provide me with the necessary information to complete the task."),
        _resp(text="I will continue once you provide the details."),
        _resp(text="Please provide me with the necessary information to complete the task."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run(
        'buat satu folder bernama "smoke-folder", lalu buat file '
        'smoke-folder/ok.txt berisi OK'
    )

    assert result.stopped_reason == "error"
    assert "without writing or editing any files" in result.final_text
    assert not (sample_project / "smoke-folder" / "ok.txt").exists()


def test_actionable_file_task_recovers_after_fake_created_file_claim(sample_project):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("create_folder", {"path": "smoke-folder"})]),
        _resp(text="<tool_response>\nCreated file 'smoke-folder/index.html'.\n</tool_response>"),
        _resp(tool_calls=[_tc("write_file", {
            "path": "smoke-folder/ok.txt",
            "content": "OK",
        })]),
        _resp(text="done"),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run(
        'buat satu folder bernama "smoke-folder", lalu buat file '
        'smoke-folder/ok.txt berisi OK'
    )

    assert result.stopped_reason == "done"
    assert result.tool_calls == 2
    assert (sample_project / "smoke-folder" / "ok.txt").read_text() == "OK"


def test_explain_website_prompt_can_finish_without_file_write(sample_project):
    llm = FakeLLM([_resp(text="This website is a static HTML app.")])
    agent = _build_agent(sample_project, llm)

    result = agent.run("jelaskan website ini")

    assert result.stopped_reason == "done"
    assert result.final_text == "This website is a static HTML app."
    assert result.tool_calls == 0


def test_folder_only_task_does_not_require_file_write(sample_project):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("create_folder", {"path": "toko laptop"})]),
        _resp(text="Folder created."),
    ])
    agent = _build_agent(sample_project, llm)

    result = agent.run('buat satu folder bernama "toko laptop"')

    assert result.stopped_reason == "done"
    assert result.tool_calls == 1
    assert (sample_project / "toko laptop").is_dir()


def test_repeated_empty_response_is_error_not_done(sample_project):
    llm = FakeLLM([_resp(text=""), _resp(text=""), _resp(text="")])
    agent = _build_agent(sample_project, llm)

    result = agent.run("buat website toko laptop")

    assert result.stopped_reason == "error"
    assert "empty responses" in result.final_text
    assert result.tool_calls == 0


def test_unknown_text_tool_json_is_retried_then_recovers(sample_project):
    fake = '```json\n{"name":"build_web_app","arguments":{"output_folder":"web-app"}}\n```'
    real = (
        '```json\n'
        '{"name":"write_file","arguments":{"path":"web-app/index.html","content":"<h1>OK</h1>"}}\n'
        '```'
    )
    llm = FakeLLM([_resp(text=fake), _resp(text=real), _resp(text="Created.")])
    agent = _build_agent(sample_project, llm)

    result = agent.run("buat web")

    assert result.stopped_reason == "done"
    assert result.tool_calls == 1
    assert (sample_project / "web-app" / "index.html").read_text() == "<h1>OK</h1>"
    user_messages = [
        m.get("content", "") for m in llm.calls[1]
        if m.get("role") == "user"
    ]
    assert any("build_web_app" in text and "write_file" in text for text in user_messages)


def test_repeated_unknown_text_tool_json_is_error(sample_project):
    fake = '```json\n{"name":"build_web_app","arguments":{"output_folder":"web-app"}}\n```'
    llm = FakeLLM([_resp(text=fake), _resp(text=fake), _resp(text=fake)])
    agent = _build_agent(sample_project, llm)

    result = agent.run("buat web")

    assert result.stopped_reason == "error"
    assert "fake tool call" in result.final_text
    assert "did not recover" in result.final_text
    assert result.iterations == 3
    assert result.tool_calls == 0


def test_tool_error_recovery(sample_project):
    # First response carries malformed JSON args -> ToolError -> error fed back.
    bad = ToolCall(id="c1", name="read_file", arguments='{"path": ')  # invalid JSON
    llm = FakeLLM([
        _resp(tool_calls=[bad]),
        _resp(text="Recovered after the error."),
    ])
    agent = _build_agent(sample_project, llm)
    result = agent.run("do something")

    assert result.stopped_reason == "done"
    assert result.iterations == 2
    # The tool-result message fed back to the model should describe the error.
    last_call_messages = llm.calls[-1]
    tool_msgs = [m for m in last_call_messages if m.get("role") == "tool"]
    assert any("ERROR" in (m.get("content") or "") for m in tool_msgs)


def test_malformed_tool_args_do_not_poison_history(sample_project):
    # Regression: a provider abort mid-stream can truncate argument JSON
    # (observed live: Cohere via OpenRouter, finish_reason 'error'). The
    # truncated string used to be replayed verbatim in every later request,
    # which strict providers 400 ("tool arguments must be a stringified JSON
    # object") — bricking the session. History must stay replayable.
    truncated = ToolCall(
        id="c1", name="write_file",
        arguments='{"path": "index.html", "content": "<!DOCTYPE ht',
    )
    llm = FakeLLM([
        _resp(tool_calls=[truncated]),
        _resp(text="Recovered."),
    ])
    agent = _build_agent(sample_project, llm)
    result = agent.run("buatkan web sederhana")

    assert result.stopped_reason == "done"
    # Every assistant tool_call replayed to the provider parses as a JSON object.
    for message in llm.calls[-1]:
        for tc in message.get("tool_calls") or []:
            assert isinstance(json.loads(tc["function"]["arguments"]), dict)
    # ... and the model was told the call failed, so it can retry.
    tool_msgs = [m for m in llm.calls[-1] if m.get("role") == "tool"]
    assert any("ERROR" in (m.get("content") or "") for m in tool_msgs)


def test_permission_gating_in_loop(sample_project):
    # Suggest mode + a prompter that always declines: the write must not happen.
    llm = FakeLLM([
        _resp(tool_calls=[_tc("write_file", {"path": "new.txt", "content": "x"})]),
        _resp(text="Okay, I won't create it."),
    ])
    agent = _build_agent(sample_project, llm, mode=PermissionMode.suggest,
                         prompter=lambda _t: False)
    result = agent.run("make a file")

    assert result.stopped_reason == "done"
    assert not (sample_project / "new.txt").exists()


def test_iteration_cap_stops_cleanly(sample_project):
    # The model always asks for another (harmless) command -> must hit the cap.
    llm = FakeLLM([_resp(tool_calls=[_tc("run_command", {"command": "true"})])],
                  loop_last=True)
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    settings = Settings(model="fake/model", permission_mode=PermissionMode.full_auto,
                        max_iterations=3)
    agent = Agent(
        settings,
        console=console,
        project=ProjectContext(sample_project),
        permissions=PermissionManager(PermissionMode.full_auto, console=console),
        llm=llm,
    )
    result = agent.run("loop forever")

    assert result.stopped_reason == "max_iterations"
    assert result.iterations == 3
    assert result.tool_calls == 3


def test_system_prompt_mentions_tools_and_mode(sample_project):
    llm = FakeLLM([_resp(text="hi")])
    agent = _build_agent(sample_project, llm)
    system = agent.session.to_messages()[0]["content"]
    assert "read_file" in system and "run_command" in system
    assert "full-auto" in system
    assert str(sample_project.resolve()) in system


def test_system_prompt_discourages_invented_high_level_tools(sample_project):
    agent = _build_agent(sample_project, FakeLLM([_resp(text="hi")]))

    system = agent.session.to_messages()[0]["content"]

    assert "Never invent high-level tools" in system
    assert "build_web_app" in system
    assert "create_folder" in system and "write_file" in system


def test_system_prompt_lists_existing_web_files(tmp_path):
    web = tmp_path / "belajar-mandarin"
    web.mkdir()
    (web / "index.html").write_text("<h1>Belajar Mandarin</h1>", encoding="utf-8")
    (web / "styles.css").write_text("body { color: white; }", encoding="utf-8")
    (web / "app.js").write_text("console.log('mandarin')", encoding="utf-8")
    agent = _build_agent(tmp_path, FakeLLM([_resp(text="hi")]))

    system = agent.session.to_messages()[0]["content"]

    assert "Project snapshot:" in system
    assert "belajar-mandarin/index.html" in system
    assert "belajar-mandarin/styles.css" in system
    assert "Do not assume `src/index.html` unless it is listed" in system


def test_missing_read_path_suggests_existing_web_candidate(tmp_path):
    web = tmp_path / "belajar-mandarin"
    web.mkdir()
    (web / "index.html").write_text("<h1>Belajar Mandarin</h1>", encoding="utf-8")
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    ctx = ToolContext(
        ProjectContext(tmp_path),
        PermissionManager(PermissionMode.full_auto, console=console),
        console,
    )

    result = read_file(ReadFileArgs(path="src/index.html"), ctx)

    assert not result.ok
    assert "src/index.html" in result.output
    assert "belajar-mandarin/index.html" in result.output
