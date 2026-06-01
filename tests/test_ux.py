"""Stage 5 tests: REPL slash-commands + rendering (no network)."""

from __future__ import annotations

import io
import os
import re

import pytest
from rich.console import Console

from relaycli.config import PermissionMode, Settings
from relaycli.llm import ToolCall, Usage
from relaycli.render import RichReporter, make_unified_diff, render_model_warning, render_task_summary
from relaycli.repl import Repl
from relaycli.tools.base import ToolResult


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch, tmp_path):
    """Keep this module's tests hermetic on configured machines.

    Ambient RELAYCLI_* env vars and a real ~/.relaycli/config.toml would
    otherwise leak into Settings (relay_enabled, permission_mode, role
    models, ...) and break exact-value assertions. Local to this module on
    purpose: the opt-in live-E2E flow depends on RELAYCLI_* env vars.
    """
    for var in list(os.environ):
        if var.startswith("RELAYCLI_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(Settings.model_config, "toml_file", str(tmp_path / "no-config.toml"))
    from relaycli import appconfig

    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "app-config.toml")


def _repl(mode=PermissionMode.suggest, model="gpt-4o-mini", **kw):
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    settings = Settings(model=model, permission_mode=mode, **kw)
    return Repl(settings, console=console), console


def _out(console) -> str:
    return console.file.getvalue()


# --- slash commands ----------------------------------------------------
def test_slash_model_switches_model():
    repl, console = _repl()
    assert repl._handle_slash("/model gpt-4o") is False
    assert repl.settings.model == "gpt-4o"
    assert repl.agent.session.model == "gpt-4o"


def test_slash_model_warns_for_slow_local_model(monkeypatch):
    import relaycli.llm as llm
    import relaycli.repl as repl_mod

    monkeypatch.setattr(llm, "ollama_models", lambda settings, timeout=0.8: ["qwen3:4b"])
    monkeypatch.setattr(repl_mod, "slow_local_model_warning", lambda model: "slow local model")
    repl, console = _repl()

    assert repl._handle_slash("/model ollama_chat/qwen3:4b") is False

    out = _out(console)
    assert "may be slow unless Ollama shows 100% GPU" in out
    assert "manual model kept" in out


def test_repl_skips_agent_for_slow_local_model(monkeypatch):
    import relaycli.repl as repl_mod

    monkeypatch.setattr(repl_mod, "slow_local_model_warning", lambda model: "slow local model")
    monkeypatch.setattr(repl_mod, "recommended_fast_local_model", lambda settings: None)
    repl, console = _repl(model="ollama_chat/qwen3:4b")
    monkeypatch.setattr(repl.agent, "run", lambda *args, **kwargs: pytest.fail("agent ran"))

    repl._run_agent("jelasin repo ini")

    out = _out(console)
    assert "slow local model" in out


def test_repl_auto_switches_slow_local_model(monkeypatch):
    import relaycli.repl as repl_mod
    from relaycli.agent import AgentResult

    monkeypatch.setattr(repl_mod, "slow_local_model_warning", lambda model: "partial gpu")
    monkeypatch.setattr(
        repl_mod,
        "recommended_fast_local_model",
        lambda settings: "ollama_chat/qwen2.5-coder:0.5b",
    )
    repl, console = _repl(model="ollama_chat/qwen3:4b")
    called = {}

    def fake_run(request, reporter=None):
        called["request"] = request
        return AgentResult("ok", 1, 0, Usage(total_tokens=1), "done")

    monkeypatch.setattr(repl.agent, "run", fake_run)

    repl._run_agent("jelasin repo ini")

    assert called["request"] == "jelasin repo ini"
    assert repl.settings.model == "ollama_chat/qwen2.5-coder:0.5b"
    assert repl.agent.session.model == "ollama_chat/qwen2.5-coder:0.5b"
    out = _out(console)
    assert "model auto-switch" in out


def test_repl_respects_manually_selected_slow_local_model(monkeypatch):
    import relaycli.llm as llm
    import relaycli.repl as repl_mod
    from relaycli.agent import AgentResult

    monkeypatch.setattr(llm, "ollama_models", lambda settings, timeout=0.8: ["qwen3:4b"])
    monkeypatch.setattr(repl_mod, "slow_local_model_warning", lambda model: "partial gpu")
    monkeypatch.setattr(
        repl_mod,
        "recommended_fast_local_model",
        lambda settings: "ollama_chat/qwen2.5-coder:0.5b",
    )
    repl, console = _repl(model="ollama_chat/qwen2.5-coder:1.5b")
    called = {}

    def fake_run(request, reporter=None):
        called["request"] = request
        return AgentResult("ok", 1, 0, Usage(total_tokens=1), "done")

    repl._handle_slash("/model ollama_chat/qwen3:4b")
    monkeypatch.setattr(repl.agent, "run", fake_run)

    repl._run_agent("jelasin repo ini")

    assert called["request"] == "jelasin repo ini"
    assert repl.settings.model == "ollama_chat/qwen3:4b"
    out = _out(console)
    assert "manual model kept" in out
    assert out.count("manual model kept") == 1
    assert "Switch to a smaller" not in out
    assert "model auto-switch" not in out


def test_slash_model_rejects_missing_installed_ollama_model(monkeypatch):
    import relaycli.llm as llm

    monkeypatch.setattr(llm, "ollama_models", lambda settings, timeout=0.8: ["qwen2.5-coder:0.5b"])
    repl, console = _repl(model="ollama_chat/qwen2.5-coder:0.5b")

    assert repl._handle_slash("/model ollama_chat/llama3.1") is False

    out = _out(console)
    assert "not installed" in out
    assert repl.settings.model == "ollama_chat/qwen2.5-coder:0.5b"


