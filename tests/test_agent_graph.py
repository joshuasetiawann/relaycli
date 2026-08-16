"""Tests for relaycli/agent/graph.py — the Stage 3 task graph that
replaces relay.py's fixed Planner->Coder->Reviewer chain."""

from __future__ import annotations

from relaycli.agent.graph import GraphError, Task, TaskGraph, parse_task_graph

import pytest


def _t(id_, deps=(), role="coder", goal="do it"):
    return Task(id=id_, role_id=role, goal=goal, depends_on=deps)


# --- construction / validation --------------------------------------------
def test_tasks_with_no_deps_start_ready():
    g = TaskGraph.from_tasks([_t("a"), _t("b")])
    assert {t.id for t in g.ready_tasks()} == {"a", "b"}


def test_tasks_with_deps_start_pending_not_ready():
    g = TaskGraph.from_tasks([_t("a"), _t("b", deps=("a",))])
    assert {t.id for t in g.ready_tasks()} == {"a"}
    assert g.tasks["b"].status == "pending"


def test_duplicate_task_id_raises():
    with pytest.raises(GraphError, match="duplicate"):
        TaskGraph.from_tasks([_t("a"), _t("a")])


def test_unknown_dependency_raises():
    with pytest.raises(GraphError, match="unknown task"):
        TaskGraph.from_tasks([_t("a", deps=("nope",))])


def test_self_cycle_raises():
    with pytest.raises(GraphError, match="cycle"):
        TaskGraph.from_tasks([_t("a", deps=("a",))])


def test_longer_cycle_raises():
    with pytest.raises(GraphError, match="cycle"):
        TaskGraph.from_tasks([_t("a", deps=("c",)), _t("b", deps=("a",)), _t("c", deps=("b",))])


def test_diamond_shape_is_valid():
    g = TaskGraph.from_tasks([_t("a"), _t("b", deps=("a",)), _t("c", deps=("a",)), _t("d", deps=("b", "c"))])
    assert g.tasks["d"].status == "pending"


# --- ready-set progression --------------------------------------------------
def test_mark_done_unlocks_dependents_only_when_all_deps_done():
    g = TaskGraph.from_tasks([_t("a"), _t("b"), _t("c", deps=("a", "b"))])
    g.mark_running("a")
    g.mark_done("a")
    assert g.tasks["c"].status == "pending"  # b still not done
    g.mark_running("b")
    g.mark_done("b")
    assert g.tasks["c"].status == "ready"


def test_ready_tasks_excludes_running_and_terminal():
    g = TaskGraph.from_tasks([_t("a"), _t("b")])
    g.mark_running("a")
    assert {t.id for t in g.ready_tasks()} == {"b"}
    g.mark_done("a")
    assert {t.id for t in g.ready_tasks()} == {"b"}


# --- failure policy: retry-then-block-subtree -------------------------------
def test_mark_failed_blocks_direct_dependents():
    g = TaskGraph.from_tasks([_t("a"), _t("b", deps=("a",))])
    g.mark_running("a")
    g.mark_failed("a")
    assert g.tasks["a"].status == "failed"
    assert g.tasks["b"].status == "blocked"


def test_mark_failed_blocks_entire_transitive_subtree():
    g = TaskGraph.from_tasks([
        _t("a"), _t("b", deps=("a",)), _t("c", deps=("b",)), _t("independent"),
    ])
    g.mark_running("a")
    g.mark_failed("a")
    assert g.tasks["b"].status == "blocked"
    assert g.tasks["c"].status == "blocked"
    assert g.tasks["independent"].status in ("pending", "ready")  # untouched


def test_mark_failed_does_not_reblock_already_terminal_dependents():
    g = TaskGraph.from_tasks([_t("a"), _t("b", deps=("a",)), _t("c", deps=("a",))])
    g.mark_running("a")
    # simulate b already finished (e.g. via some other path) before a fails —
    # shouldn't happen in practice but the guard should hold regardless
    g.tasks["b"].status = "done"
    g.mark_failed("a")
    assert g.tasks["b"].status == "done"  # not reset to blocked
    assert g.tasks["c"].status == "blocked"


def test_descendants_is_transitive_and_excludes_unrelated():
    g = TaskGraph.from_tasks([
        _t("a"), _t("b", deps=("a",)), _t("c", deps=("b",)), _t("other"),
    ])
    assert g.descendants("a") == {"b", "c"}
    assert g.descendants("other") == set()


