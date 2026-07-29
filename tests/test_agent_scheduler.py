"""Tests for relaycli/agent/scheduler.py — Stage 3's parallel orchestration
core. Deterministic per the master prompt's own testing rule ("fake clock,
fake LLM, asserted interleavings" — no real LLM anywhere here): fake task
runners using real asyncio.to_thread(time.sleep(...)) to prove genuine
concurrency, not mocked timing.

These map directly to Part A's acceptance criteria (§3 "Acceptance"):
- Three independent tasks finish in measurably less wall-clock than serial
- Two tasks claiming one path never run concurrently — with a test proving it
- Cancelling one agent leaves the others running
- Ctrl-C cancels all cleanly — no orphan subprocesses, no corrupt files
- Blackboard measurably cuts duplicate reads
- Budget breach kills one agent and reports why
"""

from __future__ import annotations

import asyncio
import time

from relaycli.agent.budget import BudgetGovernor
from relaycli.agent.graph import Task, TaskGraph
from relaycli.agent.leases import LeaseManager
from relaycli.agent.scheduler import Scheduler, TaskOutcome
from relaycli.core.llm import Usage


def _graph(*tasks: Task) -> TaskGraph:
    return TaskGraph.from_tasks(list(tasks))


def _instant_ok(task: Task) -> TaskOutcome:
    return TaskOutcome(task_id=task.id, ok=True, summary=f"{task.id} done")


# --- basic completion --------------------------------------------------
def test_independent_tasks_all_complete():
    async def run_task(task):
        return _instant_ok(task)

    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    result = asyncio.run(Scheduler(graph, run_task).run())
    assert graph.all_ok()
    assert set(result.outcomes) == {"a", "b"}
    assert not result.stopped_early


def test_outcomes_and_task_started_at_are_readable_mid_run_via_on_tick():
    # A live view (ui/live.py) has no other way to see per-task usage/cost
    # (Agent.run() only returns a usage total at completion) or per-task
    # elapsed time — both must be live-readable off the Scheduler instance.
    holder: dict = {}
    snapshots = []

    def on_tick():
        sched = holder["scheduler"]
        snapshots.append((dict(sched.task_started_at), dict(sched.outcomes)))

    async def run_task(task):
        await asyncio.to_thread(time.sleep, 0.05)
        return TaskOutcome(task_id=task.id, ok=True, summary="done", usage=Usage(total_tokens=42))

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    sched = Scheduler(graph, run_task, on_tick=on_tick, should_stop_poll_interval=0.01)
    holder["scheduler"] = sched
    asyncio.run(sched.run())

    assert any("a" in started for started, _ in snapshots), \
        "task_started_at should show 'a' while it's running, before it completes"
    assert sched.outcomes["a"].usage.total_tokens == 42


def test_scheduler_result_carries_graph_and_budget_references():
    async def run_task(task):
        return _instant_ok(task)

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    budget = BudgetGovernor()
    sched = Scheduler(graph, run_task, budget=budget)
    result = asyncio.run(sched.run())
    assert result.graph is graph
    assert result.budget is budget
    assert result.blackboard is sched.blackboard


# --- acceptance: genuine wall-clock parallelism ----------------------------
def test_three_independent_tasks_run_concurrently_not_serially():
    async def main():
        async def run_task(task):
            await asyncio.to_thread(time.sleep, 0.3)
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(*(Task(id=f"t{i}", role_id="coder", goal="x") for i in range(3)))
        sched = Scheduler(graph, run_task, max_concurrent_agents=3)
        start = time.perf_counter()
        await sched.run()
        return time.perf_counter() - start

    elapsed = asyncio.run(main())
    # Serial would be ~0.9s; parallel should be close to the single 0.3s task.
    assert elapsed < 0.6, f"tasks did not run in parallel (took {elapsed:.2f}s)"


def test_concurrency_cap_is_respected():
    async def main():
        active = {"n": 0, "max": 0}

        async def run_task(task):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.to_thread(time.sleep, 0.15)
            active["n"] -= 1
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(*(Task(id=f"t{i}", role_id="coder", goal="x") for i in range(6)))
        sched = Scheduler(graph, run_task, max_concurrent_agents=2)
        await sched.run()
        return active["max"]

    max_concurrent = asyncio.run(main())
    assert max_concurrent == 2


