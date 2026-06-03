"""Smart model router: which model serves which relay role.

Deliberately trivial — an explicit config-driven mapping with a fallback to
the base model. It exists as its own module so future routing logic
(task-complexity heuristics, cost caps) lands here without touching callers.
"""

from __future__ import annotations

from enum import Enum

from relaycli.config import Settings


class Role(str, Enum):
    """A relay pipeline role, declared in pipeline order."""

    explorer = "explorer"  # optional: scouts the codebase before planning
    planner = "planner"
    coder = "coder"
    tester = "tester"      # optional: runs the verification step after coding
    reviewer = "reviewer"

    def __str__(self) -> str:  # nicer display in banners / summaries
        return self.value


def resolve_model(settings: Settings, role: Role) -> str:
    """Return the model id serving ``role``: the role override or the base model."""
    override: str | None = getattr(settings, f"{role.value}_model")
    return override or settings.model


def role_enabled(settings: Settings, role: Role) -> bool:
    """Whether ``role`` takes part in the pipeline under ``settings``."""
    if role is Role.explorer:
        return settings.relay_explorer
    if role is Role.tester:
        return settings.relay_tester
    return True  # planner/coder/reviewer are the pipeline's backbone


def routing_table(settings: Settings) -> dict[Role, str]:
    """Active role → resolved model, in pipeline order (banners, ``config``)."""
    return {
        role: resolve_model(settings, role)
        for role in Role
        if role_enabled(settings, role)
    }
