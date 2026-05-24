"""Stage 7 tests: regression coverage for the security & correctness audit.

Each test maps to a confirmed audit finding and proves the fix holds.
"""

from __future__ import annotations

import io
import json
import os
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from rich.console import Console

from relaycli import config as config_mod
from relaycli.agent import Agent
from relaycli.config import PermissionMode, Settings
from relaycli.context import ProjectContext
from relaycli.llm import LLM, LLMError, ToolCall, Usage
from relaycli.permissions import PermissionManager
from relaycli.session import Session
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, atomic_write
from relaycli.tools.edit_file import EditFileArgs, edit_file
from relaycli.tools.read_file import ReadFileArgs, read_file
from relaycli.tools.run_command import RunCommandArgs, run_command
from relaycli.tools.write_file import WriteFileArgs, write_file

from tests.conftest import console_text, make_context


# === H1: read_file secret/ignored guard is human-gated, not model-controlled ==
def test_readfileargs_has_no_force_field():
    # The model must not be able to flip a secret-read bypass on itself.
    assert "force" not in ReadFileArgs.model_fields


def test_read_secret_denied_without_approval(sample_project):
    ctx = make_context(sample_project, PermissionMode.suggest, prompter=lambda _t: False)
    res = read_file(ReadFileArgs(path=".env"), ctx)
    assert not res.ok
    assert "topsecret" not in res.output


def test_read_secret_not_auto_approved_even_in_full_auto(sample_project):
    # full-auto loosens edits/commands but must NEVER silently disclose secrets.
    ctx = make_context(sample_project, PermissionMode.full_auto, prompter=lambda _t: False)
    res = read_file(ReadFileArgs(path=".env"), ctx)
    assert not res.ok
    assert "topsecret" not in res.output


def test_read_secret_always_prompts_action_is_not_auto():
    for mode in (PermissionMode.suggest, PermissionMode.auto_edit, PermissionMode.full_auto):
        pm = PermissionManager(mode)
        assert pm.is_auto("read_secret") is False


def test_read_ignored_allowed_with_approval(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)  # no prompter -> auto
    res = read_file(ReadFileArgs(path="ignored.txt"), ctx)
    assert res.ok
    assert "should be ignored" in res.output


# === H2: Rich-markup injection in console previews is neutralized ============
def test_run_command_echo_escapes_markup(sample_project):
    # A model-crafted command must not be able to hide part of itself via markup.
    ctx = make_context(sample_project, PermissionMode.full_auto)
    payload = "echo hi [black on black]HIDDEN[/black on black]"
    run_command(RunCommandArgs(command=payload), ctx)
    out = console_text(ctx)
    # The literal bracket markup is preserved (escaped), so nothing is hidden.
    assert "[black on black]HIDDEN" in out


def test_run_command_bracket_command_does_not_crash(sample_project):
    # Bracketed commands previously risked a Rich MarkupError on the preview.
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(RunCommandArgs(command="echo '[done]'"), ctx)
    assert res.ok
    assert "[done]" in res.output


# === H3: a CWD .env cannot escalate the mode or redirect model traffic =======
def test_filtered_source_drops_security_fields():
    class _Inner:
        settings_cls = Settings

        def __call__(self):
            return {
                "permission_mode": "full-auto",
                "ollama_base_url": "http://evil.example",
                "model": "attacker/model",
                "openai_api_key": "sk-fromdotenv",
            }

    src = config_mod._FilteredSource(_Inner(), config_mod._DOTENV_BLOCKED_FIELDS)
    out = src()
    assert "permission_mode" not in out
    assert "ollama_base_url" not in out
    # model + provider keys still load from .env as documented.
    assert out["model"] == "attacker/model"
    assert out["openai_api_key"] == "sk-fromdotenv"


