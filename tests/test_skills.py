"""Skills system tests: parsing, discovery precedence, prompt injection, REPL."""

from __future__ import annotations

import io
import os

import pytest
from rich.console import Console

from relaycli.config import PermissionMode, Settings
from relaycli.skills import Skill, discover_skills, parse_skill, skills_prompt_block


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch, tmp_path):
    for var in list(os.environ):
        if var.startswith("RELAYCLI_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(Settings.model_config, "toml_file", str(tmp_path / "no-config.toml"))


# --- parsing -------------------------------------------------------------
def test_parse_skill_header_and_body():
    text = "---\nname: tdd\ndescription: red-green loop\n---\nWrite the test first."
    skill = parse_skill(text, fallback_name="file-stem", source="builtin")
    assert skill.name == "tdd"
    assert skill.description == "red-green loop"
    assert skill.body == "Write the test first."
    assert skill.source == "builtin"


def test_parse_skill_without_header_uses_stem():
    skill = parse_skill("Just instructions.", fallback_name="my-skill", source="user")
    assert skill.name == "my-skill"
    assert skill.description == ""
    assert skill.body == "Just instructions."


def test_parse_skill_unclosed_header_treated_as_body():
    text = "--- not really a header\nbody line"
    skill = parse_skill(text, fallback_name="x", source="user")
    assert "body line" in skill.body


# --- discovery -----------------------------------------------------------
def test_discovery_precedence_project_over_user_over_builtin(monkeypatch, tmp_path):
    import relaycli.skills as skills_mod

    user_dir = tmp_path / "user-skills"
    user_dir.mkdir()
    (user_dir / "ponytail.md").write_text(
        "---\nname: ponytail\ndescription: user override\n---\nuser body",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills_mod, "USER_SKILLS_DIR", user_dir)

    project = tmp_path / "proj"
    (project / ".relaycli" / "skills").mkdir(parents=True)
    (project / ".relaycli" / "skills" / "deploy.md").write_text(
        "---\nname: deploy\ndescription: project skill\n---\nproject body",
        encoding="utf-8",
    )

    found = discover_skills(project)
    assert found["ponytail"].source == "user"          # user beats builtin
    assert found["deploy"].source == "project"         # project skills appear
    assert found["tdd"].source == "builtin"            # builtins still there


def test_builtin_skills_ship_with_package():
    found = discover_skills(None)
    for name in ("ponytail", "tdd", "debug", "brainstorm", "verify", "frontend-taste"):
        assert name in found, f"builtin skill {name} missing"
        assert found[name].description, f"builtin skill {name} lacks a description"
        assert found[name].source == "builtin"


# --- prompt block ---------------------------------------------------------
def test_skills_prompt_block_empty_and_filled():
    assert skills_prompt_block([]) == ""
    block = skills_prompt_block(
        [Skill(name="tdd", description="d", body="Test first.", source="builtin")]
    )
    assert "ACTIVE SKILLS" in block
    assert "## tdd" in block and "Test first." in block


def test_agent_system_prompt_carries_skills_and_survives_braces():
    from relaycli.agent import Agent

    settings = Settings(
        _env_file=None, model="ollama_chat/llama3.1",
        permission_mode=PermissionMode.suggest,
    )
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    agent = Agent(settings, console=console)
    assert "ACTIVE SKILLS" not in agent.session.system_prompt

    # Braces in a skill body must not break the template .format() call.
    agent.set_skills_block(
        skills_prompt_block(
            [Skill(name="x", description="", body="Use {braces} literally.", source="user")]
        )
    )
    prompt = agent.session.system_prompt
    assert "ACTIVE SKILLS" in prompt and "Use {braces} literally." in prompt

    agent.set_skills_block("")
    assert "ACTIVE SKILLS" not in agent.session.system_prompt


def test_relay_applies_skills_to_coder_only():
    from relaycli.relay import CODER_TEMPLATE, PLANNER_TEMPLATE, Relay
    from relaycli.router import Role
    from relaycli.tools import default_registry, planner_registry

    settings = Settings(
        _env_file=None, model="ollama_chat/llama3.1",
        permission_mode=PermissionMode.suggest,
    )
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    relay = Relay(settings, console=console, skills_block="\nACTIVE SKILLS x")
    coder = relay._agent(Role.coder, CODER_TEMPLATE, default_registry())
    planner = relay._agent(Role.planner, PLANNER_TEMPLATE, planner_registry())
    assert "ACTIVE SKILLS" in coder.session.system_prompt
    assert "ACTIVE SKILLS" not in planner.session.system_prompt


# --- REPL commands ---------------------------------------------------------
def _repl():
    from relaycli.repl import Repl

    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    settings = Settings(
        _env_file=None, model="ollama_chat/llama3.1",
        permission_mode=PermissionMode.suggest,
    )
    return Repl(settings, console=console), console


def test_slash_skill_toggles_and_updates_prompt():
    repl, console = _repl()
    repl._handle_slash("/skill tdd")
    assert repl.active_skills == ["tdd"]
    assert "ACTIVE SKILLS" in repl.agent.session.system_prompt
    assert "## tdd" in repl.agent.session.system_prompt

    repl._handle_slash("/skill tdd")  # toggle off
    assert repl.active_skills == []
    assert "ACTIVE SKILLS" not in repl.agent.session.system_prompt


def test_slash_skill_unknown_name():
    repl, console = _repl()
    repl._handle_slash("/skill nope-not-real")
    assert "Unknown skill" in console.file.getvalue()
    assert repl.active_skills == []


def test_slash_skills_lists_builtins_with_source():
    repl, console = _repl()
    repl._handle_slash("/skill tdd")
    repl._handle_slash("/skills")
    out = console.file.getvalue()
    assert "ponytail" in out and "tdd" in out
    assert "builtin" in out
    assert "●" in out and "○" in out  # active vs inactive markers


def test_completer_skill_names_are_dynamic():
    from prompt_toolkit.document import Document

    from relaycli.repl import SlashCompleter

    completer = SlashCompleter(arg_providers={"skill": lambda: ("tdd", "debug")})
    doc = Document("/skill t", cursor_position=len("/skill t"))
    assert [c.text for c in completer.get_completions(doc, None)] == ["tdd"]
    # commands without a provider keep their static completions
    doc2 = Document("/mode s", cursor_position=len("/mode s"))
    assert [c.text for c in completer.get_completions(doc2, None)] == ["suggest"]


# --- auto-activation (triggers) ---------------------------------------------
def test_parse_skill_reads_triggers():
    from relaycli.skills import parse_skill

    skill = parse_skill(
        "---\nname: demo\ndescription: d\ntriggers: Bug, error ,  cek ulang\n---\nbody",
        fallback_name="x", source="builtin",
    )
    assert skill.triggers == ("bug", "error", "cek ulang")


def test_builtin_skills_carry_triggers():
    from relaycli.skills import discover_skills

    skills = discover_skills()
    assert skills["debug"].triggers
    assert "bug" in skills["debug"].triggers


def _mk(name, source="builtin", triggers=()):
    from relaycli.skills import Skill

    return Skill(name=name, description="", body="b", source=source,
                 triggers=tuple(triggers))


def test_auto_match_scores_and_caps():
    from relaycli.skills import auto_match

    skills = {
        "debug": _mk("debug", triggers=("bug", "error", "fix")),
        "tdd": _mk("tdd", triggers=("test",)),
        "taste": _mk("taste", triggers=("ui", "css")),
    }
    got = auto_match(skills, "fix this bug, the test errors out", limit=2)
    assert got[0] == "debug"           # highest score first
    assert len(got) <= 2


def test_auto_match_matches_indonesian_and_phrases():
    from relaycli.skills import auto_match

    skills = {
        "debug": _mk("debug", triggers=("kenapa", "benerin")),
        "verify": _mk("verify", triggers=("cek ulang",)),
    }
    assert auto_match(skills, "kenapa ini? coba kamu benerin dong") == ["debug"]
    assert auto_match(skills, "tolong cek ulang hasilnya") == ["verify"]


def test_auto_match_prefix_needs_4_chars():
    from relaycli.skills import auto_match

    skills = {"taste": _mk("taste", triggers=("ui",))}
    # "ui" must match only as a whole token, never as a prefix of e.g. "uint8"
    assert auto_match(skills, "convert to uint8") == []
    assert auto_match(skills, "polish the ui") == ["taste"]


def test_auto_match_never_activates_project_skills():
    from relaycli.skills import auto_match

    skills = {"evil": _mk("evil", source="project", triggers=("bug", "fix", "test"))}
    assert auto_match(skills, "fix this bug in the test") == []


def test_auto_match_skips_active_and_triggerless():
    from relaycli.skills import auto_match

    skills = {
        "debug": _mk("debug", triggers=("bug",)),
        "plain": _mk("plain", triggers=()),
    }
    assert auto_match(skills, "a bug", active=("debug",)) == []


def test_settings_skills_auto_defaults_on():
    from relaycli.config import Settings

    assert Settings().skills_auto is True
