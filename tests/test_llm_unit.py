"""Stage 2 unit tests: tool registry + LLM normalization helpers (no network)."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from relaycli.llm import (
    LLM,
    LLMError,
    LLMResponse,
    ToolCall,
    Usage,
    _resolve_arguments,
    best_ollama_model,
    tool_capability_warning,
    make_tool_result_message,
)
from relaycli.tools import ToolError, ToolRegistry


class _Args(BaseModel):
    x: int = Field(description="a number")
    label: str = "hi"


def _echo(args: _Args, ctx=None) -> str:
    return f"{args.label}:{args.x}"


def test_registry_schema_and_run():
    reg = ToolRegistry()
    reg.register("echo", "Echo a value", _Args)(_echo)

    schemas = reg.schemas()
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "echo"
    assert "x" in schemas[0]["function"]["parameters"]["properties"]

    assert reg.run("echo", '{"x": 3}') == "hi:3"
    assert reg.run("echo", {"x": 4, "label": "yo"}) == "yo:4"


def test_registry_unknown_tool():
    reg = ToolRegistry()
    with pytest.raises(ToolError):
        reg.run("nope", "{}")


def test_tool_malformed_json_raises_toolerror():
    reg = ToolRegistry()
    reg.register("echo", "Echo", _Args)(_echo)
    with pytest.raises(ToolError):
        reg.run("echo", '{"x": 3')  # truncated JSON
    with pytest.raises(ToolError):
        reg.run("echo", '{"label": "no-x"}')  # missing required field


@pytest.mark.parametrize(
    "frags,expected",
    [
        (['{"timezone": "utc"}'], '{"timezone": "utc"}'),
        (['{"timezone":', ' "utc"}'], '{"timezone": "utc"}'),            # incremental
        (['{"timezone": "utc"}', '{"timezone": "utc"}'], '{"timezone": "utc"}'),  # doubled
        ([], "{}"),
    ],
)
def test_resolve_arguments(frags, expected):
    out = _resolve_arguments(frags)
    assert json.loads(out) == json.loads(expected)


def test_to_assistant_message_roundtrip():
    resp = LLMResponse(
        text="",
        tool_calls=[ToolCall(id="c1", name="get_time", arguments='{"timezone": "utc"}')],
    )
    msg = resp.to_assistant_message()
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["function"]["name"] == "get_time"

    tmsg = make_tool_result_message(resp.tool_calls[0], "2026-01-01")
    assert tmsg == {"role": "tool", "tool_call_id": "c1", "name": "get_time", "content": "2026-01-01"}


def test_to_assistant_message_sanitizes_malformed_arguments():
    # A provider abort mid-stream (finish_reason 'error') can truncate the
    # argument JSON. Strict providers (Cohere via OpenRouter) then 400 the
    # WHOLE conversation on every later request — "tool arguments must be a
    # stringified JSON object" — bricking the session. History must always
    # carry a valid JSON object; the model still sees the ERROR tool result.
    truncated = '{"path": "index.html", "content": "<!DOCTYPE ht'
    resp = LLMResponse(
        text="",
        tool_calls=[ToolCall(id="c1", name="write_file", arguments=truncated)],
    )
    msg = resp.to_assistant_message()
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {}
    # the in-memory call keeps the raw string (tool error reporting uses it)
    assert resp.tool_calls[0].arguments == truncated


def test_to_assistant_message_sanitizes_non_object_arguments():
    for bad in ("[1, 2]", '"just a string"', "42", "", "   "):
        resp = LLMResponse(
            text="", tool_calls=[ToolCall(id="c1", name="t", arguments=bad)]
        )
        args = resp.to_assistant_message()["tool_calls"][0]["function"]["arguments"]
        assert json.loads(args) == {}, f"arguments {bad!r} must serialize as an object"


def test_to_assistant_message_keeps_valid_arguments_verbatim():
    raw = '{"timezone": "utc", "n": 3}'
    resp = LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="t", arguments=raw)])
    assert resp.to_assistant_message()["tool_calls"][0]["function"]["arguments"] == raw


def test_usage_add():
    a = Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3, cost_usd=0.5)
    b = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30, cost_usd=0.25)
    c = a.add(b)
    assert (c.prompt_tokens, c.completion_tokens, c.total_tokens, c.cost_usd) == (11, 22, 33, 0.75)


def test_credential_kwargs_missing_key(monkeypatch):
    from relaycli.config import Settings

    for key in ("OPENAI_API_KEY", "RELAYCLI_OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    llm = LLM(Settings())
    with pytest.raises(LLMError):
        llm._credential_kwargs("gpt-4o-mini")


def test_credential_kwargs_openrouter(monkeypatch):
    from relaycli.config import Settings

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    llm = LLM(Settings())
    kwargs = llm._credential_kwargs("openrouter/openai/gpt-4o-mini")
    assert kwargs == {"api_key": "sk-or-test"}


def test_credential_kwargs_ollama_uses_base_url():
    from relaycli.config import Settings

    llm = LLM(Settings(ollama_base_url="http://localhost:11434"))
    kwargs = llm._credential_kwargs("ollama_chat/llama3.2:3b")
    assert kwargs == {"api_base": "http://localhost:11434"}


def test_credential_kwargs_unknown_model_raises():
    from relaycli.config import Settings

    llm = LLM(Settings())
    with pytest.raises(LLMError):
        llm._credential_kwargs("not-a-real-provider/zzz-model")


def test_wrap_error_auth_failure_gives_actionable_hint():
    from relaycli.config import Settings

    class AuthenticationError(Exception):
        pass

    llm = LLM(Settings())
    err = llm._wrap_error(
        AuthenticationError('OpenrouterException - {"error":{"message":"User not found.","code":401}}'),
        "openrouter/cohere/north-mini-code:free",
    )
    msg = str(err)
    assert "rejected" in msg
    assert "relaycli config set-key openrouter" in msg


def test_wrap_error_auth_failure_unknown_provider_still_hints():
    from relaycli.config import Settings

    class AuthenticationError(Exception):
        pass

    llm = LLM(Settings())
    msg = str(llm._wrap_error(AuthenticationError("401"), "not-a-real-provider/zzz"))
    assert "rejected" in msg


def test_wrap_error_non_auth_unchanged():
    from relaycli.config import Settings

    llm = LLM(Settings())
    msg = str(llm._wrap_error(TimeoutError("took too long"), "openrouter/x/y"))
    assert "rejected" not in msg
    assert "Model call failed for 'openrouter/x/y'" in msg


def test_best_ollama_model_prefers_tool_capable_hint(monkeypatch):
    from relaycli.config import Settings
    import relaycli.llm as llm

    monkeypatch.setattr(llm, "ollama_models", lambda settings: ["qwen2.5-coder:7b", "llama3.1:8b"])
    assert best_ollama_model(Settings()) == "ollama_chat/llama3.1:8b"


def test_best_ollama_model_prefers_qwen_coder_over_unknown_small_model(monkeypatch):
    from relaycli.config import Settings
    import relaycli.llm as llm

    monkeypatch.setattr(llm, "ollama_models", lambda settings: ["smollm2:360m", "qwen2.5-coder:0.5b"])
    assert best_ollama_model(Settings()) == "ollama_chat/qwen2.5-coder:0.5b"


def test_tool_capability_warning_for_risky_local_model():
    msg = tool_capability_warning("ollama_chat/deepseek-coder:6.7b")
    assert msg is not None
    assert "plain text" in msg
    assert tool_capability_warning("ollama_chat/qwen2.5-coder:0.5b") is None
    assert tool_capability_warning("ollama_chat/llama3.1:8b") is None