# --- acceptance: path leases prevent concurrent writes ----------------------
def test_two_tasks_claiming_one_path_never_run_concurrently():
    async def main():
        active_on_path = {"n": 0, "max": 0}

        async def run_task(task):
            active_on_path["n"] += 1
            active_on_path["max"] = max(active_on_path["max"], active_on_path["n"])
            await asyncio.to_thread(time.sleep, 0.15)
            active_on_path["n"] -= 1
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(
            Task(id="a", role_id="coder", goal="x", path_claims=("shared/file.py",)),
            Task(id="b", role_id="coder", goal="y", path_claims=("shared/file.py",)),
        )
        sched = Scheduler(graph, run_task, max_concurrent_agents=3)
        start = time.perf_counter()
        await sched.run()
        elapsed = time.perf_counter() - start
        return active_on_path["max"], elapsed

    max_concurrent, elapsed = asyncio.run(main())
    assert max_concurrent == 1, "two tasks with an overlapping path claim ran at the same time"
    assert elapsed > 0.25, "serialized tasks finished too fast to have actually waited"


def test_disjoint_path_claims_do_run_concurrently():
    async def main():
        active = {"n": 0, "max": 0}

        async def run_task(task):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.to_thread(time.sleep, 0.15)
            active["n"] -= 1
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(
            Task(id="a", role_id="coder", goal="x", path_claims=("src/api/**",)),
            Task(id="b", role_id="coder", goal="y", path_claims=("src/ui/**",)),
        )
        sched = Scheduler(graph, run_task, max_concurrent_agents=3)
        await sched.run()
        return active["max"]

    assert asyncio.run(main()) == 2


def test_lease_is_released_after_completion_so_a_waiting_task_can_start():
    async def main():
        order = []

        async def run_task(task):
            order.append(("start", task.id))
            await asyncio.to_thread(time.sleep, 0.1)
            order.append(("end", task.id))
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(
            Task(id="a", role_id="coder", goal="x", path_claims=("shared.py",)),
            Task(id="b", role_id="coder", goal="y", path_claims=("shared.py",)),
        )
        await Scheduler(graph, run_task, max_concurrent_agents=2).run()
        return order

    order = asyncio.run(main())
    # b's start must come after a's end (or vice versa) — never interleaved.
    a_start, a_end = order.index(("start", "a")), order.index(("end", "a"))
    b_start, b_end = order.index(("start", "b")), order.index(("end", "b"))
    assert (a_end < b_start) or (b_end < a_start)


# --- failure policy: retry-then-block, independent work continues ----------
def test_failed_task_blocks_only_its_own_dependent_subtree():
    async def run_task(task):
        if task.id == "a":
            return TaskOutcome(task_id="a", ok=False, summary="boom", error="simulated")
        return _instant_ok(task)

    graph = _graph(
        Task(id="a", role_id="coder", goal="x"),
        Task(id="b", role_id="coder", goal="y", depends_on=("a",)),
        Task(id="independent", role_id="coder", goal="z"),
    )
    asyncio.run(Scheduler(graph, run_task).run())
    assert graph.tasks["a"].status == "failed"
    assert graph.tasks["b"].status == "blocked"
    assert graph.tasks["independent"].status == "done"


def test_scheduler_finishes_cleanly_when_everything_fails():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=False, summary="boom", error="nope")

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    result = asyncio.run(Scheduler(graph, run_task).run())
    assert graph.is_finished()
    assert not result.stopped_early


# --- budget: breach kills one task, reports why, others continue -----------
def test_budget_breach_fails_one_task_and_reports_why():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done", usage=Usage(total_tokens=60))

    graph = _graph(*(Task(id=f"t{i}", role_id="coder", goal="x") for i in range(3)))
    budget = BudgetGovernor(max_tokens_total=100)
    sched = Scheduler(graph, run_task, max_concurrent_agents=1, budget=budget)
    result = asyncio.run(sched.run())

    statuses = {tid: t.status for tid, t in graph.tasks.items()}
    assert "done" in statuses.values(), "at least one task should complete before the breach"
    assert "failed" in statuses.values(), "at least one task should be refused for budget"
    assert budget.spent_tokens_total > 0
    assert not result.stopped_early  # a clean stop, not a stall


