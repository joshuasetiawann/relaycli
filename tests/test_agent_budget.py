"""Tests for relaycli/agent/budget.py — Stage 3's budget governor:
"stops a runaway agent without killing the session" (acceptance
criterion)."""

from __future__ import annotations

import pytest

from relaycli.agent.budget import BudgetExceeded, BudgetGovernor
from relaycli.core.llm import Usage


def test_no_ceilings_never_raises():
    b = BudgetGovernor()
    b.check_before_launch("t1")
    for _ in range(100):
        b.record("t1", Usage(total_tokens=10_000, cost_usd=100.0))  # must not raise


def test_check_before_launch_passes_under_ceiling():
    b = BudgetGovernor(max_tokens_total=100)
    b.check_before_launch("t1")  # must not raise


def test_check_before_launch_refuses_when_total_already_exhausted():
    b = BudgetGovernor(max_tokens_total=100)
    b.spent_tokens_total = 100
    with pytest.raises(BudgetExceeded, match="t1"):
        b.check_before_launch("t1")


def test_check_before_launch_refuses_when_usd_already_exhausted():
    b = BudgetGovernor(max_usd_total=5.0)
    b.spent_usd_total = 5.0
    with pytest.raises(BudgetExceeded):
        b.check_before_launch("t1")


def test_record_accumulates_totals():
    b = BudgetGovernor()
    b.record("t1", Usage(total_tokens=50, cost_usd=0.1))
    b.record("t2", Usage(total_tokens=30, cost_usd=0.05))
    assert b.spent_tokens_total == 80
    assert b.spent_usd_total == pytest.approx(0.15)
    assert b.tokens_spent_by("t1") == 50
    assert b.tokens_spent_by("t2") == 30


def test_record_raises_on_per_task_ceiling_naming_the_task():
    b = BudgetGovernor(max_tokens_per_task=100)
    b.record("t1", Usage(total_tokens=60))
    with pytest.raises(BudgetExceeded, match="t1"):
        b.record("t1", Usage(total_tokens=60))  # 120 > 100


def test_record_per_task_ceiling_does_not_affect_other_tasks():
    b = BudgetGovernor(max_tokens_per_task=100)
    b.record("t1", Usage(total_tokens=90))
    b.record("t2", Usage(total_tokens=90))  # separate task, separate ceiling — must not raise
    assert b.tokens_spent_by("t2") == 90


def test_record_raises_on_session_total_ceiling():
    b = BudgetGovernor(max_tokens_total=100)
    b.record("a", Usage(total_tokens=60))
    b.record("b", Usage(total_tokens=30))
    with pytest.raises(BudgetExceeded, match="session"):
        b.record("c", Usage(total_tokens=20))


def test_record_raises_on_usd_ceiling():
    b = BudgetGovernor(max_usd_total=1.0)
    with pytest.raises(BudgetExceeded):
        b.record("t1", Usage(total_tokens=1, cost_usd=1.5))


def test_remaining_tokens_total_none_when_unbounded():
    assert BudgetGovernor().remaining_tokens_total() is None


def test_remaining_tokens_total_tracks_spend():
    b = BudgetGovernor(max_tokens_total=100)
    b.record("t1", Usage(total_tokens=40))
    assert b.remaining_tokens_total() == 60


def test_remaining_tokens_total_floors_at_zero_not_negative():
    b = BudgetGovernor(max_tokens_total=100)
    b._spent_tokens_by_task["t1"] = 150
    b.spent_tokens_total = 150  # simulate an overshoot without tripping record()'s own raise
    assert b.remaining_tokens_total() == 0


def test_tokens_spent_by_unknown_task_is_zero():
    assert BudgetGovernor().tokens_spent_by("never-recorded") == 0


def test_breach_only_stops_the_offending_task_not_the_governor():
    """After a BudgetExceeded, the governor itself must stay usable for
    the scheduler to keep running everything else — this is the "cancel
    one task, not the session" acceptance criterion at the governor
    level (the scheduler is what actually decides not to cancel siblings,
    covered in test_agent_scheduler.py)."""
    b = BudgetGovernor(max_tokens_per_task=50)
    with pytest.raises(BudgetExceeded):
        b.record("bad", Usage(total_tokens=60))
    b.record("good", Usage(total_tokens=10))  # must still work
    assert b.tokens_spent_by("good") == 10
