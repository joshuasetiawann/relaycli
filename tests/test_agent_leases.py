"""Tests for relaycli/agent/leases.py — the mechanism Stage 3's acceptance
criteria calls out explicitly: "Two tasks claiming one path never run
concurrently — with a test proving it.\""""

from __future__ import annotations

import pytest

from relaycli.agent.leases import LeaseError, LeaseManager, claims_overlap


# --- claims_overlap ------------------------------------------------------
def test_identical_paths_overlap():
    assert claims_overlap(("src/a.py",), ("src/a.py",))


def test_disjoint_directories_do_not_overlap():
    assert not claims_overlap(("src/api/**",), ("docs/**",))


def test_glob_matches_a_concrete_path_inside_it():
    assert claims_overlap(("src/**",), ("src/api/queue.ts",))


def test_sibling_directories_with_shared_prefix_do_not_overlap():
    assert not claims_overlap(("src/api/**",), ("src/ui/**",))


def test_single_unrelated_file_does_not_overlap_a_directory_glob():
    assert not claims_overlap(("README.md",), ("src/**",))


def test_multiple_claims_any_pairwise_overlap_counts():
    assert claims_overlap(("docs/**", "src/api/queue.ts"), ("src/api/**",))


def test_empty_claims_never_overlap():
    assert not claims_overlap((), ("src/**",))
    assert not claims_overlap(("src/**",), ())


# --- LeaseManager: acquire / conflict --------------------------------------
def test_acquire_succeeds_with_no_prior_leases():
    m = LeaseManager()
    lease = m.acquire("t1", ("src/api/**",))
    assert lease.task_id == "t1"
    assert lease.paths == ("src/api/**",)


def test_acquire_conflicting_path_raises_lease_error():
    m = LeaseManager()
    m.acquire("t1", ("src/api/**",))
    with pytest.raises(LeaseError, match="t1"):
        m.acquire("t2", ("src/api/queue.ts",))


def test_two_tasks_claiming_one_path_never_both_hold_it():
    """The literal acceptance criterion: prove two overlapping claims can
    never coexist, by exhaustively trying both acquisition orders."""
    for first, second in [("t1", "t2"), ("t2", "t1")]:
        m = LeaseManager()
        m.acquire(first, ("shared/file.py",))
        with pytest.raises(LeaseError):
            m.acquire(second, ("shared/file.py",))
        # exactly one task holds it, never both
        assert set(m.held_paths()) == {first}


def test_acquire_disjoint_paths_both_succeed():
    m = LeaseManager()
    m.acquire("t1", ("src/api/**",))
    m.acquire("t2", ("src/ui/**",))
    assert set(m.held_paths()) == {"t1", "t2"}


def test_conflicts_with_running_reports_holder_without_acquiring():
    m = LeaseManager()
    m.acquire("t1", ("src/api/**",))
    assert m.conflicts_with_running(("src/api/queue.ts",)) == "t1"
    assert m.conflicts_with_running(("docs/**",)) is None
    assert set(m.held_paths()) == {"t1"}  # unchanged — a query, not a mutation


def test_release_frees_the_path_for_another_task():
    m = LeaseManager()
    m.acquire("t1", ("src/api/**",))
    m.release("t1")
    m.acquire("t2", ("src/api/**",))  # would have raised before release
    assert set(m.held_paths()) == {"t2"}


def test_release_unknown_task_is_a_no_op():
    m = LeaseManager()
    m.release("never-acquired")  # must not raise


# --- holder_of / check_write_allowed ---------------------------------------
def test_holder_of_returns_the_owning_task():
    m = LeaseManager()
    m.acquire("t1", ("src/api/**",))
    assert m.holder_of("src/api/queue.ts") == "t1"
    assert m.holder_of("docs/readme.md") is None


def test_check_write_allowed_passes_for_the_lease_holder():
    m = LeaseManager()
    m.acquire("t1", ("src/api/**",))
    m.check_write_allowed("t1", "src/api/queue.ts")  # must not raise


def test_check_write_allowed_blocks_a_different_task():
    m = LeaseManager()
    m.acquire("t1", ("src/api/**",))
    with pytest.raises(LeaseError, match="t2"):
        m.check_write_allowed("t2", "src/api/queue.ts")


def test_check_write_allowed_blocks_an_unclaimed_path():
    m = LeaseManager()
    with pytest.raises(LeaseError, match="without a lease"):
        m.check_write_allowed("t1", "unclaimed/file.py")


def test_check_write_allowed_none_task_id_always_passes():
    """None means "not running under the scheduler" (the plain
    single-agent flow) — leases only govern scheduled concurrent
    execution, so this must never block it."""
    m = LeaseManager()
    m.check_write_allowed(None, "anything/at/all.py")  # must not raise
    m.acquire("t1", ("src/**",))
    m.check_write_allowed(None, "src/api/queue.ts")  # still must not raise