def test_budget_exceeded_task_gets_an_explanatory_error():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done", usage=Usage(total_tokens=200))

    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    budget = BudgetGovernor(max_tokens_total=100)
    asyncio.run(Scheduler(graph, run_task, max_concurrent_agents=1, budget=budget).run())
    failed = [t for t in graph.tasks.values() if t.status == "failed"]
    assert len(failed) == 1  # exactly one task refused; the other already completed


# --- should_stop: cancels cleanly, promptly, nothing left dangling ---------
def test_should_stop_cancels_running_tasks_promptly():
    async def main():
        async def run_task(task):
            await asyncio.to_thread(time.sleep, 1.0)
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(*(Task(id=f"t{i}", role_id="coder", goal="x") for i in range(4)))
        stop_flag = {"go": False}
        sched = Scheduler(
            graph, run_task, max_concurrent_agents=2,
            should_stop=lambda: stop_flag["go"], should_stop_poll_interval=0.05,
        )

        async def stopper():
            await asyncio.sleep(0.1)
            stop_flag["go"] = True

        start = time.perf_counter()
        result, _ = await asyncio.gather(sched.run(), stopper())
        return result, time.perf_counter() - start

    result, elapsed = asyncio.run(main())
    assert result.stopped_early
    assert elapsed < 0.5, f"should_stop was not responsive (took {elapsed:.2f}s)"


def test_should_stop_leaves_nothing_in_running_state():
    async def main():
        async def run_task(task):
            await asyncio.to_thread(time.sleep, 0.5)
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(*(Task(id=f"t{i}", role_id="coder", goal="x") for i in range(4)))
        stop_flag = {"go": False}
        sched = Scheduler(
            graph, run_task, max_concurrent_agents=2,
            should_stop=lambda: stop_flag["go"], should_stop_poll_interval=0.05,
        )

        async def stopper():
            await asyncio.sleep(0.1)
            stop_flag["go"] = True

        await asyncio.gather(sched.run(), stopper())
        return {tid: t.status for tid, t in graph.tasks.items()}

    statuses = asyncio.run(main())
    assert "cancelled" in statuses.values()
    assert not any(s == "running" for s in statuses.values())


def test_should_stop_cancelled_tasks_release_their_leases():
    async def main():
        async def run_task(task):
            await asyncio.to_thread(time.sleep, 0.5)
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(Task(id="a", role_id="coder", goal="x", path_claims=("f.py",)))
        stop_flag = {"go": False}
        leases = LeaseManager()
        sched = Scheduler(
            graph, run_task, leases=leases,
            should_stop=lambda: stop_flag["go"], should_stop_poll_interval=0.05,
        )

        async def stopper():
            await asyncio.sleep(0.1)
            stop_flag["go"] = True

        await asyncio.gather(sched.run(), stopper())
        return leases

    leases = asyncio.run(main())
    assert leases.held_paths() == {}  # no orphaned lease left after cancellation


# --- blackboard integration --------------------------------------------
def test_completed_tasks_post_findings_to_the_blackboard():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary=f"{task.id} summary", refs=(f"{task.id}.py",))

    graph = _graph(Task(id="a", role_id="backend", goal="x"), Task(id="b", role_id="frontend", goal="y"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())

    findings = {f.task_id: f for f in sched.blackboard.all()}
    assert findings["a"].role == "backend"
    assert findings["a"].summary == "a summary"
    assert findings["a"].refs == ("a.py",)
    assert findings["b"].role == "frontend"


def test_blackboard_refs_covered_reflects_all_completed_tasks():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done", refs=(f"{task.id}.py",))

    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())
    assert sched.blackboard.refs_covered() == {"a.py", "b.py"}


def test_failed_tasks_still_post_a_finding():
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=False, summary="it broke", error="boom")

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    sched = Scheduler(graph, run_task)
    asyncio.run(sched.run())
    assert len(sched.blackboard.all()) == 1
    assert sched.blackboard.all()[0].summary == "it broke"