def test_slash_model_lists_installed_ollama_models(monkeypatch):
    import relaycli.llm as llm

    monkeypatch.setattr(llm, "ollama_models", lambda settings, timeout=0.5: ["qwen2.5-coder:0.5b"])
    repl, console = _repl(model="ollama_chat/qwen2.5-coder:0.5b")

    assert repl._handle_slash("/model") is False

    out = _out(console)
    assert "local Ollama" in out
    assert "ollama_chat/qwen2.5-coder:0.5b" in out


def test_slash_model_searches_catalog():
    repl, console = _repl()

    assert repl._handle_slash("/model search qwen") is False

    out = _out(console)
    assert "qwen" in out.lower()
    assert "switch with" in out


def test_slash_mode_switches_and_updates_system_prompt():
    repl, console = _repl()
    repl._handle_slash("/mode full-auto")
    assert repl.settings.permission_mode is PermissionMode.full_auto
    assert repl.permissions.mode is PermissionMode.full_auto
    # The system prompt the agent sends reflects the new mode.
    assert "full-auto" in repl.agent.session.to_messages()[0]["content"]
    # full-auto prints a warning banner.
    assert "full-auto" in _out(console)


def test_slash_runtime_toggles_persist_to_next_session():
    from relaycli import appconfig

    repl, _console = _repl()
    repl._handle_slash("/mode full-auto")
    repl._handle_slash("/relay on")
    repl._handle_slash("/agents tester on")
    repl._handle_slash("/agents tasks on")
    repl._handle_slash("/skill auto off")

    cfg = appconfig.load_app_config()
    assert cfg._raw["permission_mode"] == "full-auto"
    assert cfg.preference("permission_mode") == "full-auto"
    assert cfg._raw["relay_enabled"] is True
    assert cfg._raw["relay_tester"] is True
    assert cfg._raw["relay_split_tasks"] is True
    assert cfg._raw["skills_auto"] is False


def test_slash_mode_invalid_is_rejected():
    repl, console = _repl()
    repl._handle_slash("/mode banana")
    assert repl.settings.permission_mode is PermissionMode.suggest
    assert "Invalid mode" in _out(console)


def test_slash_clear_resets_session():
    repl, _ = _repl()
    repl.agent.session.add_user("hello")
    assert repl.agent.session.messages
    repl._handle_slash("/clear")
    assert repl.agent.session.messages == []


def test_slash_exit_returns_true():
    repl, _ = _repl()
    assert repl._handle_slash("/exit") is True
    assert repl._handle_slash("/quit") is True


def test_slash_unknown_command():
    repl, console = _repl()
    assert repl._handle_slash("/wat") is False
    assert "Unknown command" in _out(console)


def test_repl_frontend_prompt_runs_agent_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    repl, _console = _repl(mode=PermissionMode.full_auto)
    calls = []
    monkeypatch.setattr(repl, "_run_agent", lambda request: calls.append(request))

    assert repl._handle_line("buatin saya web platform belajar mandarin") is False

    assert calls == ["buatin saya web platform belajar mandarin"]
    assert not (tmp_path / "belajar-mandarin").exists()


def test_repl_frontend_shop_scaffold_runs_without_agent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    repl, console = _repl(mode=PermissionMode.full_auto, local_scaffolds=True)

    assert repl._handle_line(
        "buatin saya web toko spatu di front endnya aja di folder baru namanya sepatuu yaa"
    ) is False

    assert (tmp_path / "sepatuu" / "index.html").is_file()
    assert (tmp_path / "sepatuu" / "styles.css").is_file()
    assert "created" in _out(console)


def test_repl_frontend_fashion_shop_scaffold_uses_fashion_copy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    repl, console = _repl(mode=PermissionMode.full_auto, local_scaffolds=True)

    assert repl._handle_line(
        "buatin saya website toko baju bukan toko sepatu buat ulang ya"
    ) is False

    html = (tmp_path / "toko-baju" / "index.html").read_text()
    app_js = (tmp_path / "toko-baju" / "app.js").read_text()
    assert "Baju harian" in html
    assert "City Coach Jacket" in html
    assert "Everyday Cotton Tee" in app_js
    assert "Aero Street Runner" not in html + app_js
    assert "created" in _out(console)


