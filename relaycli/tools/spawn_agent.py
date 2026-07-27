"""spawn_agent tool — lets an agent delegate a sub-task to a fresh
specialist agent (Part A §3.6). Recursion depth capped at 2; a spawned
child's usage counts against the parent's budget, not a fresh one, so a
delegation chain can't be used to bypass the session budget governor.

Synchronous and tree-recursive by design: spawn_agent runs the child to
completion and returns its result as this tool's output, rather than
enqueuing a new node into a live TaskGraph (which would need the
scheduler itself to be reentrant mid-run — a bigger change than "delegate
one sub-task and get an answer back" needs). A task graph built by the
Orchestrator role is still the primary way multiple agents run
concurrently (agent/scheduler.py); this is for the narrower case of one
already-running agent needing focused help mid-task.

Only usable inside a scheduler-run task: needs ctx.settings (to build the
child's Agent) which only the scheduler sets — the plain single-agent
CLI/REPL flow's ToolContext leaves it None, and this tool refuses
cleanly rather than guessing a settings object to fall back to.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from relaycli.core.logging import get_logger
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry

_log = get_logger(__name__)

MAX_SPAWN_DEPTH = 2


class SpawnAgentArgs(BaseModel):
    role: str = Field(description="Roster role id to run as, e.g. 'backend', 'tester'.")
    goal: str = Field(description="One focused, self-contained task for the sub-agent.")
    path_claims: list[str] = Field(
        default_factory=list,
        description="Paths/globs this sub-task expects to touch (checked against any "
                     "active lease_manager before spawning).",
    )


def spawn_agent(args: SpawnAgentArgs, ctx: ToolContext | None) -> ToolResult:
    if ctx is None or ctx.settings is None:
        return ToolResult.error(
            "spawn_agent is only available inside a scheduler-run task.",
            summary="spawn_agent (unavailable)",
        )

    if ctx.spawn_depth >= MAX_SPAWN_DEPTH:
        return ToolResult.error(
            f"spawn_agent recursion limit reached ({MAX_SPAWN_DEPTH}) — "
            f"a sub-agent cannot itself spawn another sub-agent at this depth.",
            summary="spawn_agent (depth limit)",
        )

    from relaycli.core.roles import builtin_role
    role = builtin_role(args.role)
    if role is None:
        return ToolResult.error(f"Unknown role '{args.role}'.", summary="spawn_agent (unknown role)")

    if ctx.lease_manager is not None and args.path_claims:
        conflict = ctx.lease_manager.conflicts_with_running(tuple(args.path_claims))
        if conflict is not None:
            return ToolResult.error(
                f"spawn_agent's path_claims overlap a lease held by task '{conflict}' — "
                f"wait for it to finish, or narrow path_claims.",
                summary="spawn_agent (lease conflict)",
            )

    decision = ctx.permissions.confirm(
        "command", prompt_text=f"Spawn a '{args.role}' sub-agent for: {args.goal[:120]}"
    )
    if not decision.approved:
        return ToolResult.error("Sub-agent spawn was not approved.", summary="spawn_agent (declined)")

    from relaycli.agent.loop import Agent
    from relaycli.appconfig import load_app_config
    from relaycli.core.roster import specialist_runtime
    from relaycli.tools.capabilities import registry_for_role

    cfg = load_app_config()
    runtime = specialist_runtime(ctx.settings, cfg, args.role)
    try:
        child_registry = registry_for_role(args.role)
    except KeyError:
        child_registry = None  # role has no capability entry -> Agent's own default

    child_ctx = ToolContext(
        project=ctx.project,
        permissions=ctx.permissions,
        console=ctx.console,
        file_cache=ctx.file_cache,
        lease_manager=ctx.lease_manager,
        current_task_id=ctx.current_task_id,
        settings=ctx.settings,
        llm=ctx.llm,
        budget=ctx.budget,
        spawn_depth=ctx.spawn_depth + 1,
    )

    agent_kwargs: dict = dict(
        settings=ctx.settings, console=ctx.console, project=ctx.project,
        permissions=ctx.permissions, prompt_template=runtime.template, model=runtime.model,
    )
    if ctx.llm is not None:
        agent_kwargs["llm"] = ctx.llm
    if child_registry is not None:
        agent_kwargs["registry"] = child_registry
    agent = Agent(**agent_kwargs)
    agent.tool_ctx = child_ctx

    _log.info("spawn_agent: role=%s depth=%d goal=%r", args.role, child_ctx.spawn_depth, args.goal[:80])
    result = agent.run(args.goal)

    if ctx.budget is not None:
        from relaycli.agent.budget import BudgetExceeded

        try:
            ctx.budget.record(ctx.current_task_id or f"spawn:{args.role}", result.usage)
        except BudgetExceeded as exc:
            # The child already ran (its work and cost are real either
            # way) — this only turns the tool result into a clean failure
            # instead of an unhandled exception escaping into the parent
            # agent's own tool-call loop over what is, from its
            # perspective, an ordinary tool failure.
            return ToolResult.error(str(exc), summary=f"spawn_agent({args.role}) (budget exceeded)")

    return ToolResult(
        ok=(result.stopped_reason == "done"),
        output=result.final_text,
        summary=f"spawn_agent({args.role}) -> {result.stopped_reason}",
        meta={"iterations": result.iterations, "tool_calls": result.tool_calls,
              "stopped_reason": result.stopped_reason},
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="spawn_agent", description=(
        "Delegate a focused sub-task to a fresh specialist agent (roster role id) and "
        "get its result back. Recursion capped at 2 levels; the child's usage counts "
        "against this session's budget. Only available inside a scheduler-run task."
    ), args_model=SpawnAgentArgs, func=spawn_agent))
