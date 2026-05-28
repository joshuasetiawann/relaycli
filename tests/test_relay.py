"""Relay layer tests: router, config, and the Planner→Coder→Reviewer pipeline.

The LLM is always scripted/mocked — no network calls.
"""

from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

import relaycli.cli as cli_module
from relaycli.agent import Agent
from relaycli.config import PermissionMode, Settings
from relaycli.context import ProjectContext
from relaycli.llm import LLMError, LLMResponse, Usage
from relaycli.permissions import PermissionManager
from relaycli.relay import Relay, RelayObserver, RelayResult, parse_verdict
from relaycli.render import RelayRichObserver, render_relay_summary
from relaycli.repl import Repl
from relaycli.router import Role, resolve_model, routing_table
from relaycli.tools import default_registry, planner_registry, reviewer_registry


import os

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch, tmp_path):
    """Isolate from the user's env and ~/.relaycli/config.toml.

    These tests assert default values (relay_enabled=False, etc.); a real
    config.toml that enables relay/task-split would otherwise leak in via
    the TOML settings source (_env_file=None does NOT block it).
    """
    for var in list(os.environ):
        if var.startswith("RELAYCLI_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(Settings.model_config, "toml_file", str(tmp_path / "no.toml"))
    # The relay reads the roster from appconfig; keep it hermetic too.
    from relaycli import appconfig
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "roster.toml")


def _settings(**kw) -> Settings:
    # _env_file=None: ignore any local .env so tests are hermetic.
    return Settings(_env_file=None, **kw)


class ScriptedLLM:
    """Scripted fake LLM. Entries are LLMResponse or Exception (raised)."""

    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict]] = []
        self.models: list[str | None] = []

    def complete(self, messages, *, tools=None, model=None, temperature=None,
                 stream=False, on_token=None):
        self.calls.append(list(messages))
        self.models.append(model)
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of scripted responses")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        if on_token and resp.text:
            on_token(resp.text)
        return resp


def _usage() -> Usage:
    return Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8)


def _resp(text="", tool_calls=None) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=tool_calls or [], usage=_usage())


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


class TestRouter:
    def test_roles_in_pipeline_order(self):
        assert [r.value for r in Role] == [
            "explorer", "planner", "coder", "tester", "reviewer",
        ]

    def test_fallback_to_base_model(self):
        s = _settings(model="base/model")
        assert resolve_model(s, Role.planner) == "base/model"
        assert resolve_model(s, Role.coder) == "base/model"
        assert resolve_model(s, Role.reviewer) == "base/model"

    def test_role_overrides_win(self):
        s = _settings(model="base/model", planner_model="cheap/planner",
                      coder_model="strong/coder")
        assert resolve_model(s, Role.planner) == "cheap/planner"
        assert resolve_model(s, Role.coder) == "strong/coder"
        assert resolve_model(s, Role.reviewer) == "base/model"  # fallback

    def test_routing_table(self):
        s = _settings(model="base/model", reviewer_model="cheap/reviewer")
        table = routing_table(s)
        # Optional roles are hidden while disabled.
        assert table == {
            Role.planner: "base/model",
            Role.coder: "base/model",
            Role.reviewer: "cheap/reviewer",
        }

    def test_routing_table_includes_enabled_optional_roles(self):
        s = _settings(model="base/model", relay_explorer=True, relay_tester=True,
                      tester_model="cheap/tester")
        table = routing_table(s)
        assert list(table) == [
            Role.explorer, Role.planner, Role.coder, Role.tester, Role.reviewer,
        ]
        assert table[Role.explorer] == "base/model"   # fallback
        assert table[Role.tester] == "cheap/tester"   # override


