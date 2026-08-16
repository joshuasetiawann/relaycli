"""Task graph — replaces relay.py's fixed Planner->Coder->Reviewer chain
with an explicit, inspectable, editable set of tasks and dependencies.

The Orchestrator role emits a JSON task list (one model call per session —
see TaskGraph.parse); this module turns that into a graph the scheduler
(agent/scheduler.py) can compute ready sets from, and that a user can
review/edit before any task actually runs (§3.1: "a task graph the user
can't see or edit is a black box").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TaskStatus = Literal["pending", "ready", "running", "blocked", "done", "failed", "cancelled"]

_TERMINAL: frozenset[str] = frozenset({"done", "failed", "blocked", "cancelled"})


class GraphError(ValueError):
    """The task list is malformed: an unknown role, a dependency that
    doesn't exist, or a dependency cycle. Raised at construction time —
    a bad graph is refused before anything runs, not discovered mid-run."""


@dataclass
class Task:
    id: str
    role_id: str
    goal: str
    depends_on: tuple[str, ...] = ()
    path_claims: tuple[str, ...] = ()
    budget_tokens: int | None = None
    status: TaskStatus = "pending"


@dataclass
class TaskGraph:
    tasks: dict[str, Task] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for task in self.tasks.values():
            for dep in task.depends_on:
                if dep not in self.tasks:
                    raise GraphError(f"task '{task.id}' depends on unknown task '{dep}'")
        _check_acyclic(self.tasks)
        for task in self.tasks.values():
            if not task.depends_on:
                task.status = "ready"

    @classmethod
    def from_tasks(cls, tasks: list[Task]) -> "TaskGraph":
        by_id: dict[str, Task] = {}
        for t in tasks:
            if t.id in by_id:
                raise GraphError(f"duplicate task id '{t.id}'")
            by_id[t.id] = t
        return cls(by_id)

    # -- queries -----------------------------------------------------------

    def ready_tasks(self) -> list[Task]:
        """Tasks whose dependencies are all done and are themselves still
        pending/ready — does not consider path-lease conflicts, which is
        the scheduler's job (a task can be graph-ready but lease-blocked)."""
        out = []
        for task in self.tasks.values():
            if task.status not in ("pending", "ready"):
                continue
            if all(self.tasks[d].status == "done" for d in task.depends_on):
                out.append(task)
        return out

    def descendants(self, task_id: str) -> set[str]:
        """Every task that transitively depends on task_id (directly or
        indirectly) — used to compute the "subtree" the failure policy
        marks blocked when task_id fails for good."""
        deps_of: dict[str, list[str]] = {t.id: [] for t in self.tasks.values()}
        for t in self.tasks.values():
            for d in t.depends_on:
                deps_of[d].append(t.id)
        out: set[str] = set()
        stack = list(deps_of.get(task_id, []))
        while stack:
            tid = stack.pop()
            if tid in out:
                continue
            out.add(tid)
            stack.extend(deps_of.get(tid, []))
        return out

    def is_finished(self) -> bool:
        """True once every task has reached a terminal state — the
        scheduler's stop condition for "all done" (mixed with failures/
        blocks is still finished; it just isn't a clean success)."""
        return all(t.status in _TERMINAL for t in self.tasks.values())

    def all_ok(self) -> bool:
        return all(t.status == "done" for t in self.tasks.values())

    # -- mutation ------------------------------------------------------------

    def mark_running(self, task_id: str) -> None:
        self.tasks[task_id].status = "running"

    def mark_done(self, task_id: str) -> None:
        self.tasks[task_id].status = "done"
        for t in self.tasks.values():
            if t.status == "pending" and task_id in t.depends_on:
                if all(self.tasks[d].status == "done" for d in t.depends_on):
                    t.status = "ready"

    def mark_failed(self, task_id: str) -> None:
        """Per the master prompt's failure policy: retry once at the next
        tier (the scheduler's job, before calling this), then mark the
        subtree blocked and continue with everything independent of it."""
        self.tasks[task_id].status = "failed"
        for dep_id in self.descendants(task_id):
            dep = self.tasks[dep_id]
            if dep.status not in _TERMINAL:
                dep.status = "blocked"

    def mark_cancelled(self, task_id: str) -> None:
        self.tasks[task_id].status = "cancelled"

    def reset_for_retry(self, task_id: str) -> bool:
        """Put a settled task back in the queue, undoing the collateral
        damage of its own failure. Returns False if the task isn't in a
        state that can be retried (still running, or already done).

        mark_failed() blocks the whole descendant subtree, so a retry that
        only reset the one task would leave its dependents stuck at
        "blocked" forever and the graph would finish with work silently
        skipped. Every descendant blocked by this failure is therefore
        released too — back to "pending", since their own dependencies are
        by definition not done yet.
        """
        task = self.tasks[task_id]
        if task.status not in ("failed", "cancelled"):
            return False
        task.status = "ready" if self._deps_satisfied(task) else "pending"
        for dep_id in self.descendants(task_id):
            dependent = self.tasks[dep_id]
            if dependent.status == "blocked":
                dependent.status = "pending"
        return True

    def _deps_satisfied(self, task: Task) -> bool:
        return all(self.tasks[d].status == "done" for d in task.depends_on)


