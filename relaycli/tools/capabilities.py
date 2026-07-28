"""Tool capability matrix — replaces hand-maintained per-registry tool
lists (the old planner_registry/reviewer_registry each hand-listed their
tool names) with one systematic source of truth: what each tool can do
(read/write/exec/net — matching RoleDef.capabilities in core/roles.py),
crossed against which of those a role is allowed. A registry is *derived*
from the two, not hand-copied, so a role's tool access can't silently
drift the way the old hand-lists had (both were missing several read-only
tools nothing had ever gone back and added — see registry_for_role).
"""

from __future__ import annotations

from relaycli.tools.registry import ToolRegistry, default_registry

# Every tool actually registered by ToolRegistry._register_defaults,
# tagged with the single capability it needs (see core.roles.CAPABILITIES
# for what each tier means). `question` and `think` have no external side
# effect at all — asking the user something, or pure reasoning — and are
# tagged READ: always available, matching their unrestricted availability
# in every registry today. `git` is EXEC rather than READ even though most
# real invocations are read-only (status/log/diff/...), because the tool
# itself can run any git subcommand including destructive ones — its
# capability ceiling, not its typical use, is what a role grant must cover.
TOOL_CAPABILITIES: dict[str, str] = {
    "list_dir": "read",
    "read_file": "read",
    "find_files": "read",
    "search": "read",
    "check_process": "read",
    "todo_list": "read",
    "question": "read",
    "think": "read",
    "use_skill": "read",
    "write_file": "write",
    "edit_file": "write",
    "create_folder": "write",
    "apply_patch": "write",
    "remember": "write",
    "todo_add": "write",
    "todo_update": "write",
    "run_command": "exec",
    "run_background": "exec",
    "stop_process": "exec",
    "git": "exec",
    "webfetch": "net",
    "websearch": "net",
}


def registry_for_role(role_id: str) -> ToolRegistry:
    """Build a ToolRegistry containing every tool whose capability is
    covered by role_id's allowance in core.roles.BUILTIN_ROLES.

    Built by starting from default_registry() (the single place that knows
    *how* to register each tool) and pruning what the role isn't allowed,
    rather than re-registering tools by hand — so this can never drift out
    of sync with what default_registry actually contains. A tool missing
    from TOOL_CAPABILITIES is pruned (fails closed: excluded, never
    silently granted) — covered by
    test_capabilities.py::test_every_registered_tool_has_a_capability,
    so this branch should never actually trigger in practice.
    """
    from relaycli.core.roles import builtin_role

    role = builtin_role(role_id)
    if role is None:
        raise KeyError(f"Unknown role '{role_id}' — not in core.roles.BUILTIN_ROLES")

    reg = default_registry()
    for name in list(reg.names()):
        if TOOL_CAPABILITIES.get(name) not in role.capabilities:
            reg.unregister(name)
    return reg


def scheduler_task_registry(role_id: str) -> ToolRegistry:
    """registry_for_role(role_id) plus spawn_agent — for a Scheduler-run
    task's Agent specifically (agent/scheduler.py's production run_task).

    spawn_agent is deliberately NOT in TOOL_CAPABILITIES / default_registry:
    it only works when ToolContext.settings is set, which only a
    scheduler-run task's context has — offering it to the plain
    single-agent CLI/REPL flow (or any planner_registry/reviewer_registry
    consumer, which derive from default_registry too) would just be a
    tool the model can call and immediately get a "not available" refusal
    from, for no benefit.
    """
    from relaycli.tools import spawn_agent as _spawn_agent

    reg = registry_for_role(role_id)
    _spawn_agent.register(reg)
    return reg