class TestRelayConfig:
    def test_defaults(self):
        s = _settings()
        assert s.relay_enabled is False
        assert s.planner_model is None
        assert s.coder_model is None
        assert s.reviewer_model is None
        assert s.max_review_cycles == 2

    def test_env_loading(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # dodge any repo-local .env
        monkeypatch.setenv("RELAYCLI_RELAY_ENABLED", "true")
        monkeypatch.setenv("RELAYCLI_PLANNER_MODEL", "cheap/p")
        monkeypatch.setenv("RELAYCLI_MAX_REVIEW_CYCLES", "5")
        s = Settings()
        assert s.relay_enabled is True
        assert s.planner_model == "cheap/p"
        assert s.max_review_cycles == 5


class TestAgentExtensions:
    def test_prompt_template_override(self, sample_project):
        llm = ScriptedLLM([_resp(text="hi")])
        settings = _settings(model="fake/model", permission_mode=PermissionMode.full_auto)
        console = _console()
        agent = Agent(
            settings, console=console, project=ProjectContext(sample_project),
            permissions=PermissionManager(PermissionMode.full_auto, console=console),
            llm=llm,
            prompt_template="CUSTOM ROLE in {cwd} mode {mode} tools:\n{tool_list}",
        )
        system = agent.session.to_messages()[0]["content"]
        assert system.startswith("CUSTOM ROLE in")
        assert str(sample_project.resolve()) in system
        assert "read_file" in system

    def test_model_override_pins_model(self, sample_project):
        llm = ScriptedLLM([_resp(text="hi")])
        settings = _settings(model="base/model", permission_mode=PermissionMode.full_auto)
        console = _console()
        agent = Agent(
            settings, console=console, project=ProjectContext(sample_project),
            permissions=PermissionManager(PermissionMode.full_auto, console=console),
            llm=llm, model="pinned/model",
        )
        agent.run("hello")
        assert agent.model == "pinned/model"
        assert llm.models == ["pinned/model"]
        settings.model = "changed/model"
        assert agent.model == "pinned/model"  # override wins

    def test_no_override_follows_settings(self, sample_project):
        llm = ScriptedLLM([_resp(text="hi"), _resp(text="again")])
        settings = _settings(model="base/model", permission_mode=PermissionMode.full_auto)
        console = _console()
        agent = Agent(
            settings, console=console, project=ProjectContext(sample_project),
            permissions=PermissionManager(PermissionMode.full_auto, console=console),
            llm=llm,
        )
        agent.run("one")
        settings.model = "switched/model"  # what /model does
        agent.refresh_system_prompt()
        agent.run("two")
        assert llm.models == ["base/model", "switched/model"]
        assert agent.session.model == "switched/model"


def _relay(project_root, llm, **settings_kw) -> Relay:
    console = _console()
    settings = _settings(model="base/model", permission_mode=PermissionMode.full_auto,
                         **settings_kw)
    return Relay(
        settings, console=console, project=ProjectContext(project_root),
        permissions=PermissionManager(PermissionMode.full_auto, console=console),
        llm=llm,
    )


class TestOptionalRoles:
    def test_explorer_and_tester_run_in_pipeline_order(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="THE-BRIEF: app.py is the entry point"),   # explorer
            _resp(text="THE-PLAN"),                                # planner
            _resp(text="THE-REPORT"),                              # coder
            _resp(text="TESTS: pass\nran pytest, all green"),      # tester
            _resp(text="VERDICT: approve"),                        # reviewer
        ])
        relay = _relay(sample_project, llm, relay_explorer=True, relay_tester=True,
                       explorer_model="cheap/e", tester_model="cheap/t")
        result = relay.run("THE-REQUEST")

        assert result.stopped_reason == "done"
        assert [str(r.role) for r in result.role_runs] == [
            "explorer", "planner", "coder", "tester", "reviewer",
        ]
        assert llm.models == ["cheap/e", "base/model", "base/model", "cheap/t", "base/model"]
        # The Explorer's brief reaches the Planner...
        planner_user = llm.calls[1][-1]["content"]
        assert "THE-REQUEST" in planner_user and "THE-BRIEF" in planner_user
        # ...and the Tester's evidence reaches the Reviewer.
        reviewer_user = llm.calls[4][-1]["content"]
        assert "TESTS: pass" in reviewer_user and "THE-REPORT" in reviewer_user
        assert result.notes == []

    def test_disabled_roles_do_not_run(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="THE-PLAN"),
            _resp(text="THE-REPORT"),
            _resp(text="VERDICT: approve"),
        ])
        result = _relay(sample_project, llm).run("r")
        assert [str(r.role) for r in result.role_runs] == ["planner", "coder", "reviewer"]

    def test_explorer_failure_is_advisory(self, sample_project):
        llm = ScriptedLLM([
            LLMError("explorer model unreachable"),   # explorer dies
            _resp(text="THE-PLAN"),
            _resp(text="THE-REPORT"),
            _resp(text="VERDICT: approve"),
        ])
        result = _relay(sample_project, llm, relay_explorer=True).run("r")
        assert result.stopped_reason == "done"
        assert result.verdict == "approve"
        assert any("Explorer failed" in n for n in result.notes)

    def test_tester_failure_is_advisory(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="THE-PLAN"),
            _resp(text="THE-REPORT"),
            LLMError("tester model unreachable"),     # tester dies
            _resp(text="VERDICT: approve"),
        ])
        result = _relay(sample_project, llm, relay_tester=True).run("r")
        assert result.stopped_reason == "done"
        assert any("Tester failed" in n for n in result.notes)
        # Reviewer still ran, without a tester report.
        reviewer_user = llm.calls[-1][-1]["content"]
        assert "Tester report" not in reviewer_user


