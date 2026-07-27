"""Model router — which model serves which role."""

from __future__ import annotations

from enum import Enum

from relaycli.core.config import Settings


class Role(str, Enum):
    explorer = "explorer"
    planner = "planner"
    coder = "coder"
    tester = "tester"
    reviewer = "reviewer"

    def __str__(self) -> str:
        return self.value


def resolve_model(settings: Settings, role: Role) -> str:
    override: str | None = getattr(settings, f"{role.value}_model")
    return override or settings.model


def role_enabled(settings: Settings, role: Role) -> bool:
    if role is Role.explorer:
        return settings.relay_explorer
    if role is Role.tester:
        return settings.relay_tester
    return True


def routing_table(settings: Settings) -> dict[Role, str]:
    return {role: resolve_model(settings, role) for role in Role if role_enabled(settings, role)}