# --- terminal state -----------------------------------------------------
def test_is_finished_false_while_anything_pending_or_running():
    g = TaskGraph.from_tasks([_t("a"), _t("b")])
    assert not g.is_finished()
    g.mark_running("a")
    assert not g.is_finished()


def test_is_finished_true_when_everything_terminal():
    g = TaskGraph.from_tasks([_t("a"), _t("b")])
    g.mark_running("a")
    g.mark_done("a")
    g.mark_running("b")
    g.mark_failed("b")
    assert g.is_finished()


def test_all_ok_false_if_anything_failed_or_blocked():
    g = TaskGraph.from_tasks([_t("a"), _t("b", deps=("a",))])
    g.mark_running("a")
    g.mark_failed("a")
    assert g.is_finished()
    assert not g.all_ok()


def test_all_ok_true_when_everything_done():
    g = TaskGraph.from_tasks([_t("a"), _t("b")])
    g.mark_running("a"); g.mark_done("a")
    g.mark_running("b"); g.mark_done("b")
    assert g.all_ok()


def test_mark_cancelled():
    g = TaskGraph.from_tasks([_t("a")])
    g.mark_cancelled("a")
    assert g.tasks["a"].status == "cancelled"


# --- parse_task_graph --------------------------------------------------------
def test_parse_task_graph_accepts_bare_list():
    g = parse_task_graph('[{"id": "t1", "role": "backend", "goal": "build it"}]')
    assert g.tasks["t1"].role_id == "backend"
    assert g.tasks["t1"].goal == "build it"


def test_parse_task_graph_accepts_tasks_wrapper():
    g = parse_task_graph('{"tasks": [{"id": "t1", "role": "backend", "goal": "build it"}]}')
    assert "t1" in g.tasks


def test_parse_task_graph_reads_deps_and_path_claims():
    g = parse_task_graph(
        '{"tasks": ['
        '{"id": "t1", "role": "backend", "goal": "a"},'
        '{"id": "t2", "role": "frontend", "goal": "b", "depends_on": ["t1"], '
        '"path_claims": ["src/ui/**"]}'
        ']}'
    )
    assert g.tasks["t2"].depends_on == ("t1",)
    assert g.tasks["t2"].path_claims == ("src/ui/**",)


def test_parse_task_graph_extracts_from_code_fence():
    text = 'Here is the plan:\n```json\n{"tasks": [{"id": "t1", "role": "coder", "goal": "x"}]}\n```'
    g = parse_task_graph(text)
    assert "t1" in g.tasks


def test_parse_task_graph_defaults_missing_id():
    g = parse_task_graph('[{"role": "coder", "goal": "x"}]')
    assert list(g.tasks) == ["t1"]


def test_parse_task_graph_rejects_missing_role():
    with pytest.raises(GraphError, match="no role"):
        parse_task_graph('[{"id": "t1", "goal": "x"}]')


def test_parse_task_graph_rejects_missing_goal():
    with pytest.raises(GraphError, match="no goal"):
        parse_task_graph('[{"id": "t1", "role": "coder"}]')


def test_parse_task_graph_rejects_unknown_role_when_validated():
    with pytest.raises(GraphError, match="unknown role"):
        parse_task_graph(
            '[{"id": "t1", "role": "not-a-real-role", "goal": "x"}]',
            valid_role_ids=frozenset({"coder", "backend"}),
        )


def test_parse_task_graph_allows_any_role_when_not_validated():
    g = parse_task_graph('[{"id": "t1", "role": "anything", "goal": "x"}]')
    assert g.tasks["t1"].role_id == "anything"


def test_parse_task_graph_rejects_non_json_text():
    with pytest.raises(GraphError, match="could not find"):
        parse_task_graph("Sorry, I can't help with that.")


def test_parse_task_graph_rejects_empty_list():
    with pytest.raises(GraphError, match="empty"):
        parse_task_graph("[]")


def test_parse_task_graph_canonicalises_a_miscased_role():
    # Smaller local models title-case the role they were handed; the role is
    # real, so the run should not die over the capital O.
    g = parse_task_graph(
        '[{"id": "t1", "role": "Orchestrator", "goal": "x"},'
        ' {"id": "t2", "role": "Web Dev", "goal": "y"}]',
        valid_role_ids=frozenset({"orchestrator", "web-dev"}),
    )
    assert g.tasks["t1"].role_id == "orchestrator"
    assert g.tasks["t2"].role_id == "web-dev"