class TestAgentsCommand:
    def _repl(self) -> Repl:
        settings = _settings(model="fake/m")
        return Repl(settings, console=_console())

    def test_agents_table_lists_all_roles(self):
        repl = self._repl()
        repl._handle_slash("/agents")
        out = repl.console.file.getvalue()
        for role in ("explorer", "planner", "coder", "tester", "reviewer"):
            assert role in out
        assert "●" in out and "○" in out  # backbone on, optional off

    def test_agents_toggle(self):
        repl = self._repl()
        repl._handle_slash("/agents explorer on")
        assert repl.settings.relay_explorer is True
        repl._handle_slash("/agents tester on")
        assert repl.settings.relay_tester is True
        repl._handle_slash("/agents explorer off")
        assert repl.settings.relay_explorer is False

    def test_agents_hints_relay_when_off(self):
        repl = self._repl()
        repl._handle_slash("/agents tester on")
        assert "/relay on" in repl.console.file.getvalue()

    def test_agents_invalid_usage(self):
        repl = self._repl()
        repl._handle_slash("/agents banana on")
        assert "Usage" in repl.console.file.getvalue()
        repl._handle_slash("/agents explorer maybe")
        assert repl.settings.relay_explorer is False


class TestParseVerdict:
    def test_approve(self):
        assert parse_verdict("Looks good.\nVERDICT: approve") == "approve"

    def test_revise_case_insensitive(self):
        assert parse_verdict("verdict: REVISE\n- fix x") == "revise"

    def test_inline_mentions_ignored(self):
        # Only line-anchored VERDICT: lines count as the decision.
        text = "If broken I'd say VERDICT: revise. But all is well.\nVERDICT: approve"
        assert parse_verdict(text) == "approve"

    def test_verdict_first_feedback_quoting_approve_stays_revise(self):
        # The template mandates verdict-first-then-feedback; feedback that
        # mentions the approve token must not flip the decision.
        text = "VERDICT: revise\n1. The test must pass before I can give VERDICT: approve."
        assert parse_verdict(text) == "revise"

    def test_ambiguous_anchored_verdicts_fail_safe(self):
        # Two anchored verdicts: revise wins (a false revise costs one bounded
        # cycle; a false approve silently ends the quality loop).
        assert parse_verdict("VERDICT: approve\nVERDICT: revise") == "revise"

    def test_inline_only_falls_back_safe(self):
        text = "End with 'VERDICT: approve' if correct, or 'VERDICT: revise' if not."
        assert parse_verdict(text) == "revise"

    def test_no_verdict(self):
        assert parse_verdict("great work, ship it") is None
        assert parse_verdict("") is None