# --- stall detection vs. clean finish --------------------------------------
def test_graph_finished_via_budget_breach_is_not_reported_as_a_stall():
    """Regression: _launch_ready can itself finish the graph (the last
    ready task refused for budget) without anything ever launching that
    round — this is a clean, successful stop, not the "genuinely stuck"
    case stopped_early is meant to flag."""
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="done", usage=Usage(total_tokens=1000))

    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    budget = BudgetGovernor(max_tokens_total=1)
    sched = Scheduler(graph, run_task, max_concurrent_agents=1, budget=budget)
    result = asyncio.run(sched.run())
    assert graph.is_finished()
    assert not result.stopped_early


def test_result_elapsed_is_populated():
    async def run_task(task):
        return _instant_ok(task)

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    result = asyncio.run(Scheduler(graph, run_task).run())
    assert result.elapsed >= 0


# --- on_tick: the hook a live view (ui/live.py) redraws from ---------------
def test_on_tick_fires_during_a_normal_run():
    async def run_task(task):
        return _instant_ok(task)

    calls = []
    graph = _graph(Task(id="a", role_id="coder", goal="x"), Task(id="b", role_id="coder", goal="y"))
    asyncio.run(Scheduler(graph, run_task, on_tick=lambda: calls.append(1)).run())
    assert calls, "on_tick should fire at least once for a run that does real work"


def test_on_tick_can_read_live_scheduler_state_mid_run():
    # Two-phase setup: the closure needs a scheduler reference, but the
    # scheduler constructor needs the closure — `holder` breaks the cycle.
    holder: dict = {}
    seen_running = []

    def on_tick():
        statuses = {tid: t.status for tid, t in holder["scheduler"].graph.tasks.items()}
        if "running" in statuses.values():
            seen_running.append(statuses)

    async def run_task(task):
        await asyncio.to_thread(time.sleep, 0.1)
        return _instant_ok(task)

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    sched = Scheduler(graph, run_task, on_tick=on_tick, should_stop_poll_interval=0.02)
    holder["scheduler"] = sched
    result = asyncio.run(sched.run())

    assert seen_running, "on_tick should observe the task in a 'running' state before it completes"
    assert result.graph.tasks["a"].status == "done"


def test_on_tick_exception_does_not_crash_the_scheduler():
    async def run_task(task):
        return _instant_ok(task)

    def bad_tick():
        raise RuntimeError("boom — a rendering bug must not take the scheduler down")

    graph = _graph(Task(id="a", role_id="coder", goal="x"))
    result = asyncio.run(Scheduler(graph, run_task, on_tick=bad_tick).run())
    assert graph.all_ok()
    assert not result.stopped_early


def test_on_tick_fires_after_should_stop_cancellation_cleanup():
    async def main():
        async def run_task(task):
            await asyncio.to_thread(time.sleep, 1.0)
            return TaskOutcome(task_id=task.id, ok=True, summary="done")

        graph = _graph(*(Task(id=f"t{i}", role_id="coder", goal="x") for i in range(2)))
        stop_flag = {"go": False}
        calls = []
        sched = Scheduler(
            graph, run_task, max_concurrent_agents=2, on_tick=lambda: calls.append(1),
            should_stop=lambda: stop_flag["go"], should_stop_poll_interval=0.05,
        )

        async def stopper():
            await asyncio.sleep(0.1)
            stop_flag["go"] = True

        await asyncio.gather(sched.run(), stopper())
        return calls, graph

    calls, graph = asyncio.run(main())
    assert calls
    assert all(t.status == "cancelled" for t in graph.tasks.values())


# --- single-task control: the `x` (drop) and `R` (retry) keys ---------------
def test_request_cancel_drops_one_task_and_leaves_the_rest_running():
    """`x` on a lane must remove exactly that task, not stop the run."""
    started: list[str] = []

    async def run_task(task):
        started.append(task.id)
        if task.id == "slow":
            await asyncio.sleep(5)
        return TaskOutcome(task_id=task.id, ok=True, summary="ok")

    graph = TaskGraph.from_tasks([
        Task(id="slow", role_id="coder", goal="a"),
        Task(id="quick", role_id="coder", goal="b"),
    ])
    sched = Scheduler(graph, run_task, max_concurrent_agents=2)

    def cancel_once():
        if graph.tasks["slow"].status == "running":
            sched.request_cancel("slow")

    sched._on_tick = cancel_once
    result = asyncio.run(sched.run())

    assert graph.tasks["slow"].status == "cancelled"
    assert graph.tasks["quick"].status == "done"
    assert result.stopped_early is False, "dropping one task is not stopping the run"


