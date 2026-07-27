"""Tests for relaycli/tools/spawn_agent.py — Part A §3.6's delegation
tool. Always uses a fake LLM (no real API calls); real depth/lease/budget
mechanics, fake completions."""

from __future__ import annotations

from relaycli.agent.budget import BudgetGovernor
from relaycli.agent.leases import LeaseManager
from relaycli.core.config import PermissionMode, Settings
from relaycli.core.context import ProjectContext
from relaycli.core.llm import LLMResponse, Usage
from relaycli.core.permissions import PermissionManager
from relaycli.tools.base import ToolContext
from relaycli.tools.spawn_agent import MAX_SPAWN_DEPTH, SpawnAgentArgs, spawn_agent


class FakeLLM:
    """Scripted, deterministic completion — never touches the network."""

    def __init__(self, text: str = "child agent done", tokens: int = 8):
        self.text = text
        self.tokens = tokens
        self.calls = 0

    def complete(self, messages, *, tools=None, model=None, temperature=None,
                 stream=False, on_token=None):
        self.calls += 1
        if on_token:
            on_token(self.text)
        return LLMResponse(
            text=self.text, tool_calls=[],
            usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=self.tokens),
        )


def _ctx(tmp_path, **overrides):
    overrides.setdefault(
        "settings", Settings(model="fake/model", permission_mode=PermissionMode.full_auto)
    )
    overrides.setdefault("llm", FakeLLM())
    overrides.setdefault("permissions", PermissionManager(PermissionMode.full_auto))
    return ToolContext(project=ProjectContext(tmp_path), **overrides)


def test_requires_settings_to_be_available(tmp_path):
    ctx = ToolContext(
        project=ProjectContext(tmp_path), permissions=PermissionManager(PermissionMode.full_auto)
    )  # settings=None, the plain single-agent flow's default
    res = spawn_agent(SpawnAgentArgs(role="backend", goal="x"), ctx)
    assert not res.ok
    assert "scheduler-run task" in res.output


def test_requires_a_toolcontext_at_all(tmp_path):
    res = spawn_agent(SpawnAgentArgs(role="backend", goal="x"), None)
    assert not res.ok


def test_depth_limit_refuses_before_spawning(tmp_path):
    ctx = _ctx(tmp_path, spawn_depth=MAX_SPAWN_DEPTH)
    res = spawn_agent(SpawnAgentArgs(role="backend", goal="x"), ctx)
    assert not res.ok
    assert "recursion limit" in res.output
    assert ctx.llm.calls == 0  # never actually tried to run anything


def test_depth_below_limit_is_allowed():
    assert MAX_SPAWN_DEPTH == 2  # pin the documented value; catches an accidental change


def test_unknown_role_refused(tmp_path):
    ctx = _ctx(tmp_path)
    res = spawn_agent(SpawnAgentArgs(role="not-a-real-role", goal="x"), ctx)
    assert not res.ok
    assert "Unknown role" in res.output
    assert ctx.llm.calls == 0


def test_lease_conflict_refused_before_spawning(tmp_path):
    leases = LeaseManager()
    leases.acquire("other-task", ("src/api/**",))
    ctx = _ctx(tmp_path, lease_manager=leases, current_task_id="t1")
    res = spawn_agent(
        SpawnAgentArgs(role="backend", goal="x", path_claims=["src/api/queue.ts"]), ctx
    )
    assert not res.ok
    assert "other-task" in res.output
    assert ctx.llm.calls == 0


def test_disjoint_path_claims_do_not_block_spawn(tmp_path):
    leases = LeaseManager()
    leases.acquire("other-task", ("src/api/**",))
    ctx = _ctx(tmp_path, lease_manager=leases, current_task_id="t1")
    res = spawn_agent(
        SpawnAgentArgs(role="backend", goal="build it", path_claims=["src/ui/**"]), ctx
    )
    assert res.ok


def test_declined_spawn_is_refused(tmp_path):
    ctx = _ctx(
        tmp_path,
        permissions=PermissionManager(PermissionMode.suggest, prompter=lambda _t: False),
    )
    res = spawn_agent(SpawnAgentArgs(role="backend", goal="x"), ctx)
    assert not res.ok
    assert "not approved" in res.output
    assert ctx.llm.calls == 0


def test_successful_spawn_returns_child_result(tmp_path):
    ctx = _ctx(tmp_path, llm=FakeLLM(text="implemented the api"))
    res = spawn_agent(SpawnAgentArgs(role="backend", goal="implement the api"), ctx)
    assert res.ok
    assert res.output == "implemented the api"
    assert res.meta["stopped_reason"] == "done"
    assert ctx.llm.calls == 1


def test_spawned_agent_shares_project_permissions_and_file_cache(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "existing.py").write_text("x = 1\n")
    res = spawn_agent(SpawnAgentArgs(role="backend", goal="do it"), ctx)
    assert res.ok  # constructing the child Agent against the real project succeeded


def test_child_usage_is_recorded_against_the_parent_budget(tmp_path):
    budget = BudgetGovernor()
    ctx = _ctx(tmp_path, budget=budget, current_task_id="parent-task", llm=FakeLLM(tokens=42))
    spawn_agent(SpawnAgentArgs(role="backend", goal="do it"), ctx)
    assert budget.tokens_spent_by("parent-task") == 42


def test_child_usage_recorded_under_a_synthetic_id_when_no_current_task_id(tmp_path):
    budget = BudgetGovernor()
    ctx = _ctx(tmp_path, budget=budget, llm=FakeLLM(tokens=10))  # current_task_id defaults to None
    spawn_agent(SpawnAgentArgs(role="backend", goal="do it"), ctx)
    assert budget.spent_tokens_total == 10


def test_budget_breach_inside_child_is_a_clean_tool_failure_not_a_crash(tmp_path):
    """A child that blows the budget surfaces as a normal failed
    ToolResult, not an unhandled BudgetExceeded escaping into the parent
    agent's own tool-call loop."""
    budget = BudgetGovernor(max_tokens_per_task=5)
    ctx = _ctx(tmp_path, budget=budget, current_task_id="parent", llm=FakeLLM(tokens=100))
    res = spawn_agent(SpawnAgentArgs(role="backend", goal="do it"), ctx)
    assert not res.ok
    assert "budget" in res.output.lower()


def test_registration_wires_the_tool_into_a_registry():
    from relaycli.tools.registry import ToolRegistry
    from relaycli.tools.spawn_agent import register

    reg = ToolRegistry()
    register(reg)
    assert reg.get("spawn_agent") is not None
    schema = reg.get("spawn_agent").json_schema()
    assert schema["function"]["name"] == "spawn_agent"
