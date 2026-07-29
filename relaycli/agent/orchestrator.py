"""Entry point for --experimental-parallel.

The Orchestrator role decomposes a request into a TaskGraph with one
model call (§11 open question 2: "one model call per session is
affordable; per task is not"); the Scheduler then runs that graph
concurrently. Sequential relay (relay.py's Relay class, gated by
settings.relay_enabled) stays the default and is completely unaffected —
this module is only reached when settings.experimental_parallel is set
(cli.py/relay.py's call site, not this module, makes that choice).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from rich.console import Console

from relaycli.agent.budget import BudgetGovernor
from relaycli.agent.graph import GraphError, TaskGraph, parse_task_graph
from relaycli.agent.leases import LeaseManager
from relaycli.agent.loop import Agent
from relaycli.agent.scheduler import Scheduler, SchedulerResult, TaskOutcome
from relaycli.core.config import Settings
from relaycli.core.context import ProjectContext
from relaycli.core.llm import LLM
from relaycli.core.logging import get_logger
from relaycli.core.permissions import PermissionManager
from relaycli.tools.base import ToolContext
from relaycli.tools.capabilities import scheduler_task_registry

_log = get_logger(__name__)

ORCHESTRATOR_TASK_LIST_INSTRUCTIONS = (
    "\n\nRespond with ONLY a JSON task list, no other text: "
    # {{ / }} — not a nested-JSON quirk: this string is concatenated onto
    # a role template and handed to Agent.__init__, which unconditionally
    # runs it through str.format(cwd=..., mode=..., mode_desc=...,
    # tool_list=...). Every literal brace here must be doubled or that
    # call raises KeyError on the JSON example's own keys (found by
    # actually running --experimental-parallel end to end — every
    # existing test avoids a real Agent/LLM construction for this path).
    '{{"tasks": [{{"id": "short-id", "role": "<roster role id>", "goal": '
    '"one imperative sentence", "depends_on": ["other-task-id", ...], '
    '"path_claims": ["glob/or/path", ...]}}]}}. '
    "Keep each task focused enough for one agent. Use depends_on only "
    "for genuine ordering requirements — independent tasks should have "
    "no depends_on, so they can run concurrently."
)


def build_task_graph(request: str, *, orchestrator_agent: Agent) -> TaskGraph:
    """Run the Orchestrator role once and parse its output into a
    TaskGraph. Raises GraphError (from parse_task_graph, or directly here)
    if the result isn't a valid graph — the caller decides how to surface
    that (e.g. falling back to the sequential pipeline).

    Checking stopped_reason before parsing matters: found by actually
    running --experimental-parallel end to end (Ollama unreachable in
    that environment) and getting "task #0 is not an object" — a call
    that failed outright leaves final_text holding a human-readable error
    string ("LLM error: Model call failed for ..."), not JSON, and
    without this check that string gets fed straight into the JSON
    parser, which fails in a way that hides the real cause instead of
    reporting it.
    """
    from relaycli.core.roles import BUILTIN_ROLES_BY_ID

    plan_result = orchestrator_agent.run(request)
    if plan_result.stopped_reason != "done":
        raise GraphError(f"orchestrator's own model call did not complete: {plan_result.final_text}")
    return parse_task_graph(plan_result.final_text, valid_role_ids=frozenset(BUILTIN_ROLES_BY_ID))


@dataclass
class TaskAgentFactory:
    """Builds the Agent (and its ToolContext) for one scheduled task.
    Production default constructs a real roster specialist Agent; tests
    substitute a fake via Scheduler's own run_task injection instead of
    subclassing this — see test_agent_orchestrator.py."""

    settings: Settings
    console: Console
    project: ProjectContext
    permissions: PermissionManager
    llm: LLM
    leases: LeaseManager
    budget: BudgetGovernor

    def __call__(self, role_id: str, task_id: str):
        from relaycli.appconfig import load_app_config
        from relaycli.core.roster import specialist_runtime

        cfg = load_app_config()
        runtime = specialist_runtime(self.settings, cfg, role_id)
        registry = scheduler_task_registry(role_id)
        ctx = ToolContext(
            project=self.project, permissions=self.permissions, console=self.console,
            lease_manager=self.leases, current_task_id=task_id,
            settings=self.settings, llm=self.llm, budget=self.budget,
        )
        agent = Agent(
            self.settings, console=self.console, project=self.project,
            permissions=self.permissions, llm=self.llm,
            prompt_template=runtime.template, model=runtime.model, registry=registry,
            roster_role_id=role_id,
        )
        agent.tool_ctx = ctx
        return agent, ctx


def make_run_task(agent_factory, reporter_factory=None):
    """A Scheduler-compatible run_task callable, closing over agent_factory
    (production TaskAgentFactory, or a fake for tests) — kept as its own
    function so it's unit-testable independent of Scheduler.run()'s own
    concurrency machinery.

    `reporter_factory(task_id, role_id) -> Reporter` gives each task's agent
    somewhere to report model steps, tool calls and their diffs. Without one
    a parallel run is silent past its task-status transitions: nothing else
    ever sees which files an agent touched, which is also what a diff review
    surface needs. Closed in a finally, since a Reporter typically buffers
    the assistant's last block until told the run is over.
    """

    async def run_task(task) -> TaskOutcome:
        def _run_sync() -> TaskOutcome:
            agent, ctx = agent_factory(task.role_id, task.id)
            reporter = reporter_factory(task.id, task.role_id) if reporter_factory else None
            try:
                result = agent.run(task.goal, reporter=reporter)
            finally:
                if reporter is not None and hasattr(reporter, "close"):
                    reporter.close()
            return TaskOutcome(
                task_id=task.id,
                ok=(result.stopped_reason == "done"),
                summary=result.final_text[:200],
                usage=result.usage,
                error=None if result.stopped_reason == "done" else result.final_text,
                refs=tuple(sorted(ctx.read_files)),
            )
        return await asyncio.to_thread(_run_sync)

    return run_task


async def run_parallel(
    settings: Settings,
    request: str,
    *,
    console: Console | None = None,
    project: ProjectContext | None = None,
    permissions: PermissionManager | None = None,
    llm: LLM | None = None,
    on_tick: Callable[[], None] | None = None,
    on_scheduler_ready: Callable[[Scheduler], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    reporter_factory: Callable[[str, str], object] | None = None,
) -> SchedulerResult:
    """Decompose `request` via the Orchestrator role, then run the
    resulting graph concurrently. The single real entry point
    --experimental-parallel uses; everything it calls is independently
    unit-tested (build_task_graph via parse_task_graph's own tests,
    make_run_task via test_agent_orchestrator.py with a fake factory,
    Scheduler via test_agent_scheduler.py) since a live multi-model run
    isn't something this session can exercise end-to-end.

    `on_tick`, if given, is forwarded straight to Scheduler (see its
    docstring) — a live view (ui/live.py) uses it to redraw from
    `scheduler.graph`/`.budget`/`.leases` as the run progresses, without
    run_parallel needing to know anything about rendering.

    `on_scheduler_ready`, if given, fires once, synchronously, right after
    the Scheduler is constructed but before `.run()` starts — the only way
    a caller can get a direct reference to it at all, since run_parallel
    otherwise only returns once everything is finished.

    `should_stop` is polled by the Scheduler between rounds (see its own
    docstring for the cadence) and halts the run early when it returns
    true — how the live view's `esc` binding stops every agent.

    `reporter_factory(task_id, role_id)` gives each task's agent a Reporter,
    which is the only way anything downstream learns what a task actually
    did — model steps, tool calls, and the structured diffs those tools
    return. Omit it and a parallel run reports task statuses and nothing
    else.
    """
    from relaycli.appconfig import load_app_config
    from relaycli.core.roster import specialist_runtime

    console = console or Console()
    project = project or ProjectContext(".")
    permissions = permissions or PermissionManager(settings.permission_mode, console=console)
    llm = llm or LLM(settings)
    cfg = load_app_config()

    orchestrator_runtime = specialist_runtime(settings, cfg, "orchestrator")
    orchestrator_agent = Agent(
        settings, console=console, project=project, permissions=permissions, llm=llm,
        prompt_template=orchestrator_runtime.template + ORCHESTRATOR_TASK_LIST_INSTRUCTIONS,
        model=orchestrator_runtime.model, pass_tool_schemas=False,
    )
    graph = build_task_graph(request, orchestrator_agent=orchestrator_agent)
    _log.info("orchestrator produced %d tasks", len(graph.tasks))

    leases = LeaseManager()
    budget = BudgetGovernor(max_tokens_total=settings.token_budget or None)
    factory = TaskAgentFactory(
        settings=settings, console=console, project=project, permissions=permissions,
        llm=llm, leases=leases, budget=budget,
    )
    scheduler = Scheduler(
        graph, make_run_task(factory, reporter_factory),
        max_concurrent_agents=settings.max_concurrent_agents,
        leases=leases, budget=budget, on_tick=on_tick, should_stop=should_stop,
    )
    if on_scheduler_ready is not None:
        on_scheduler_ready(scheduler)
    return await scheduler.run()
