"""Budget governor — per-task and per-session ceilings in tokens and USD.

A runaway agent on a metered API is the failure users never forgive
(§3.5). Checked before launch and mid-flight; on breach, the scheduler
cancels the offending task, not the whole session — everything else keeps
running.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from relaycli.core.llm import Usage


class BudgetExceeded(RuntimeError):
    """A task or the session as a whole hit its ceiling. Names which one
    and by how much, so the cancellation the scheduler performs in
    response is explainable to the user, not a silent kill."""


@dataclass
class BudgetGovernor:
    max_tokens_per_task: int | None = None
    max_tokens_total: int | None = None
    max_usd_total: float | None = None

    spent_tokens_total: int = 0
    spent_usd_total: float = 0.0
    _spent_tokens_by_task: dict[str, int] = field(default_factory=dict)

    def check_before_launch(self, task_id: str) -> None:
        """Called before starting a task — refuses to launch anything once
        the session total is already exhausted, rather than letting one
        more task start and only catching it after the fact."""
        if self.max_tokens_total is not None and self.spent_tokens_total >= self.max_tokens_total:
            raise BudgetExceeded(
                f"session token budget exhausted "
                f"({self.spent_tokens_total}/{self.max_tokens_total}) — "
                f"cannot start task '{task_id}'"
            )
        if self.max_usd_total is not None and self.spent_usd_total >= self.max_usd_total:
            raise BudgetExceeded(
                f"session budget exhausted (${self.spent_usd_total:.2f}/"
                f"${self.max_usd_total:.2f}) — cannot start task '{task_id}'"
            )

    def record(self, task_id: str, usage: Usage) -> None:
        """Update running totals and raise BudgetExceeded (naming task_id)
        the moment either ceiling is crossed — called after every model
        call, not just at task boundaries, so a single chatty task can't
        blow the whole session budget before anyone notices."""
        self._spent_tokens_by_task[task_id] = (
            self._spent_tokens_by_task.get(task_id, 0) + usage.total_tokens
        )
        self.spent_tokens_total += usage.total_tokens
        self.spent_usd_total += usage.cost_usd

        per_task = self._spent_tokens_by_task[task_id]
        if self.max_tokens_per_task is not None and per_task > self.max_tokens_per_task:
            raise BudgetExceeded(
                f"task '{task_id}' exceeded its per-task token budget "
                f"({per_task}/{self.max_tokens_per_task})"
            )
        if self.max_tokens_total is not None and self.spent_tokens_total > self.max_tokens_total:
            raise BudgetExceeded(
                f"session token budget exceeded "
                f"({self.spent_tokens_total}/{self.max_tokens_total}) during task '{task_id}'"
            )
        if self.max_usd_total is not None and self.spent_usd_total > self.max_usd_total:
            raise BudgetExceeded(
                f"session budget exceeded (${self.spent_usd_total:.2f}/"
                f"${self.max_usd_total:.2f}) during task '{task_id}'"
            )

    def remaining_tokens_total(self) -> int | None:
        if self.max_tokens_total is None:
            return None
        return max(0, self.max_tokens_total - self.spent_tokens_total)

    def tokens_spent_by(self, task_id: str) -> int:
        return self._spent_tokens_by_task.get(task_id, 0)
