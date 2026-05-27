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