def test_cwd_dotenv_cannot_escalate_permission_mode(tmp_path, monkeypatch):
    for var in ("RELAYCLI_PERMISSION_MODE", "OLLAMA_BASE_URL", "OLLAMA_API_BASE",
                "RELAYCLI_OLLAMA_BASE_URL", "RELAYCLI_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".env").write_text(
        "RELAYCLI_PERMISSION_MODE=full-auto\n"
        "OLLAMA_BASE_URL=http://evil.example\n"
        "RELAYCLI_MODEL=evil-model-xyz\n"
        "OPENAI_API_KEY=sk-fromdotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    # The dangerous fields are ignored...
    assert settings.permission_mode is not PermissionMode.full_auto
    assert settings.ollama_base_url != "http://evil.example"
    # ...but the benign, documented ones still load, proving .env WAS read.
    assert settings.model == "evil-model-xyz"
    assert settings.openai_api_key == "sk-fromdotenv"


# === M2: read_file bounds the read itself (no unbounded-memory read) =========
def test_read_file_bounds_bytes_read(sample_project):
    big = sample_project / "big.txt"
    big.write_text("A" * 5000, encoding="utf-8")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = read_file(ReadFileArgs(path="big.txt", max_bytes=100), ctx)
    assert res.ok
    assert res.meta["truncated"] is True
    assert res.meta["bytes"] == 5000  # true size reported
    assert res.output.count("A") <= 100 + 5  # only max_bytes returned (+marker slack)


# === M3: edit_file refuses to corrupt non-UTF-8 / binary files ==============
def test_edit_file_refuses_non_utf8(sample_project):
    raw = b"caf\xe9 total = 1\n"
    (sample_project / "legacy.txt").write_bytes(raw)
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(
        EditFileArgs(path="legacy.txt", old_string="total = 1", new_string="total = 2"), ctx
    )
    assert not res.ok
    assert "utf-8" in res.output.lower()
    assert (sample_project / "legacy.txt").read_bytes() == raw  # untouched


def test_edit_file_refuses_binary(sample_project):
    (sample_project / "b.dat").write_bytes(b"\x00\x01\x02x = 1")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = edit_file(EditFileArgs(path="b.dat", old_string="x = 1", new_string="x = 2"), ctx)
    assert not res.ok
    assert "binary" in res.output.lower()


# === M4: run_command scrubs RelayCLI provider keys from the child env ========
def test_run_command_scrubs_provider_keys(sample_project, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-remain")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(
        RunCommandArgs(command='echo "K=$OPENAI_API_KEY T=$GITHUB_TOKEN"'), ctx
    )
    assert res.ok
    assert "sk-must-not-leak" not in res.output  # provider key scrubbed
    assert "ghp-should-remain" in res.output      # unrelated token preserved


def test_run_command_scrubs_openrouter_keys(sample_project, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-must-not-leak")
    monkeypatch.setenv("RELAYCLI_OPENROUTER_API_KEY", "sk-or-alias-must-not-leak")
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(
        RunCommandArgs(command='echo "A=$OPENROUTER_API_KEY B=$RELAYCLI_OPENROUTER_API_KEY"'), ctx
    )
    assert res.ok
    assert "sk-or-must-not-leak" not in res.output
    assert "sk-or-alias-must-not-leak" not in res.output


# === M5: Ctrl-C mid-tool never leaves a dangling tool_call ===================
class _BoomArgs(BaseModel):
    pass


def _boom(args, ctx=None):
    raise KeyboardInterrupt


def _fake_llm_with(response):
    class _F:
        def complete(self, messages, **kw):
            return response

    return _F()


def test_interrupt_stubs_all_tool_results(sample_project):
    reg = ToolRegistry()
    reg.add(Tool(name="boom", description="raises", args_model=_BoomArgs, func=_boom))
    from relaycli.llm import LLMResponse

    resp = LLMResponse(
        text="",
        tool_calls=[ToolCall(id="c1", name="boom", arguments="{}"),
                    ToolCall(id="c2", name="boom", arguments="{}")],
        usage=Usage(),
    )
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    settings = Settings(model="fake/model", permission_mode=PermissionMode.full_auto)
    agent = Agent(
        settings,
        console=console,
        project=ProjectContext(sample_project),
        permissions=PermissionManager(PermissionMode.full_auto, console=console),
        registry=reg,
        llm=_fake_llm_with(resp),
    )
    with pytest.raises(KeyboardInterrupt):
        agent.run("do it")

    tool_msgs = [m for m in agent.session.messages if m.get("role") == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2"}  # both answered
    assert all("interrupted" in m["content"].lower() for m in tool_msgs)


# === M6: writes are atomic and preserve the existing file mode ==============
def test_atomic_write_preserves_mode_and_content(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("old\n", encoding="utf-8")
    os.chmod(target, 0o640)
    atomic_write(target, "new\n")
    assert target.read_text() == "new\n"
    if os.name == "posix":
        assert (target.stat().st_mode & 0o777) == 0o640
    # no temp files left behind
    assert not list(tmp_path.glob("*.relaytmp"))


def test_write_file_overwrite_leaves_no_tempfiles(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = write_file(WriteFileArgs(path="app.py", content="print('x')\n"), ctx)
    assert res.ok
    assert (sample_project / "app.py").read_text() == "print('x')\n"
    assert not list(sample_project.glob("*.relaytmp"))


# === M7: token trim sheds groups within a single (one-shot) turn =============
def test_trim_sheds_groups_within_single_turn(monkeypatch):
    s = Session("system", token_budget=1, model="gpt-4o-mini")
    s.add_user("do the big task")
    for i in range(4):
        s.add_assistant_message(
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": f"c{i}", "type": "function",
                             "function": {"name": "read_file", "arguments": "{}"}}]}
        )
        s.add_tool_result(f"c{i}", "read_file", f"result {i}")
    # Force "always over budget" so trimming runs deterministically offline.
    monkeypatch.setattr(Session, "estimated_tokens", lambda self: 10 ** 9)
    dropped = s.trim()
    assert dropped > 0
    assert s.messages[0]["role"] == "user"  # leading user turn preserved
    assistants = [m for m in s.messages if m.get("role") == "assistant"]
    assert len(assistants) == 1  # only the most-recent group kept
    assert s.messages[-1]["content"] == "result 3"


# === L2/L1: run_command bounds output and enforces timeouts =================
def test_run_command_output_is_capped(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(
        RunCommandArgs(command="python3 -c \"import sys; sys.stdout.write('x'*60000)\""), ctx
    )
    assert res.meta["truncated"] is True
    assert res.output.count("x") <= 20_000 + 5


def test_run_command_timeout_is_clean(sample_project):
    ctx = make_context(sample_project, PermissionMode.full_auto)
    res = run_command(RunCommandArgs(command="sleep 5", timeout=1), ctx)
    assert not res.ok
    assert "timed out" in res.output.lower()


# === L4: is_secret covers common credential filenames =======================
@pytest.mark.parametrize(
    "name,secret",
    [
        ("credentials.json", True),
        ("gcp-credentials.yaml", True),
        ("kubeconfig", True),
        (".pypirc", True),
        ("credentials.example", False),
        ("app.py", False),
    ],
)
def test_is_secret_credential_coverage(tmp_path, name, secret):
    assert ProjectContext(tmp_path).is_secret(name) is secret


# === L5: config dir is created private (0700) ===============================
@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_ensure_config_dir_is_private(tmp_path, monkeypatch):
    private = tmp_path / ".relaycli"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", private)
    config_mod.ensure_config_dir()
    assert private.is_dir()
    assert (private.stat().st_mode & 0o777) == 0o700


# === L6: an empty/blocked provider response raises LLMError, not a traceback =
def test_normalize_empty_choices_raises_llmerror(monkeypatch):
    import relaycli.llm as llm_mod

    monkeypatch.setattr(
        llm_mod.litellm, "completion", lambda **kw: SimpleNamespace(choices=[], usage=None)
    )
    llm = LLM(Settings(model="ollama_chat/llama3"))
    with pytest.raises(LLMError):
        llm.complete([{"role": "user", "content": "hi"}], stream=False)


# === L7: streaming requests real usage via stream_options ====================
def test_streaming_requests_include_usage(monkeypatch):
    import relaycli.llm as llm_mod
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([SimpleNamespace(choices=None, usage=None)])

    monkeypatch.setattr(llm_mod.litellm, "completion", fake_completion)
    llm = LLM(Settings(model="ollama_chat/llama3"))
    llm.complete([{"role": "user", "content": "hi"}], stream=True)
    assert captured.get("stream") is True
    assert captured.get("stream_options") == {"include_usage": True}
