"""Tests for the tool capability matrix (relaycli/tools/capabilities.py) —
Stage 1 of the v2 orchestrator prompt: "replace hand-built planner_registry/
reviewer_registry with a capability matrix ... registries derived." These
guard the matrix's own invariants, separate from planner_registry/
reviewer_registry's specific behavior (covered in test_relay.py/
test_tools.py)."""

from __future__ import annotations

import pytest

from relaycli.core.roles import BUILTIN_ROLES, CAPABILITIES, builtin_role
from relaycli.tools.capabilities import TOOL_CAPABILITIES, registry_for_role
from relaycli.tools.registry import default_registry


def test_every_registered_tool_has_a_capability():
    """A tool present in default_registry() but missing from
    TOOL_CAPABILITIES silently loses access to every restricted-tier role
    (registry_for_role's fail-closed default) rather than a loud error —
    this is what should catch that before it ships quietly."""
    registered = set(default_registry().names())
    mapped = set(TOOL_CAPABILITIES)
    assert registered == mapped, (
        f"registered but unmapped: {registered - mapped}; "
        f"mapped but not registered: {mapped - registered}"
    )


def test_every_tool_capability_is_a_known_capability():
    for name, cap in TOOL_CAPABILITIES.items():
        assert cap in CAPABILITIES, f"{name} has unknown capability {cap!r}"


def test_every_builtin_role_has_at_least_read():
    for role in BUILTIN_ROLES:
        assert role.capabilities, f"{role.id} has no capabilities at all"
        assert "read" in role.capabilities, f"{role.id} can't even read"


def test_every_role_capability_is_a_known_capability():
    for role in BUILTIN_ROLES:
        assert role.capabilities <= set(CAPABILITIES), (
            f"{role.id} has unknown capability in {role.capabilities}"
        )


def test_registry_for_role_unknown_role_raises():
    with pytest.raises(KeyError):
        registry_for_role("not-a-real-role")


def test_registry_for_role_matches_declared_capabilities_exactly():
    """For every one of the 16 roles: the derived registry contains exactly
    the tools whose capability is in that role's set — not a subset, not a
    superset. This is the actual "derived, not hand-copied" guarantee."""
    for role in BUILTIN_ROLES:
        names = set(registry_for_role(role.id).names())
        expected = {n for n, cap in TOOL_CAPABILITIES.items() if cap in role.capabilities}
        assert names == expected, f"role {role.id}: {names} != {expected}"


def test_read_only_role_gets_no_write_or_exec_tools():
    reg = registry_for_role("researcher")
    names = set(reg.names())
    assert not any(TOOL_CAPABILITIES.get(n) in ("write", "exec") for n in names)
    assert "read_file" in names


def test_full_implementation_role_gets_write_and_exec():
    reg = registry_for_role("coder")
    names = set(reg.names())
    assert "write_file" in names
    assert "run_command" in names
    assert "read_file" in names


def test_verify_only_role_gets_exec_but_not_write():
    """reviewer/security: can run tests/inspect, can't change files."""
    for role_id in ("reviewer", "security"):
        reg = registry_for_role(role_id)
        names = set(reg.names())
        assert "run_command" in names
        assert not any(TOOL_CAPABILITIES.get(n) == "write" for n in names)


def test_write_no_exec_role_gets_no_command_tools():
    """documenter: writes docs, never runs a command."""
    reg = registry_for_role("documenter")
    names = set(reg.names())
    assert "write_file" in names
    assert not any(TOOL_CAPABILITIES.get(n) == "exec" for n in names)


def test_registry_for_role_reuses_default_registry_tool_definitions():
    """The derived registry must not redefine tools — same schema objects
    default_registry() produces, just a filtered set (mirrors the existing
    test_subset_schemas_match_default in test_relay.py, at the matrix
    level rather than just for planner/reviewer)."""
    default_schemas = {s["function"]["name"]: s for s in default_registry().schemas()}
    for role in BUILTIN_ROLES:
        for schema in registry_for_role(role.id).schemas():
            name = schema["function"]["name"]
            assert schema == default_schemas[name]


def test_all_16_roles_are_covered():
    assert len(BUILTIN_ROLES) == 16
    assert len({r.id for r in BUILTIN_ROLES}) == 16  # no duplicate ids
