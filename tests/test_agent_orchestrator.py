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

    def __init__(self, text: str, *, stopped_reason: str = "done"):
        self._text = text
        self._stopped_reason = stopped_reason
        self.requests: list[str] = []

    def run(self, request: str) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            final_text=self._text, iterations=1, tool_calls=0,
            usage=Usage(total_tokens=10), stopped_reason=self._stopped_reason,
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


def test_build_task_graph_reports_the_real_error_when_the_call_itself_failed():
    """Regression: found by actually running --experimental-parallel
    end to end (Ollama unreachable there) — before this check,
    build_task_graph fed an LLM-error string straight into the JSON
    parser and produced a misleading "task #0 is not an object" instead
    of the real cause."""
    agent = _FakeOrchestratorAgent(
        "LLM error: Model call failed for 'ollama_chat/x' (APIConnectionError): "
        "Ollama isn't reachable at localhost:11434 — start it with: ollama serve",
        stopped_reason="error",
    )
    with pytest.raises(GraphError, match="Ollama isn't reachable"):
        build_task_graph("do something", orchestrator_agent=agent)


def test_build_task_graph_reports_max_iterations_too():
    agent = _FakeOrchestratorAgent("(ran out of iterations)", stopped_reason="max_iterations")
    with pytest.raises(GraphError):
        build_task_graph("do something", orchestrator_agent=agent)


def test_orchestrator_task_list_instructions_survive_a_real_prompt_template_format():
    """Regression: ORCHESTRATOR_TASK_LIST_INSTRUCTIONS is concatenated
    onto a role template and passed to Agent.__init__, which
    unconditionally runs the combined string through
    str.format(cwd=..., mode=..., mode_desc=..., tool_list=...) — found
    by actually running --experimental-parallel end to end and hitting
    `KeyError: '"tasks"'` from the instructions' own literal JSON
    example braces. Every role template must survive this exact call."""
    from relaycli.agent.orchestrator import ORCHESTRATOR_TASK_LIST_INSTRUCTIONS
    from relaycli.core.roster import roster_template

    template = roster_template("orchestrator") + ORCHESTRATOR_TASK_LIST_INSTRUCTIONS
    formatted = template.format(cwd="/tmp/project", mode="full-auto", mode_desc="desc", tool_list="- t: d")

    # The escaping must be transparent: the model-visible text has single
    # braces, not the doubled {{ }} needed only for str.format's own sake.
    assert '{"tasks":' in formatted
    assert '{{' not in formatted


# --- make_run_task -------------------------------------------------------
class _FakeCtx:
    def __init__(self, read_files):
        self.read_files = read_files


class _FakeTaskAgent:
    """Mirrors agent/loop.py's real `Agent.run(request, *, reporter=None)`.
    The reporter keyword is not decoration: make_run_task always passes it,
    and a fake that omitted it hid that fact until the real signature was
    exercised."""

    def __init__(self, result: AgentResult):
        self._result = result
        self.goals: list[str] = []
        self.reporters: list[object] = []

    def run(self, goal: str, *, reporter=None) -> AgentResult:
        self.goals.append(goal)
        self.reporters.append(reporter)
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


# --- reporter_factory: how anything learns what a task actually did ---------
def test_make_run_task_gives_each_task_agent_a_reporter():
    """Without this a parallel run is silent past its status transitions —
    no model steps, no tool calls, and no structured diffs, which is what a
    review surface needs."""
    agent = _FakeTaskAgent(AgentResult(
        final_text="ok", iterations=1, tool_calls=0,
        usage=Usage(total_tokens=5), stopped_reason="done"))
    made = []

    def factory(role_id, task_id):
        return agent, _FakeCtx(set())

    def reporter_factory(task_id, role_id):
        made.append((task_id, role_id))
        return object()

    run_task = make_run_task(factory, reporter_factory)
    asyncio.run(run_task(Task(id="t1", role_id="backend", goal="do it")))

    assert made == [("t1", "backend")], "reporter must be built per task, with its id and role"
    assert agent.reporters and agent.reporters[0] is not None


def test_make_run_task_closes_the_reporter_even_when_the_agent_raises():
    """A Reporter buffers the assistant's last block until close(); leaking
    one loses that text, and a raising agent is exactly when you want it."""
    closed = []

    class _Reporter:
        def close(self):
            closed.append(True)

    class _Boom:
        def run(self, goal, *, reporter=None):
            raise RuntimeError("agent exploded")

    run_task = make_run_task(
        lambda role_id, task_id: (_Boom(), _FakeCtx(set())),
        lambda task_id, role_id: _Reporter(),
    )
    with pytest.raises(RuntimeError, match="exploded"):
        asyncio.run(run_task(Task(id="t1", role_id="backend", goal="x")))
    assert closed == [True]


def test_make_run_task_without_a_reporter_factory_still_works():
    """The terminal path passes none; a task agent must not require one."""
    agent = _FakeTaskAgent(AgentResult(
        final_text="ok", iterations=1, tool_calls=0,
        usage=Usage(total_tokens=5), stopped_reason="done"))
    run_task = make_run_task(lambda role_id, task_id: (agent, _FakeCtx(set())))
    outcome = asyncio.run(run_task(Task(id="t1", role_id="backend", goal="x")))
    assert outcome.ok is True
    assert agent.reporters == [None]
