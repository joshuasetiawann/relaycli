"""Tests for relaycli/agent/orchestrator.py — the --experimental-parallel
entry point. A live end-to-end run (real Orchestrator model call, real
scheduled specialist agents) isn't something this session can exercise —
no real LLM anywhere here — so these test the two independently
injectable pieces (build_task_graph's parsing, make_run_task's outcome
construction) against fakes, on top of the already-independently-tested
parse_task_graph (test_agent_graph.py) and Scheduler
(test_agent_scheduler.py) they're built from."""

from __future__ import annotations

import asyncio

import pytest

from relaycli.agent.graph import GraphError, Task
from relaycli.agent.loop import AgentResult
from relaycli.agent.orchestrator import build_task_graph, make_run_task
from relaycli.core.llm import Usage


class _FakeOrchestratorAgent:
    """Stands in for agent/loop.py's Agent — .run(request) is the only
    method build_task_graph calls."""

    def __init__(self, text: str):
        self._text = text
        self.requests: list[str] = []

    def run(self, request: str) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            final_text=self._text, iterations=1, tool_calls=0,
            usage=Usage(total_tokens=10), stopped_reason="done",
        )


# --- build_task_graph --------------------------------------------------
def test_build_task_graph_parses_a_valid_plan():
    agent = _FakeOrchestratorAgent(
        '{"tasks": [{"id": "t1", "role": "backend", "goal": "build the api"}]}'
    )
    graph = build_task_graph("build something", orchestrator_agent=agent)
    assert "t1" in graph.tasks
    assert graph.tasks["t1"].goal == "build the api"
    assert agent.requests == ["build something"]


def test_build_task_graph_parses_multi_task_plan_with_dependencies():
    agent = _FakeOrchestratorAgent(
        '{"tasks": ['
        '{"id": "backend-task", "role": "backend", "goal": "build api"},'
        '{"id": "frontend-task", "role": "frontend", "goal": "build ui", '
        '"depends_on": ["backend-task"]}'
        "]}"
    )
    graph = build_task_graph("build a full-stack app", orchestrator_agent=agent)
    assert set(graph.tasks) == {"backend-task", "frontend-task"}
    assert graph.tasks["frontend-task"].depends_on == ("backend-task",)


def test_build_task_graph_raises_graph_error_on_unparseable_output():
    agent = _FakeOrchestratorAgent("Sorry, I don't understand the request.")
    with pytest.raises(GraphError):
        build_task_graph("do something", orchestrator_agent=agent)


def test_build_task_graph_rejects_a_hallucinated_role():
    agent = _FakeOrchestratorAgent(
        '{"tasks": [{"id": "t1", "role": "definitely-not-a-real-role", "goal": "x"}]}'
    )
    with pytest.raises(GraphError, match="unknown role"):
        build_task_graph("do something", orchestrator_agent=agent)


def test_build_task_graph_accepts_every_real_builtin_role():
    from relaycli.core.roles import BUILTIN_ROLES

    for role in BUILTIN_ROLES:
        agent = _FakeOrchestratorAgent(
            f'{{"tasks": [{{"id": "t1", "role": "{role.id}", "goal": "x"}}]}}'
        )
        graph = build_task_graph("x", orchestrator_agent=agent)
        assert graph.tasks["t1"].role_id == role.id


# --- make_run_task -------------------------------------------------------
class _FakeCtx:
    def __init__(self, read_files):
        self.read_files = read_files


class _FakeTaskAgent:
    def __init__(self, result: AgentResult):
        self._result = result
        self.goals: list[str] = []

    def run(self, goal: str) -> AgentResult:
        self.goals.append(goal)
        return self._result


def test_make_run_task_success_produces_ok_outcome():
    result = AgentResult(
        final_text="implemented the endpoint", iterations=2, tool_calls=3,
        usage=Usage(total_tokens=123), stopped_reason="done",
    )
    fake_agent = _FakeTaskAgent(result)
    fake_ctx = _FakeCtx(read_files={"src/api.py"})

    def factory(role_id, task_id):
        assert role_id == "backend"
        assert task_id == "t1"
        return fake_agent, fake_ctx

    run_task = make_run_task(factory)
    task = Task(id="t1", role_id="backend", goal="implement the endpoint")
    outcome = asyncio.run(run_task(task))

    assert outcome.task_id == "t1"
    assert outcome.ok
    assert outcome.summary == "implemented the endpoint"
    assert outcome.usage.total_tokens == 123
    assert outcome.error is None
    assert outcome.refs == ("src/api.py",)
    assert fake_agent.goals == ["implement the endpoint"]


def test_make_run_task_failure_produces_not_ok_outcome_with_error():
    result = AgentResult(
        final_text="Stopped after the maximum of 8 iterations.", iterations=8, tool_calls=5,
        usage=Usage(total_tokens=500), stopped_reason="max_iterations",
    )
    fake_agent = _FakeTaskAgent(result)
    fake_ctx = _FakeCtx(read_files=set())

    run_task = make_run_task(lambda role_id, task_id: (fake_agent, fake_ctx))
    outcome = asyncio.run(run_task(Task(id="t1", role_id="backend", goal="x")))

    assert not outcome.ok
    assert outcome.error == "Stopped after the maximum of 8 iterations."


def test_make_run_task_summary_is_truncated():
    long_text = "x" * 500
    result = AgentResult(
        final_text=long_text, iterations=1, tool_calls=0,
        usage=Usage(), stopped_reason="done",
    )
    fake_ctx = _FakeCtx(read_files=set())
    run_task = make_run_task(lambda r, t: (_FakeTaskAgent(result), fake_ctx))
    outcome = asyncio.run(run_task(Task(id="t1", role_id="backend", goal="x")))
    assert len(outcome.summary) == 200


def test_make_run_task_refs_are_sorted_for_determinism():
    result = AgentResult(final_text="done", iterations=1, tool_calls=0, usage=Usage(), stopped_reason="done")
    fake_ctx = _FakeCtx(read_files={"z.py", "a.py", "m.py"})
    run_task = make_run_task(lambda r, t: (_FakeTaskAgent(result), fake_ctx))
    outcome = asyncio.run(run_task(Task(id="t1", role_id="backend", goal="x")))
    assert outcome.refs == ("a.py", "m.py", "z.py")