class TestRelayHappyPath:
    def test_plan_code_review_approve(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="1. Edit app.py\n2. Run tests"),        # planner
            _resp(text="Done: edited app.py as planned."),      # coder
            _resp(text="Verified.\nVERDICT: approve"),           # reviewer
        ])
        relay = _relay(sample_project, llm,
                       planner_model="cheap/p", reviewer_model="cheap/r")
        result = relay.run("improve app.py")

        assert result.stopped_reason == "done"
        assert result.verdict == "approve"
        assert result.cycles == 0
        assert result.final_text == "Done: edited app.py as planned."
        assert [str(r.role) for r in result.role_runs] == ["planner", "coder", "reviewer"]
        # Router applied per role; coder falls back to the base model.
        assert llm.models == ["cheap/p", "base/model", "cheap/r"]
        assert result.usage.total_tokens == 24  # 3 calls * 8
        assert result.notes == []
        assert result.elapsed > 0

    def test_role_prompts_and_artifacts(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="THE-PLAN"),
            _resp(text="THE-REPORT"),
            _resp(text="VERDICT: approve"),
        ])
        relay = _relay(sample_project, llm)
        relay.run("THE-REQUEST")

        planner_system = llm.calls[0][0]["content"]
        coder_system = llm.calls[1][0]["content"]
        reviewer_system = llm.calls[2][0]["content"]
        assert "Planner" in planner_system and "read_file" in planner_system
        assert "write_file" not in planner_system      # read-only tool list
        assert "Coder" in coder_system and "edit_file" in coder_system
        assert "Reviewer" in reviewer_system and "VERDICT" in reviewer_system
        assert "edit_file" not in reviewer_system      # no write tools offered
        # Untrusted-content boundary present in every role prompt.
        for system in (planner_system, coder_system, reviewer_system):
            assert "UNTRUSTED" in system

        # Handoff artifacts flow as user messages.
        coder_user = llm.calls[1][-1]["content"]
        assert "THE-REQUEST" in coder_user and "THE-PLAN" in coder_user
        reviewer_user = llm.calls[2][-1]["content"]
        assert "THE-REQUEST" in reviewer_user and "THE-PLAN" in reviewer_user
        assert "THE-REPORT" in reviewer_user

    def test_observer_hooks_called(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"), _resp(text="report"), _resp(text="VERDICT: approve"),
        ])
        seen: list[tuple[str, str, int]] = []

        class Spy(RelayObserver):
            def role_start(self, role, model, cycle):
                seen.append((str(role), model, cycle))

        _relay(sample_project, llm).run("req", observer=Spy())
        assert seen == [("planner", "base/model", 0), ("coder", "base/model", 0),
                        ("reviewer", "base/model", 0)]


class TestRelayReflection:
    def test_revise_then_approve(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"),                                  # planner
            _resp(text="v1 report"),                             # coder cycle 0
            _resp(text="VERDICT: revise\n1. fix the name"),      # reviewer cycle 0
            _resp(text="v2 report"),                             # coder cycle 1
            _resp(text="VERDICT: approve"),                      # reviewer cycle 1
        ])
        relay = _relay(sample_project, llm)
        result = relay.run("req")

        assert result.stopped_reason == "done"
        assert result.verdict == "approve"
        assert result.cycles == 1
        assert result.final_text == "v2 report"
        assert [str(r.role) for r in result.role_runs] == [
            "planner", "coder", "reviewer", "coder", "reviewer"]
        # The coder session persists: its second call still contains cycle-0
        # history, and the new user message carries the reviewer feedback.
        second_coder_call = llm.calls[3]
        assert any("v1 report" in (m.get("content") or "") for m in second_coder_call)
        assert "fix the name" in second_coder_call[-1]["content"]

    def test_review_exhausted(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"),
            _resp(text="v1"), _resp(text="VERDICT: revise\n1. x"),
            _resp(text="v2"), _resp(text="VERDICT: revise\n1. still x"),
        ])
        relay = _relay(sample_project, llm, max_review_cycles=1)
        result = relay.run("req")

        assert result.stopped_reason == "review_exhausted"
        assert result.verdict == "revise"
        assert result.cycles == 1
        assert result.final_text == "v2"
        assert any("revision limit" in n for n in result.notes)

    def test_zero_cycles_means_no_retry(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"), _resp(text="v1"),
            _resp(text="VERDICT: revise\n1. x"),
        ])
        relay = _relay(sample_project, llm, max_review_cycles=0)
        result = relay.run("req")
        assert result.stopped_reason == "review_exhausted"
        assert result.cycles == 0
        assert len(result.role_runs) == 3  # no retry happened

    def test_malformed_verdict_treated_as_approve(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"), _resp(text="report"),
            _resp(text="all looks great to me"),  # no VERDICT line
        ])
        result = _relay(sample_project, llm).run("req")
        assert result.stopped_reason == "done"
        assert result.verdict == "approve"
        assert any("VERDICT" in n for n in result.notes)