def test_request_cancel_frees_the_lease_so_a_blocked_task_can_run():
    """The point of dropping a stuck task: whatever it was holding must be
    released, or the graph deadlocks on a path claim nobody will free."""
    async def run_task(task):
        if task.id == "hog":
            await asyncio.sleep(5)
        return TaskOutcome(task_id=task.id, ok=True, summary="ok")

    graph = TaskGraph.from_tasks([
        Task(id="hog", role_id="coder", goal="a", path_claims=("src/app.py",)),
        Task(id="waiter", role_id="coder", goal="b", path_claims=("src/app.py",)),
    ])
    sched = Scheduler(graph, run_task, max_concurrent_agents=2)

    def cancel_the_hog():
        if graph.tasks["hog"].status == "running":
            sched.request_cancel("hog")

    sched._on_tick = cancel_the_hog
    asyncio.run(sched.run())

    assert graph.tasks["hog"].status == "cancelled"
    assert graph.tasks["waiter"].status == "done", "lease was never released"
    assert sched.leases.conflicts_with_running(("src/app.py",)) is None


def test_request_retry_reruns_a_failed_task_and_unblocks_its_subtree():
    """mark_failed() blocks the whole descendant subtree. A retry that only
    reset the one task would leave dependents stuck at 'blocked' forever
    and the graph would finish with work silently skipped."""
    attempts: dict[str, int] = {}

    async def run_task(task):
        attempts[task.id] = attempts.get(task.id, 0) + 1
        ok = not (task.id == "flaky" and attempts[task.id] == 1)
        return TaskOutcome(task_id=task.id, ok=ok, summary="ok" if ok else "boom")

    graph = TaskGraph.from_tasks([
        Task(id="flaky", role_id="coder", goal="a"),
        Task(id="dependent", role_id="coder", goal="b", depends_on=("flaky",)),
    ])
    sched = Scheduler(graph, run_task)

    def retry_once():
        if graph.tasks["flaky"].status == "failed":
            sched.request_retry("flaky")

    sched._on_tick = retry_once
    asyncio.run(sched.run())

    assert attempts["flaky"] == 2, "the failed task should have been retried"
    assert graph.tasks["flaky"].status == "done"
    assert graph.tasks["dependent"].status == "done", "subtree stayed blocked after retry"


def test_control_requests_for_unknown_or_finished_tasks_are_ignored():
    """The key map addresses lanes by id; a stale id (or a lane that just
    finished) must not raise or corrupt state mid-run."""
    async def run_task(task):
        return TaskOutcome(task_id=task.id, ok=True, summary="ok")

    graph = TaskGraph.from_tasks([Task(id="only", role_id="coder", goal="a")])
    sched = Scheduler(graph, run_task)
    sched.request_cancel("does-not-exist")
    sched.request_retry("does-not-exist")
    result = asyncio.run(sched.run())

    assert graph.tasks["only"].status == "done"
    assert result.stopped_early is False

    sched.request_cancel("only")          # already done
    asyncio.run(sched._drain_control_requests({}))
    assert graph.tasks["only"].status == "done", "a finished task must not be re-marked"


def test_retry_is_refused_for_a_task_that_did_not_fail():
    graph = TaskGraph.from_tasks([Task(id="t1", role_id="coder", goal="a")])
    assert graph.reset_for_retry("t1") is False      # still 'ready'
    graph.mark_running("t1")
    assert graph.reset_for_retry("t1") is False      # running
    graph.mark_done("t1")
    assert graph.reset_for_retry("t1") is False      # done
    graph.mark_failed("t1")
    assert graph.reset_for_retry("t1") is True       # failed -> retryable