def test_repl_frontend_fashion_shop_scaffold_respects_quoted_folder_and_dark_tone(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    repl, console = _repl(mode=PermissionMode.full_auto, local_scaffolds=True)

    assert repl._handle_line(
        'tolong build ulang website toko baju online di folder baru bernama "toko baju" pake html css js aja nuansa hitam gekao'
    ) is False

    html = (tmp_path / "toko baju" / "index.html").read_text()
    css = (tmp_path / "toko baju" / "styles.css").read_text()
    app_js = (tmp_path / "toko baju" / "app.js").read_text()
    assert 'class="theme-dark"' in html
    assert "Baju harian" in html
    assert "Everyday Cotton Tee" in app_js
    assert "Aero Street Runner" not in html + app_js
    assert ".theme-dark" in css
    assert "created" in _out(console)


def test_repl_mandarin_learning_platform_scaffold(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    repl, console = _repl(mode=PermissionMode.full_auto, local_scaffolds=True)

    assert repl._handle_line("buatin saya web platform belajar mandarin") is False

    html = (tmp_path / "belajar-mandarin" / "index.html").read_text()
    app_js = (tmp_path / "belajar-mandarin" / "app.js").read_text()
    assert "MandarinLab" in html
    assert "Belajar Mandarin" in html
    assert "Modul populer" in html
    assert "Nada dan Pinyin" in app_js
    assert "Pelajari" in app_js
    assert "created" in _out(console)


def test_repl_frontend_shop_scaffold_defaults_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    repl, console = _repl(mode=PermissionMode.full_auto, local_scaffolds=True)

    assert repl._handle_line("buatin saya web toko sepatu fokus frontend aja") is False

    assert (tmp_path / "toko-sepatu" / "index.html").is_file()
    assert (tmp_path / "toko-sepatu" / "styles.css").is_file()
    assert "created" in _out(console)


def test_slash_help_lists_commands():
    repl, console = _repl()
    repl._handle_slash("/help")
    text = _out(console)
    assert "/model" in text and "/mode" in text and "/diff" in text
    assert "/setup" in text and "/services" in text and "/doctor" in text


def test_slash_palette_and_unknown_suggestion():
    repl, console = _repl()
    repl._handle_slash("/")
    repl._handle_slash("/modle")
    text = _out(console)
    assert "press / for commands" in text
    assert "/setup" in text and "/model" in text
    assert "Did you mean" in text and "/model" in text


def test_slash_services_lists_optional_profiles():
    repl, console = _repl()
    repl._handle_slash("/services")
    text = _out(console)
    assert "ollama" in text and "n8n" in text
    assert "/services start ollama,n8n" in text


def test_slash_help_matches_slash_commands_registry():
    """render_help() must document every entry in SLASH_COMMANDS — the two
    are meant to stay in lockstep (see the comment above SLASH_COMMANDS).
    'quit' is a documented alias of /exit (its own SLASH_COMMANDS entry says
    so) rather than a separate row, so it's exempt."""
    from relaycli.repl import SLASH_COMMANDS

    repl, console = _repl()
    repl._handle_slash("/help")
    text = _out(console)
    for cmd in SLASH_COMMANDS:
        if cmd == "quit":
            continue
        assert f"/{cmd}" in text, f"/{cmd} is missing from /help"


def test_slash_diff_no_crash():
    # In this repo .git exists; in a fresh dir it falls back gracefully.
    repl, console = _repl()
    repl._handle_slash("/diff")
    # Either shows a diff or a "no changes"/"not a git repo" note — never raises.
    assert _out(console) != ""


# --- rendering ---------------------------------------------------------
def test_rich_reporter_tool_lines_have_claude_shape():
    # Two-line outcome: "⏺ tool_name" then an indented "⎿  summary".
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    call = ToolCall(id="c1", name="edit_file", arguments="{}")
    reporter.tool_end(call, ToolResult(ok=True, output="ok", summary="edit app.py (+2 -1)"))
    reporter.tool_end(
        ToolCall(id="c2", name="run_command", arguments="{}"),
        ToolResult(ok=False, output="boom", summary="run pytest → exit 1"),
    )
    out = _out(console)
    assert "⏺ edit_file" in out
    assert "⏺ run_command" in out
    assert out.count("⎿") == 2
    # the summary sits on the ⎿ line, not glued to the tool name
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    assert any(l.startswith("⎿") and "edit app.py (+2 -1)" in l for l in lines)


def test_rich_reporter_tool_error_shape():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    reporter.tool_end(ToolCall(id="c1", name="read_file", arguments="{}"), None)
    out = _out(console)
    assert "⏺ read_file" in out and "error" in out


def test_rich_reporter_shows_tool_error_detail():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    reporter.tool_end(
        ToolCall(id="c1", name="read_file", arguments="{}"),
        ToolResult(
            ok=False,
            output=(
                "Path 'src/index.html' does not exist.\n\n"
                "Existing likely paths: belajar-mandarin/index.html."
            ),
            summary="read src/index.html (refused)",
        ),
    )

    out = _out(console)

    assert "read src/index.html (refused)" in out
    assert "belajar-mandarin/index.html" in out


def test_assistant_blocks_get_one_bullet_each():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    reporter.assistant_token("Hello ")
    reporter.assistant_token("world")
    reporter.assistant_end()
    reporter.assistant_token("Again")
    reporter.assistant_end()
    out = _out(console)
    assert "⏺ Hello world" in out
    assert "⏺ Again" in out
    assert out.count("⏺") == 2


def test_reporter_no_spinner_frames_on_non_terminal():
    # StringIO consoles (tests, pipes) must get plain output only — the
    # working spinner is strictly terminal-only.
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    reporter.iteration(1)
    reporter.assistant_token("hi")
    reporter.assistant_end()
    reporter.close()
    out = _out(console)
    assert "working" not in out
    assert "⏺ hi" in out


def test_reporter_close_is_idempotent():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    reporter.close()
    reporter.close()


def test_rich_reporter_activity_lines():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    call = ToolCall(id="c1", name="edit_file", arguments="{}")
    reporter.tool_start(call)
    reporter.tool_end(call, ToolResult(ok=True, output="ok", summary="edit app.py (+2 -1)"))
    reporter.tool_end(
        ToolCall(id="c2", name="run_command", arguments="{}"),
        ToolResult(ok=False, output="boom", summary="run pytest → exit 1"),
    )
    out = _out(console)
    assert "edit app.py (+2 -1)" in out
    assert "run pytest → exit 1" in out
    assert reporter.tools_used == ["edit_file", "run_command"]


def test_rich_reporter_model_and_tool_logs():
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    reporter = RichReporter(console)
    call = ToolCall(id="c1", name="read_file", arguments='{"path":"app.py"}')

    reporter.model_start(1, "fake/model")
    reporter.model_end(1, "fake/model", 1, False, Usage(total_tokens=12))
    reporter.tool_start(call)
    reporter.tool_end(call, ToolResult(ok=True, output="x", summary="read app.py"))

    out = _out(console)
    assert "→ model" in out and "fake/model" in out
    assert "← model" in out and "1 tool call" in out
    assert "→ tool" in out and "read_file" in out and "app.py" in out
    assert "read app.py" in out



def test_rich_reporter_shows_ollama_gpu_hint():
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    reporter = RichReporter(console)

    reporter.model_start(1, "ollama_chat/qwen3:4b")

    out = _out(console)
    assert "Ollama local" in out
    assert "100% GPU" in out


def test_rich_reporter_streams_text():
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    reporter = RichReporter(console)
    reporter.assistant_token("Hello ")
    reporter.assistant_token("world")
    reporter.assistant_end()
    assert "Hello world" in _out(console)


def test_render_task_summary():
    class _R:
        stopped_reason = "done"
        iterations = 3
        tool_calls = 4
        usage = Usage(total_tokens=123, cost_usd=0.0012)
        elapsed = 2.5

    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_task_summary(console, _R(), ["read_file", "read_file", "edit_file"])
    out = _out(console)
    assert "done" in out and "3 steps" in out and "123 tokens" in out
    assert "read_file×2" in out


def test_make_unified_diff_no_trailing_newline():
    diff = make_unified_diff("a", "b", "f.txt")
    assert "-a" in diff and "+b" in diff


class _Result:
    def __init__(self, stopped_reason, final_text):
        self.stopped_reason = stopped_reason
        self.final_text = final_text
        self.iterations = 1
        self.tool_calls = 0
        self.usage = Usage()
        self.elapsed = 0.1


def test_render_task_summary_shows_error_text():
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_task_summary(console, _Result("error", "LLM error: rate limited (429)"))
    out = _out(console)
    assert "LLM rate limit" in out
    assert "/model" in out


def test_render_task_summary_shows_max_iterations_text():
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_task_summary(console, _Result("max_iterations", "Stopped after the maximum of 25 iterations."))
    assert "Stopped after the maximum" in _out(console)


def test_render_task_summary_does_not_repeat_streamed_text():
    # For a normal "done" run the final text was already streamed live;
    # the summary must not print it a second time.
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_task_summary(console, _Result("done", "All finished, everything works."))
    assert "All finished" not in _out(console)


# --- credential preflight (no network) ----------------------------------
# Provider-key fields carry a validation_alias, so init kwargs must use the
# alias name (OPENAI_API_KEY=...) — the snake_case field name is silently
# ignored (extra="ignore"). Explicit None wins over the process env, which
# is what makes these tests hermetic on machines with real keys set.
_PROVIDER_KEY_ALIASES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
)


def _hermetic(**kw) -> Settings:
    for alias in _PROVIDER_KEY_ALIASES:
        kw.setdefault(alias, None)
    # Pin behavior fields too: init kwargs outrank every other source, so
    # these cannot be overridden by anything ambient the autouse fixture
    # may have missed.
    kw.setdefault("relay_enabled", False)
    kw.setdefault("permission_mode", PermissionMode.suggest)
    kw.setdefault("planner_model", None)
    kw.setdefault("coder_model", None)
    kw.setdefault("reviewer_model", None)
    return Settings(_env_file=None, **kw)


def test_hermetic_helper_actually_isolates(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-machine-key")
    assert _hermetic().openai_api_key is None
    assert _hermetic(OPENAI_API_KEY="sk-test").openai_api_key == "sk-test"
    # Non-key fields must not leak in from env or a user config.toml either.
    monkeypatch.setenv("RELAYCLI_RELAY_ENABLED", "true")
    monkeypatch.setenv("RELAYCLI_PERMISSION_MODE", "full-auto")
    cfg = tmp_path / "config.toml"
    cfg.write_text('planner_model = "gpt-4o"\n', encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "toml_file", str(cfg))
    s = _hermetic()
    assert s.relay_enabled is False
    assert s.permission_mode is PermissionMode.suggest
    assert s.planner_model is None


def test_preflight_missing_key_names_env_var():
    from relaycli.llm import LLM

    s = _hermetic(model="gpt-4o-mini")
    problem = LLM(s).preflight()
    assert problem is not None and "OPENAI_API_KEY" in problem


def test_preflight_ok_with_key():
    from relaycli.llm import LLM

    s = _hermetic(model="gpt-4o-mini", OPENAI_API_KEY="sk-test")
    assert LLM(s).preflight() is None


def test_preflight_ollama_needs_no_key():
    from relaycli.llm import LLM

    s = _hermetic(model="ollama_chat/llama3.1")
    assert LLM(s).preflight() is None


def test_preflight_unknown_provider_is_permissive():
    from relaycli.llm import LLM

    s = _hermetic(model="fake/model")
    assert LLM(s).preflight() is None


def test_preflight_settings_covers_relay_role_models():
    from relaycli.llm import preflight_settings

    s = _hermetic(model="ollama_chat/llama3.1", relay_enabled=True,
                  planner_model="gpt-4o-mini")
    problem = preflight_settings(s)
    assert problem is not None and "OPENAI_API_KEY" in problem
    s2 = _hermetic(model="ollama_chat/llama3.1")
    assert preflight_settings(s2) is None


def test_setup_panel_contents():
    from relaycli.render import render_setup_panel

    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    render_setup_panel(
        console,
        "No API key configured for provider 'openai' (model 'gpt-4o-mini'). "
        "Set OPENAI_API_KEY in your environment / .env, or add it to "
        "~/.relaycli/config.toml.",
        {"openai": False, "anthropic": True, "ollama": True},
    )
    out = _out(console)
    assert "export OPENAI_API_KEY=" in out       # exact fix for current model
    assert "ollama" in out                        # keyless alternative offered
    assert "anthropic" in out                     # already-detected key suggested


def test_setup_panel_export_hint_ignores_spoofed_model_id():
    from relaycli.render import render_setup_panel

    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_setup_panel(
        console,
        "No API key configured for provider 'openai' (model 'openai/EVIL_API_KEY'). "
        "Set OPENAI_API_KEY in your environment / .env, or add it to "
        "~/.relaycli/config.toml.",
        {"ollama": True},
    )
    out = _out(console)
    assert "export OPENAI_API_KEY=" in out
    assert "export EVIL_API_KEY=" not in out


def test_one_shot_missing_key_fails_fast(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import relaycli.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module, "get_settings",
        lambda: _hermetic(model="gpt-4o-mini"),
    )
    result = CliRunner().invoke(cli_module.app, ["-p", "explain this repo"])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_one_shot_greeting_uses_local_guide(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import relaycli.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module, "get_settings",
        lambda: _hermetic(model="gpt-4o-mini"),
    )
    result = CliRunner().invoke(cli_module.app, ["-p", "hi"])
    assert result.exit_code == 0
    assert "siap bantu" in result.output
    assert "OPENAI_API_KEY" not in result.output


def test_one_shot_relay_preflights_role_models(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import relaycli.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module, "get_settings",
        lambda: _hermetic(model="ollama_chat/llama3.1", relay_enabled=True,
                          coder_model="gpt-4o-mini"),
    )
    result = CliRunner().invoke(cli_module.app, ["-p", "explain this repo"])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


# --- REPL input dispatch -------------------------------------------------
def _hermetic_repl(**settings_kw):
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    return Repl(_hermetic(**settings_kw), console=console), console


def test_dispatch_flag_input_is_hinted_not_sent(monkeypatch):
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    called = []
    monkeypatch.setattr(repl, "_run_agent", lambda req: called.append(req))
    for flagged in ("-h", "--help", '-p "do a thing"'):
        assert repl._handle_line(flagged) is False
    assert called == []
    assert "/help" in _out(console)


def test_dispatch_help_aliases(monkeypatch):
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    monkeypatch.setattr(
        repl, "_run_agent",
        lambda req: (_ for _ in ()).throw(AssertionError("help must not hit the agent")),
    )
    assert repl._handle_line("help") is False
    assert repl._handle_line("?") is False
    out = _out(console)
    assert "/model" in out and "!<cmd>" in out


def test_dispatch_exit_aliases():
    repl, _ = _hermetic_repl(model="gpt-4o-mini")
    assert repl._handle_line("exit") is True
    assert repl._handle_line("quit") is True
    assert repl._handle_line("EXIT") is True


def test_dispatch_plain_text_goes_to_agent(monkeypatch):
    repl, _ = _hermetic_repl(model="gpt-4o-mini")
    called = []
    monkeypatch.setattr(repl, "_run_agent", lambda req: called.append(req))
    assert repl._handle_line("explain this repo") is False
    assert called == ["explain this repo"]


def test_dispatch_greeting_uses_local_guide(monkeypatch):
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    monkeypatch.setattr(
        repl,
        "_run_agent",
        lambda req: (_ for _ in ()).throw(AssertionError("greeting must stay local")),
    )
    assert repl._handle_line("halo") is False
    assert "siap bantu" in _out(console)


def test_dispatch_bang_runs_shell_not_permission_gated():
    repl, console = _hermetic_repl(model="gpt-4o-mini")

    class _Boom:
        def __getattr__(self, name):
            raise AssertionError("!cmd must not consult the permission manager")

    repl.permissions = _Boom()
    assert repl._handle_line("!echo hi-from-shell") is False
    out = _out(console)
    assert "hi-from-shell" in out
    assert "exit 0" in out


def test_bang_shows_stderr_and_nonzero_exit():
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    repl._handle_line("!echo oops >&2; exit 3")
    out = _out(console)
    assert "oops" in out and "exit 3" in out


def test_bang_empty_shows_usage():
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    repl._handle_line("!")
    assert "usage" in _out(console)


def test_bang_binary_output_does_not_crash_repl():
    # A strict UTF-8 decode of !cmd output would raise UnicodeDecodeError
    # out of the REPL loop, killing the session.
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    assert repl._handle_line("!printf '\\xff\\xfebinary\\n'") is False
    assert "exit 0" in _out(console)


def test_bang_multiline_is_refused(tmp_path):
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    marker = tmp_path / "should-not-exist"
    repl._handle_line(f"!git status\ntouch {marker}")
    out = _out(console)
    assert "Multiline" in out
    assert not marker.exists()


def test_setup_panel_reshown_after_missing_key_error():
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    repl._maybe_setup_hint(_Result(
        "error",
        "LLM error: No API key configured for provider 'openai' (model 'gpt-4o-mini'). "
        "Set OPENAI_API_KEY in your environment / .env, or add it to ~/.relaycli/config.toml.",
    ))
    assert "setup needed" in _out(console)


def test_setup_panel_not_reshown_for_other_errors():
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    repl._maybe_setup_hint(_Result("error", "LLM error: rate limited (429)"))
    repl._maybe_setup_hint(_Result("done", "all good"))
    assert "setup needed" not in _out(console)


# --- welcome banner + prompt ---------------------------------------------
def test_banner_shows_version_cwd_model_mode_and_key_warning():
    from relaycli import __version__

    repl, console = _hermetic_repl(model="gpt-4o-mini")
    repl._print_banner()
    out = _out(console)
    assert __version__ in out
    assert repl.project.root.name in out
    assert "gpt-4o-mini" in out
    assert "suggest" in out
    assert "key missing" in out
    # Preflight failed -> the setup panel appears right away (non-blocking).
    assert "setup needed" in out
    assert "export OPENAI_API_KEY=" in out


def test_banner_keyless_model_no_warning():
    repl, console = _hermetic_repl(model="ollama_chat/llama3.1")
    repl._print_banner()
    out = _out(console)
    assert "no key needed" in out
    assert "setup needed" not in out


def test_banner_key_detected():
    repl, console = _hermetic_repl(model="gpt-4o-mini", OPENAI_API_KEY="sk-test")
    repl._print_banner()
    out = _out(console)
    assert "key detected" in out
    assert "setup needed" not in out


def test_banner_relay_routing_shown():
    repl, console = _hermetic_repl(model="ollama_chat/llama3.1", relay_enabled=True)
    repl._print_banner()
    out = _out(console)
    assert "planner" in out and "coder" in out and "reviewer" in out


def test_banner_long_cwd_is_folded_not_truncated(monkeypatch, tmp_path):
    deep = tmp_path / ("a" * 40) / ("b" * 40) / "project-checkout-dir"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    repl, console = _hermetic_repl(model="ollama_chat/llama3.1")
    repl._print_banner()
    # The cwd cell folds across lines rather than ellipsizing; normalize away
    # panel borders/whitespace so a mid-word fold can't hide the substring.
    flat = re.sub(r"[│\s]+", "", _out(console))
    assert "project-checkout-dir" in flat


def test_prompt_is_minimal_caret():
    # Claude Code-style: the prompt is a bare accent caret; the session
    # status (model · mode · relay) lives in the bottom toolbar instead.
    repl, _ = _hermetic_repl(model="gpt-4o-mini")
    assert repl._prompt_text() == [("class:prompt", "❯ ")]
    # ... and stays static when the session state changes
    repl.settings.relay_enabled = True
    repl.settings.model = "ollama_chat/llama3.1"
    assert repl._prompt_text() == [("class:prompt", "❯ ")]


def test_banner_warns_when_workspace_is_home(tmp_path):
    # Launching from $HOME makes the whole home tree the workspace — the
    # 2026-07-03 incident: the agent wandered into Downloads and ballooned
    # the context. The banner must say so; a real project dir stays quiet.
    from pathlib import Path

    from relaycli.render import render_welcome

    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    render_welcome(console, _hermetic(model="ollama_chat/llama3.1"), Path.home(), "not needed")
    assert "whole home" in _out(console)

    console2 = Console(file=io.StringIO(), force_terminal=False, width=100)
    render_welcome(console2, _hermetic(model="ollama_chat/llama3.1"), tmp_path, "not needed")
    assert "whole home" not in _out(console2)


def test_banner_has_claude_style_welcome():
    repl, console = _hermetic_repl(model="ollama_chat/llama3.1")
    repl._print_banner()
    out = _out(console)
    assert "✻ RelayCLI" in out
    assert "agent workspace" in out


# --- slash-command menu ------------------------------------------------
def _completions(text: str):
    from prompt_toolkit.document import Document

    from relaycli.repl import SlashCompleter

    doc = Document(text, cursor_position=len(text))
    return list(SlashCompleter().get_completions(doc, None))


def test_completer_slash_lists_every_command_with_meta():
    comps = _completions("/")
    texts = [c.text for c in comps]
    for cmd in ("/model", "/mode", "/relay", "/diff", "/clear", "/help", "/exit"):
        assert cmd in texts
    # every entry carries a one-line description for the popup
    assert all(c.display_meta for c in comps)


def test_completer_prefix_filters():
    assert [c.text for c in _completions("/mo")] == ["/model", "/mode"]
    assert [c.text for c in _completions("/rel")] == ["/relay"]
    assert _completions("/zzz") == []


def test_completer_recovers_small_command_typos():
    texts = [c.text for c in _completions("/modle")]
    assert "/model" in texts


def test_completer_replaces_the_whole_token():
    # Accepting /model from "/mo" must replace "/mo", not append to it.
    (model, _mode) = _completions("/mo")
    assert model.start_position == -len("/mo")


def test_completer_mode_arguments():
    assert [c.text for c in _completions("/mode ")] == [
        "suggest", "auto-edit", "full-auto",
    ]
    assert [c.text for c in _completions("/mode a")] == ["auto-edit"]


def test_completer_relay_arguments():
    assert [c.text for c in _completions("/relay ")] == ["on", "off"]
    assert [c.text for c in _completions("/relay o")] == ["on", "off"]


def test_completer_model_arguments_are_curated_ids():
    texts = [c.text for c in _completions("/model ")]
    assert "gpt-4o-mini" in texts
    assert "ollama_chat/qwen3:4b" in texts
    # prefix-filtering works on the argument too
    claude = [c.text for c in _completions("/model claude")]
    assert claude and all(t.startswith("claude") for t in claude)


def test_completer_openrouter_suggestions_are_open_source_only():
    # Per the 2026-07-03 spec: everything suggested behind the openrouter/
    # prefix must be an open-weights model (verified against the live API),
    # and at least one :free variant is offered for keyless-budget users.
    openrouter = [
        c.text for c in _completions("/model ") if c.text.startswith("openrouter/")
    ]
    assert openrouter, "curated list must keep openrouter entries"
    closed = ("openrouter/anthropic/", "openrouter/openai/gpt-4", "openrouter/openai/o")
    assert not [t for t in openrouter if t.startswith(closed)]
    assert any(t.endswith(":free") for t in openrouter)
    assert "openrouter/qwen/qwen3-coder:free" in openrouter


def test_completer_only_first_argument_is_completed():
    assert _completions("/mode suggest ") == []
    assert _completions("/diff ") == []  # no arguments to offer


def test_completer_plain_text_and_multiline_yield_nothing():
    assert _completions("explain this repo") == []
    assert _completions("") == []
    assert _completions("hello /model") == []
    # a pasted multiline buffer must never pop the menu
    assert _completions("/model gpt-4o\nsecond line") == []


def test_enter_applies_highlighted_completion_else_submits():
    from types import SimpleNamespace

    calls = []
    completion = object()
    buf_menu_open = SimpleNamespace(
        complete_state=SimpleNamespace(current_completion=completion),
        apply_completion=lambda c: calls.append(("apply", c)),
        validate_and_handle=lambda: calls.append(("submit", None)),
    )
    Repl._submit_or_complete(buf_menu_open)
    assert calls == [("apply", completion)]

    calls.clear()
    # menu open but nothing highlighted yet -> Enter submits
    buf_no_highlight = SimpleNamespace(
        complete_state=SimpleNamespace(current_completion=None),
        apply_completion=lambda c: calls.append(("apply", c)),
        validate_and_handle=lambda: calls.append(("submit", None)),
    )
    Repl._submit_or_complete(buf_no_highlight)
    assert calls == [("submit", None)]

    calls.clear()
    buf_no_menu = SimpleNamespace(
        complete_state=None,
        apply_completion=lambda c: calls.append(("apply", c)),
        validate_and_handle=lambda: calls.append(("submit", None)),
    )
    Repl._submit_or_complete(buf_no_menu)
    assert calls == [("submit", None)]


def test_toolbar_shows_live_session_status():
    repl, _ = _hermetic_repl(model="gpt-4o-mini")
    assert repl._toolbar() == " model gpt-4o-mini · mode suggest · relay off · /help · / "
    repl.settings.relay_enabled = True
    repl._cmd_mode("full-auto")  # the real channel: updates settings + permissions together
    assert repl._toolbar() == " model gpt-4o-mini · mode full-auto · relay on · /help · / "


def test_toolbar_reflects_enforced_mode_not_a_bare_settings_mutation():
    """The toolbar (and /mode with no argument) must show what THIS session's
    PermissionManager actually enforces, not whatever the shared Settings
    object last held — /desktop mutates settings.permission_mode from a
    different session, and that must never desync the REPL's own display
    from what it's actually enforcing (see _toolbar's docstring)."""
    repl, console = _hermetic_repl(model="gpt-4o-mini")
    assert repl._toolbar() == " model gpt-4o-mini · mode suggest · relay off · /help · / "
    repl.settings.permission_mode = PermissionMode.full_auto  # e.g. the web UI's toggle
    assert repl.permissions.mode is PermissionMode.suggest    # unaffected: still enforced
    assert repl._toolbar() == " model gpt-4o-mini · mode suggest · relay off · /help · / "
    repl._handle_slash("/mode")
    assert "suggest" in _out(console)


# --- instant startup (lazy LiteLLM) ----------------------------------------
def test_startup_does_not_import_litellm():
    # The whole point of the lazy import: banner/preflight/config must not
    # pay the litellm import cost. Fresh interpreter, so sys.modules is clean.
    import subprocess
    import sys

    code = (
        "import sys; import relaycli.cli, relaycli.repl, relaycli.llm; "
        "from relaycli.llm import preflight_settings, key_status; "
        "from relaycli.config import Settings; "
        "s = Settings(_env_file=None, OPENAI_API_KEY=None); "
        "preflight_settings(s); key_status(s); "
        "assert 'litellm' not in sys.modules, 'litellm imported at startup'"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_fast_resolver_matches_managed_providers():
    from relaycli.llm import _resolve_provider

    assert _resolve_provider("gpt-4o-mini") == "openai"
    assert _resolve_provider("o3-mini") == "openai"
    assert _resolve_provider("claude-3-5-sonnet-latest") == "anthropic"
    assert _resolve_provider("gemini-1.5-pro") == "gemini"
    assert _resolve_provider("mistral-large-latest") == "mistral"
    assert _resolve_provider("groq/llama-3.3-70b-versatile") == "groq"
    assert _resolve_provider("openrouter/meta-llama/llama-3-70b") == "openrouter"
    assert _resolve_provider("ollama_chat/llama3.1") == "ollama_chat"
    assert _resolve_provider("some-unknown-model") is None
    assert _resolve_provider("fake/model") is None


def test_preflight_openrouter_prefix_named_var():
    from relaycli.llm import LLM

    s = _hermetic(model="openrouter/meta-llama/llama-3-70b")
    problem = LLM(s).preflight()
    assert problem is not None and "OPENROUTER_API_KEY" in problem


def test_key_status_bare_claude_model():
    from relaycli.llm import key_status

    assert key_status(_hermetic(model="claude-3-5-sonnet-latest")) == "missing"
    assert key_status(_hermetic(model="claude-3-5-sonnet-latest",
                                ANTHROPIC_API_KEY="sk-ant")) == "detected"


def test_repl_startup_announces_mcp_connection_before_blocking(monkeypatch):
    """A configured MCP server can block the constructor for up to ~2
    minutes (cold npx download, hung handshake). The user must see why,
    not stare at a blank terminal."""
    import relaycli.mcp as mcp_mod

    monkeypatch.setattr(
        mcp_mod, "enabled_servers",
        lambda: {"github": object(), "fetch": object()},
    )
    monkeypatch.setattr(mcp_mod, "extend_registry", lambda reg, **kw: reg)
    repl, console = _repl()
    text = _out(console)
    assert "connecting to 2 MCP connector" in text
    assert "github" in text and "fetch" in text


def test_repl_startup_silent_when_no_mcp_servers(monkeypatch):
    import relaycli.mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "enabled_servers", lambda: {})
    repl, console = _repl()
    assert "MCP connector" not in _out(console)


def test_render_model_warning_for_risky_local_model():
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_model_warning(console, Settings(model="ollama_chat/deepseek-coder:6.7b"))
    out = _out(console)
    assert "plain text" in out and "relaycli init" in out


def test_slow_local_model_warning_for_cpu_large_model(monkeypatch):
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(runtime, "_ollama_processor", lambda name, timeout=0.4: "100% CPU")
    monkeypatch.setattr(runtime, "_total_memory_gib", lambda: 32.0)

    warning = runtime.slow_local_model_warning("ollama_chat/qwen3:4b")

    assert warning is not None
    assert "CPU" in warning
    assert "qwen2.5-coder:0.5b" in warning


def test_slow_local_model_warning_for_loaded_small_cpu_model(monkeypatch):
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(runtime, "_ollama_processor", lambda name, timeout=0.4: "100% CPU")

    warning = runtime.slow_local_model_warning("ollama_chat/qwen2.5-coder:0.5b")

    assert warning is not None
    assert "CPU" in warning


def test_slow_local_model_warning_allows_loaded_gpu_model(monkeypatch):
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(runtime, "_ollama_processor", lambda name, timeout=0.4: "100% GPU")
    monkeypatch.setattr(runtime, "_total_memory_gib", lambda: 4.0)

    assert runtime.slow_local_model_warning("ollama_chat/qwen3:4b") is None


def test_slow_local_model_warning_for_mixed_cpu_gpu_model(monkeypatch):
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(runtime, "_ollama_processor", lambda name, timeout=0.4: "8%/92% CPU/GPU")
    monkeypatch.setattr(runtime, "_total_memory_gib", lambda: 32.0)

    warning = runtime.slow_local_model_warning("ollama_chat/qwen3:4b")

    assert warning is not None
    assert "partially offloading" in warning


def test_slow_local_model_warning_requires_verified_full_gpu_for_large_model(monkeypatch):
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(runtime, "_ollama_processor", lambda name, timeout=0.4: None)
    monkeypatch.setattr(runtime, "_ollama_server_prefers_gpu", lambda timeout=0.4: True)
    monkeypatch.setattr(runtime, "_total_memory_gib", lambda: 16.0)

    warning = runtime.slow_local_model_warning("ollama_chat/qwen3:4b")

    assert warning is not None
    assert "100% GPU" in warning


def test_slow_local_model_warning_can_be_overridden(monkeypatch):
    import relaycli.ollama_runtime as runtime

    monkeypatch.setenv("RELAYCLI_ALLOW_SLOW_LOCAL", "1")
    monkeypatch.setattr(runtime, "_ollama_processor", lambda name, timeout=0.4: "100% CPU")

    assert runtime.slow_local_model_warning("ollama_chat/qwen3:4b") is None


def test_recommended_fast_local_model_prefers_small_coder(monkeypatch):
    import relaycli.llm as llm
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(
        llm,
        "ollama_models",
        lambda settings, timeout=0.8: ["qwen3:4b", "qwen2.5-coder:0.5b"],
    )

    assert (
        runtime.recommended_fast_local_model(Settings(model="ollama_chat/qwen3:4b"))
        == "ollama_chat/qwen2.5-coder:0.5b"
    )


def test_recommended_fast_local_model_prefers_larger_small_coder(monkeypatch):
    import relaycli.llm as llm
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(
        llm,
        "ollama_models",
        lambda settings, timeout=0.8: ["qwen2.5-coder:0.5b", "qwen2.5-coder:1.5b"],
    )

    assert (
        runtime.recommended_fast_local_model(Settings(model="ollama_chat/qwen3:4b"))
        == "ollama_chat/qwen2.5-coder:1.5b"
    )


def test_recommended_fast_local_model_ignores_only_large_models(monkeypatch):
    import relaycli.llm as llm
    import relaycli.ollama_runtime as runtime

    monkeypatch.setattr(llm, "ollama_models", lambda settings, timeout=0.8: ["qwen3:4b"])

    assert runtime.recommended_fast_local_model(Settings(model="ollama_chat/qwen3:4b")) is None
