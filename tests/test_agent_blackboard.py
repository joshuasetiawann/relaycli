"""Tests for relaycli/agent/blackboard.py — the Stage 3 mechanism that
lets concurrent agents avoid re-reading the same files."""

from __future__ import annotations

from relaycli.agent.blackboard import Blackboard, Finding


def _finding(task_id="t1", role="researcher", kind="file_survey", summary="looked at it",
             refs=()):
    return Finding(task_id=task_id, role=role, kind=kind, summary=summary, refs=refs)


def test_post_and_all():
    b = Blackboard()
    b.post(_finding(summary="first"))
    b.post(_finding(summary="second"))
    assert [f.summary for f in b.all()] == ["first", "second"]


def test_all_returns_a_copy_not_the_live_list():
    b = Blackboard()
    b.post(_finding())
    snapshot = b.all()
    b.post(_finding(summary="added after snapshot"))
    assert len(snapshot) == 1


def test_by_kind_filters():
    b = Blackboard()
    b.post(_finding(kind="file_survey"))
    b.post(_finding(kind="test_result"))
    b.post(_finding(kind="file_survey"))
    assert len(b.by_kind("file_survey")) == 2
    assert len(b.by_kind("test_result")) == 1
    assert b.by_kind("nonexistent") == []


def test_by_task_filters():
    b = Blackboard()
    b.post(_finding(task_id="t1"))
    b.post(_finding(task_id="t2"))
    b.post(_finding(task_id="t1"))
    assert len(b.by_task("t1")) == 2
    assert len(b.by_task("t2")) == 1


def test_refs_covered_unions_all_findings():
    b = Blackboard()
    b.post(_finding(refs=("src/a.py", "src/b.py")))
    b.post(_finding(refs=("src/c.py",)))
    assert b.refs_covered() == {"src/a.py", "src/b.py", "src/c.py"}


def test_refs_covered_empty_when_nothing_posted():
    assert Blackboard().refs_covered() == set()


def test_find_covering_returns_most_recent_match():
    b = Blackboard()
    b.post(_finding(task_id="t1", summary="stale", refs=("src/a.py",)))
    b.post(_finding(task_id="t2", summary="fresh", refs=("src/a.py",)))
    found = b.find_covering("src/a.py")
    assert found is not None
    assert found.summary == "fresh"


def test_find_covering_returns_none_when_unreferenced():
    b = Blackboard()
    b.post(_finding(refs=("src/a.py",)))
    assert b.find_covering("src/never-mentioned.py") is None


def test_nothing_is_ever_removed_history_stays_inspectable():
    """The blackboard is append-only by design (see its own docstring) —
    a superseded finding must still be visible in .all()."""
    b = Blackboard()
    b.post(_finding(task_id="t1", summary="stale", refs=("src/a.py",)))
    b.post(_finding(task_id="t2", summary="fresh", refs=("src/a.py",)))
    assert len(b.all()) == 2
