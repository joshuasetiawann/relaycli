"""Smart model router: which model serves which relay role.

Deliberately trivial — an explicit config-driven mapping with a fallback to
the base model. It exists as its own module so future routing logic
(task-complexity heuristics, cost caps) lands here without touching callers.
"""

from __future__ import annotations

from enum import Enum

from relaycli.config import Settings


class Role(str, Enum):
    """A relay pipeline role."""

    planner = "planner"
    coder = "coder"
    reviewer = "reviewer"

    def __str__(self) -> str:  # nicer display in banners / summaries
        return self.value


def resolve_model(settings: Settings, role: Role) -> str:
    """Return the model id serving ``role``: the role override or the base model."""
    override: str | None = getattr(settings, f"{role.value}_model")
    return override or settings.model


def routing_table(settings: Settings) -> dict[Role, str]:
    """Role → resolved model, for banners and the ``config`` display."""
    return {role: resolve_model(settings, role) for role in Role}