class TestRelayFailureModes:
    def test_planner_error_aborts_before_coder(self, sample_project):
        llm = ScriptedLLM([LLMError("planner exploded")])
        result = _relay(sample_project, llm).run("req")
        assert result.stopped_reason == "error"
        assert "planner exploded" in result.final_text
        assert len(llm.calls) == 1  # coder never ran
        assert result.elapsed > 0

    def test_empty_plan_aborts(self, sample_project):
        llm = ScriptedLLM([_resp(text="   ")])
        result = _relay(sample_project, llm).run("req")
        assert result.stopped_reason == "error"
        assert "no plan" in result.final_text.lower()
        assert len(llm.calls) == 1

    def test_coder_error_aborts_before_review(self, sample_project):
        llm = ScriptedLLM([_resp(text="plan"), LLMError("coder exploded")])
        result = _relay(sample_project, llm).run("req")
        assert result.stopped_reason == "error"
        assert "coder exploded" in result.final_text
        assert len(llm.calls) == 2  # reviewer never ran

    def test_reviewer_error_keeps_coder_result(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"), _resp(text="the work"), LLMError("reviewer exploded"),
        ])
        result = _relay(sample_project, llm).run("req")
        assert result.stopped_reason == "done"
        assert result.final_text == "the work"
        assert any("unreviewed" in n for n in result.notes)
        assert result.role_runs[-1].result.stopped_reason == "error"

    def test_reviewer_error_after_revise_clears_stale_verdict(self, sample_project):
        # The cycle-0 'revise' applied to superseded work; when the re-review
        # fails, no verdict was ever issued on the work that stands.
        llm = ScriptedLLM([
            _resp(text="plan"),
            _resp(text="v1"), _resp(text="VERDICT: revise\n1. x"),
            _resp(text="v2"), LLMError("reviewer exploded"),
        ])
        result = _relay(sample_project, llm).run("req")
        assert result.stopped_reason == "done"
        assert result.final_text == "v2"
        assert result.verdict is None
        assert any("unreviewed" in n for n in result.notes)

    def test_coder_iteration_cap_propagates(self, sample_project):
        import json as _json

        from relaycli.llm import ToolCall

        run_tool = ToolCall(id="c1", name="run_command",
                            arguments=_json.dumps({"command": "true"}))
        llm = ScriptedLLM([
            _resp(text="plan"),                      # planner (done in 1 iteration)
            _resp(tool_calls=[run_tool]),            # coder keeps calling tools → cap
        ])
        relay = _relay(sample_project, llm, max_iterations=1)
        result = relay.run("req")
        assert result.stopped_reason == "max_iterations"
        assert "Stopped after the maximum" in result.final_text
        assert len(llm.calls) == 2  # reviewer never ran


class TestRelayRendering:
    def test_role_banners_and_summary(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"), _resp(text="report"), _resp(text="VERDICT: approve"),
        ])
        console = _console()
        relay = Relay(
            _settings(model="base/model", permission_mode=PermissionMode.full_auto,
                      planner_model="cheap/p"),
            console=console, project=ProjectContext(sample_project),
            permissions=PermissionManager(PermissionMode.full_auto, console=console),
            llm=llm,
        )
        result = relay.run("req", observer=RelayRichObserver(console))
        render_relay_summary(console, result)
        out = console.file.getvalue()
        assert "planner" in out and "coder" in out and "reviewer" in out
        assert "cheap/p" in out            # routed model shown in the banner
        assert "done" in out
        assert "approve" in out

    def test_summary_shows_notes_and_exhaustion(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="plan"), _resp(text="v1"),
            _resp(text="VERDICT: revise\n1. x"),
        ])
        console = _console()
        relay = Relay(
            _settings(model="base/model", permission_mode=PermissionMode.full_auto,
                      max_review_cycles=0),
            console=console, project=ProjectContext(sample_project),
            permissions=PermissionManager(PermissionMode.full_auto, console=console),
            llm=llm,
        )
        result = relay.run("req", observer=RelayRichObserver(console))
        render_relay_summary(console, result)
        out = console.file.getvalue()
        assert "review_exhausted" in out
        assert "revision limit" in out     # the note is surfaced

    def test_streamed_report_not_reprinted_on_exhaustion(self, sample_project):
        # The coder's report streams live; the summary must not print it again.
        llm = ScriptedLLM([
            _resp(text="plan"), _resp(text="UNIQUE-CODER-REPORT"),
            _resp(text="VERDICT: revise\n1. x"),
        ])
        console = _console()
        relay = Relay(
            _settings(model="base/model", permission_mode=PermissionMode.full_auto,
                      max_review_cycles=0),
            console=console, project=ProjectContext(sample_project),
            permissions=PermissionManager(PermissionMode.full_auto, console=console),
            llm=llm,
        )
        result = relay.run("req", observer=RelayRichObserver(console))
        render_relay_summary(console, result)
        out = console.file.getvalue()
        assert out.count("UNIQUE-CODER-REPORT") == 1

    def test_error_text_still_printed_once(self, sample_project):
        # Constructed (never-streamed) error text must still appear.
        llm = ScriptedLLM([LLMError("planner exploded")])
        console = _console()
        relay = Relay(
            _settings(model="base/model", permission_mode=PermissionMode.full_auto),
            console=console, project=ProjectContext(sample_project),
            permissions=PermissionManager(PermissionMode.full_auto, console=console),
            llm=llm,
        )
        result = relay.run("req", observer=RelayRichObserver(console))
        render_relay_summary(console, result)
        out = console.file.getvalue()
        assert out.count("planner exploded") == 1