def _check_acyclic(tasks: dict[str, Task]) -> None:
    """Kahn's algorithm: if a topological order can't consume every task,
    there's a cycle. Raises GraphError naming one task still stuck in the
    cycle, rather than a generic "graph has a cycle" with no lead."""
    in_degree = {tid: len(t.depends_on) for tid, t in tasks.items()}
    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    visited = 0
    # reverse edges: dep -> dependents
    dependents: dict[str, list[str]] = {tid: [] for tid in tasks}
    for t in tasks.values():
        for dep in t.depends_on:
            dependents[dep].append(t.id)

    while queue:
        tid = queue.pop()
        visited += 1
        for nxt in dependents[tid]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if visited != len(tasks):
        stuck = next(tid for tid, deg in in_degree.items() if deg > 0)
        raise GraphError(f"dependency cycle detected (involves task '{stuck}')")


def parse_task_graph(text: str, *, valid_role_ids: frozenset[str] | None = None) -> TaskGraph:
    """Parse the Orchestrator's raw model output into a TaskGraph.

    Accepts either ``{"tasks": [...]}`` or a bare ``[...]`` list. Uses the
    same json-repair-based extraction as the rest of the agent stack
    (agent/__init__.py's _json_from_text) since models get JSON wrong
    constantly. Validates role ids against valid_role_ids when given
    (pass core.roles.BUILTIN_ROLES_BY_ID) so a hallucinated role name
    fails loudly here, with the task id that named it, rather than
    surfacing as a confusing KeyError deep inside the scheduler.
    """
    from relaycli.agent import _json_from_text

    parsed = _json_from_text(text)
    if parsed is None:
        raise GraphError("could not find a JSON task list in the orchestrator's response")
    raw_tasks = parsed.get("tasks") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise GraphError("task list is empty or not a list")

    tasks: list[Task] = []
    for i, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise GraphError(f"task #{i} is not an object")
        task_id = str(raw.get("id") or f"t{i + 1}")
        role_id = str(raw.get("role") or raw.get("role_id") or "")
        goal = str(raw.get("goal") or "").strip()
        if not role_id:
            raise GraphError(f"task '{task_id}' has no role")
        if valid_role_ids is not None and role_id not in valid_role_ids:
            # Smaller models echo the role back title-cased ("Orchestrator")
            # or spaced ("web dev"). That names a role that exists, so
            # canonicalise it rather than killing the run; anything that
            # still doesn't resolve is a genuine hallucination.
            key = role_id.strip().lower().replace(" ", "-").replace("_", "-")
            match = next((r for r in valid_role_ids if r.lower() == key), None)
            if match is None:
                raise GraphError(f"task '{task_id}' has unknown role '{role_id}'")
            role_id = match
        if not goal:
            raise GraphError(f"task '{task_id}' has no goal")
        depends_on = tuple(str(d) for d in (raw.get("depends_on") or []))
        path_claims = tuple(str(p) for p in (raw.get("path_claims") or []))
        budget_tokens = raw.get("budget_tokens")
        tasks.append(Task(
            id=task_id, role_id=role_id, goal=goal, depends_on=depends_on,
            path_claims=path_claims,
            budget_tokens=int(budget_tokens) if budget_tokens else None,
        ))
    return TaskGraph.from_tasks(tasks)