class TestCliRelayFlag:
    def test_one_shot_uses_relay_when_flagged(self, sample_project, monkeypatch):
        monkeypatch.chdir(sample_project)
        calls: dict = {}

        class FakeRelay:
            def __init__(self, settings, **kw):
                calls["enabled"] = settings.relay_enabled

            def run(self, request, *, observer=None):
                calls["request"] = request
                return RelayResult(final_text="ok", stopped_reason="done", cycles=0)

        monkeypatch.setattr(cli_module, "get_settings", lambda: _settings(model="fake/m"))
        monkeypatch.setattr("relaycli.relay.Relay", FakeRelay)
        result = CliRunner().invoke(cli_module.app, ["-p", "do it", "--relay", "-y"])
        assert result.exit_code == 0, result.output
        assert calls == {"enabled": True, "request": "do it"}

    def test_one_shot_without_flag_uses_single_agent(self, sample_project, monkeypatch):
        monkeypatch.chdir(sample_project)

        class BoomRelay:
            def __init__(self, *a, **kw):
                raise AssertionError("Relay must not be constructed when off")

        class FakeAgent:
            def __init__(self, *a, **kw):
                pass

            def run(self, request, *, reporter=None):
                from relaycli.agent import AgentResult
                return AgentResult(final_text="ok", iterations=1, tool_calls=0,
                                   usage=Usage(), stopped_reason="done")

        monkeypatch.setattr(cli_module, "get_settings", lambda: _settings(model="fake/m"))
        monkeypatch.setattr("relaycli.relay.Relay", BoomRelay)
        monkeypatch.setattr("relaycli.agent.Agent", FakeAgent)
        result = CliRunner().invoke(cli_module.app, ["-p", "do it", "-y"])
        assert result.exit_code == 0, result.output

    def test_no_relay_flag_overrides_enabled_config(self, sample_project, monkeypatch):
        monkeypatch.chdir(sample_project)

        class BoomRelay:
            def __init__(self, *a, **kw):
                raise AssertionError("Relay must not run with --no-relay")

        class FakeAgent:
            def __init__(self, *a, **kw):
                pass

            def run(self, request, *, reporter=None):
                from relaycli.agent import AgentResult
                return AgentResult(final_text="ok", iterations=1, tool_calls=0,
                                   usage=Usage(), stopped_reason="done")

        monkeypatch.setattr(cli_module, "get_settings",
                            lambda: _settings(model="fake/m", relay_enabled=True))
        monkeypatch.setattr("relaycli.relay.Relay", BoomRelay)
        monkeypatch.setattr("relaycli.agent.Agent", FakeAgent)
        result = CliRunner().invoke(cli_module.app, ["-p", "do it", "--no-relay", "-y"])
        assert result.exit_code == 0, result.output

class TestReplRelayCommand:
    def _repl(self) -> Repl:
        settings = _settings(model="fake/m")
        return Repl(settings, console=_console())

    def test_toggle_on_off(self):
        repl = self._repl()
        assert repl.settings.relay_enabled is False
        repl._handle_slash("/relay on")
        assert repl.settings.relay_enabled is True
        repl._handle_slash("/relay off")
        assert repl.settings.relay_enabled is False

    def test_bare_relay_prints_status(self):
        repl = self._repl()
        repl._handle_slash("/relay")
        out = repl.console.file.getvalue()
        assert "off" in out

    def test_invalid_arg(self):
        repl = self._repl()
        repl._handle_slash("/relay sideways")
        assert repl.settings.relay_enabled is False
        out = repl.console.file.getvalue()
        assert "Usage" in out
        assert "[on|off]" in out  # not swallowed as Rich markup

    def test_routing_banner_escapes_model_markup(self):
        settings = _settings(model="[red]evil[/red]")
        repl = Repl(settings, console=_console())
        repl._handle_slash("/relay on")
        out = repl.console.file.getvalue()
        assert "[red]evil[/red]" in out  # literal, not interpreted markup

    def test_run_dispatches_to_relay_when_enabled(self, monkeypatch):
        repl = self._repl()
        repl.settings.relay_enabled = True
        seen: dict = {}

        class FakeRelay:
            def __init__(self, *a, **kw): ...

            def run(self, request, *, observer=None):
                seen["request"] = request
                return RelayResult(final_text="ok", stopped_reason="done", cycles=0)

        monkeypatch.setattr("relaycli.relay.Relay", FakeRelay)
        repl._run_agent("build the thing")
        assert seen == {"request": "build the thing"}


class TestRegistrySubsets:
    def test_planner_is_read_only(self):
        names = set(planner_registry().names())
        assert names == {"list_dir", "find_files", "read_file", "search"}

    def test_reviewer_reads_and_runs_but_never_writes(self):
        names = set(reviewer_registry().names())
        assert names == {"list_dir", "find_files", "read_file", "search",
                         "run_command", "check_process"}

    def test_subset_schemas_match_default(self):
        # The subset must reuse the same tool definitions, not redefine them.
        default_schemas = {s["function"]["name"]: s for s in default_registry().schemas()}
        for schema in planner_registry().schemas() + reviewer_registry().schemas():
            assert schema == default_schemas[schema["function"]["name"]]


class TestTaskSplit:
    def test_parse_tasks(self):
        from relaycli.relay import _MAX_TASKS, parse_tasks

        plan = "Goal: do X\n1. scaffold jwt utils\n2) protect user routes\n3. auth tests"
        assert parse_tasks(plan) == [
            "scaffold jwt utils", "protect user routes", "auth tests",
        ]
        assert parse_tasks("no numbers here") == []
        many = "\n".join(f"{i}. step {i}" for i in range(1, 12))
        assert len(parse_tasks(many)) == _MAX_TASKS

    def test_one_fresh_coder_per_task(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="Goal\n1. make utils\n2. wire routes"),   # planner
            _resp(text="UTILS-DONE"),                              # coder task 1
            _resp(text="ROUTES-DONE"),                             # coder task 2
            _resp(text="VERDICT: approve"),                        # reviewer
        ])
        relay = _relay(sample_project, llm, relay_split_tasks=True)
        result = relay.run("REQ")

        assert result.stopped_reason == "done"
        assert result.tasks == ["make utils", "wire routes"]
        assert [str(r.role) for r in result.role_runs] == [
            "planner", "coder", "coder", "reviewer",
        ]
        # Task 1's coder sees only its assignment; task 2's sees task 1's report.
        t1_user = llm.calls[1][-1]["content"]
        assert "ONLY task 1 of 2" in t1_user and "make utils" in t1_user
        t2_user = llm.calls[2][-1]["content"]
        assert "ONLY task 2 of 2" in t2_user and "UTILS-DONE" in t2_user
        # Fresh context per task: task 2's history has no task-1 dialogue.
        t2_roles = [m["role"] for m in llm.calls[2]]
        assert t2_roles == ["system", "user"]
        # The reviewer sees the combined report.
        reviewer_user = llm.calls[3][-1]["content"]
        assert "UTILS-DONE" in reviewer_user and "ROUTES-DONE" in reviewer_user
        assert result.final_text.startswith("Task 1")

    def test_unsplittable_plan_falls_back(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="just do the thing, no numbered steps"),
            _resp(text="DONE"),
            _resp(text="VERDICT: approve"),
        ])
        result = _relay(sample_project, llm, relay_split_tasks=True).run("REQ")
        assert result.stopped_reason == "done"
        assert result.tasks == []
        assert any("no numbered tasks" in n for n in result.notes)
        assert [str(r.role) for r in result.role_runs] == ["planner", "coder", "reviewer"]

    def test_revise_uses_single_fixer_with_context(self, sample_project):
        llm = ScriptedLLM([
            _resp(text="1. a\n2. b"),               # planner
            _resp(text="A-DONE"),                    # task 1
            _resp(text="B-DONE"),                    # task 2
            _resp(text="VERDICT: revise\n1. fix X"), # reviewer
            _resp(text="FIXED"),                     # fixer coder (fresh)
            _resp(text="VERDICT: approve"),          # re-review
        ])
        result = _relay(sample_project, llm, relay_split_tasks=True).run("REQ")
        assert result.stopped_reason == "done"
        assert result.cycles == 1
        fixer_user = llm.calls[4][-1]["content"]
        assert "A-DONE" in fixer_user and "B-DONE" in fixer_user
        assert "fix X" in fixer_user
        assert result.final_text == "FIXED"

    def test_agents_tasks_toggle(self):
        settings = _settings(model="fake/m")
        repl = Repl(settings, console=_console())
        repl._handle_slash("/agents tasks on")
        assert repl.settings.relay_split_tasks is True
        repl._handle_slash("/agents tasks off")
        assert repl.settings.relay_split_tasks is False


class TestAgentsSpecialistsDisplay:
    def test_agents_lists_specialists_when_task_split(self, monkeypatch, tmp_path):
        from relaycli import appconfig
        monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "roster.toml")
        settings = _settings(model="fake/m", relay_split_tasks=True)
        repl = Repl(settings, console=_console())
        repl._handle_slash("/agents")
        out = repl.console.file.getvalue()
        assert "specialists (task-split)" in out and "coder" in out


class TestSpecialistRouting:
    def _enable(self, monkeypatch, tmp_path, *roles):
        from relaycli import appconfig
        from relaycli.appconfig import RoleConfig, load_app_config, save_app_config
        monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "roster.toml")
        cfg = load_app_config()
        for r in roles:
            cfg.roles[r] = RoleConfig(enabled=True, model=f"model-for-{r}")
        save_app_config(cfg)

    def test_tagged_tasks_run_their_specialist_role_and_model(self, sample_project, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path, "backend", "frontend")
        llm = ScriptedLLM([
            _resp(text="1. [backend] build the API\n2. [frontend] build the UI"),  # planner
            _resp(text="API-DONE"),      # backend specialist
            _resp(text="UI-DONE"),       # frontend specialist
            _resp(text="VERDICT: approve"),
        ])
        relay = _relay(sample_project, llm, relay_split_tasks=True)
        result = relay.run("make an app")

        assert result.stopped_reason == "done"
        # the specialist roles actually ran, in order, as the task owners
        assert [str(r.role) for r in result.role_runs] == [
            "planner", "backend", "frontend", "reviewer",
        ]
        # each ran with its roster-resolved specialist model
        assert llm.models == ["base/model", "model-for-backend", "model-for-frontend", "base/model"]
        # and with its role-specific system prompt
        backend_system = llm.calls[1][0]["content"]
        assert "Backend" in backend_system and "server" in backend_system.lower()
        frontend_system = llm.calls[2][0]["content"]
        assert "Frontend" in frontend_system

    def test_planner_is_told_the_enabled_specialists(self, sample_project, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path, "security")
        llm = ScriptedLLM([
            _resp(text="1. a\n2. b"),
            _resp(text="A"), _resp(text="B"),
            _resp(text="VERDICT: approve"),
        ])
        _relay(sample_project, llm, relay_split_tasks=True).run("go")
        planner_user = llm.calls[0][-1]["content"]
        assert "specialist" in planner_user.lower() and "security" in planner_user

    def test_disabled_or_unknown_tag_falls_back_to_coder(self, sample_project, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)  # nothing extra enabled
        llm = ScriptedLLM([
            _resp(text="1. [architect] design it\n2. [wizard] cast a spell"),  # both unavailable
            _resp(text="DESIGN"), _resp(text="SPELL"),
            _resp(text="VERDICT: approve"),
        ])
        result = _relay(sample_project, llm, relay_split_tasks=True).run("go")
        # architect is a real role but disabled; wizard is unknown → both Coder
        assert [str(r.role) for r in result.role_runs] == [
            "planner", "coder", "coder", "reviewer",
        ]
        assert any("architect" in n for n in result.notes)
        assert any("wizard" in n for n in result.notes)
