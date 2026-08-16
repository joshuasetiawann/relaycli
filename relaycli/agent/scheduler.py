"""Scheduler — runs a TaskGraph's ready tasks concurrently.

Agent.run() is still synchronous (Stage 2 added escalation to it without
making the whole loop async — see agent/router.py's call_with_escalation_
sync docstring). Real concurrency for network-bound model calls still
needs each one off the event loop thread, so tasks run via
asyncio.to_thread rather than requiring Agent itself to be async yet.

Loop: compute ready tasks (graph dependencies satisfied AND no lease
conflict with anything currently running), launch up to
max_concurrent_agents, wait for the next one to finish, post its outcome
to the graph/leases/blackboard/budget, refill. Stops when the graph is
finished, the budget is exhausted, or should_stop() returns true.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from relaycli.agent.blackboard import Blackboard, Finding
from relaycli.agent.budget import BudgetExceeded, BudgetGovernor
from relaycli.agent.graph import Task, TaskGraph
from relaycli.agent.leases import LeaseManager
from relaycli.core.llm import Usage
from relaycli.core.logging import get_logger

_log = get_logger(__name__)

# Statuses a control request must not overwrite (a task that already
# finished can't be "cancelled" after the fact).
_FINISHED_STATES = frozenset({"done", "failed", "cancelled"})


@dataclass
class TaskOutcome:
    task_id: str
    ok: bool
    summary: str
    usage: Usage = field(default_factory=Usage)
    error: str | None = None
    # Paths this task's agent read (typically ToolContext.read_files) —
    # posted to the blackboard as the finding's refs, so a later task can
    # tell this path was already surveyed instead of reading it again.
    refs: tuple[str, ...] = ()


TaskRunner = Callable[[Task], Awaitable[TaskOutcome]]


@dataclass
class SchedulerResult:
    graph: TaskGraph
    blackboard: Blackboard
    budget: BudgetGovernor
    outcomes: dict[str, TaskOutcome] = field(default_factory=dict)
    stopped_early: bool = False
    elapsed: float = 0.0


class Scheduler:
    def __init__(
        self,
        graph: TaskGraph,
        run_task: TaskRunner,
        *,
        max_concurrent_agents: int = 3,
        leases: LeaseManager | None = None,
        blackboard: Blackboard | None = None,
        budget: BudgetGovernor | None = None,
        should_stop: Callable[[], bool] | None = None,
        should_stop_poll_interval: float = 0.2,
        on_tick: Callable[[], None] | None = None,
        steer: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.graph = graph
        self._run_task = run_task
        self.max_concurrent_agents = max(1, max_concurrent_agents)
        self.leases = leases if leases is not None else LeaseManager()
        self.blackboard = blackboard if blackboard is not None else Blackboard()
        self.budget = budget if budget is not None else BudgetGovernor()
        self._should_stop = should_stop
        self._should_stop_poll_interval = should_stop_poll_interval
        self._on_tick = on_tick
        self._steer = steer
        # Public, like graph/budget/leases — on_tick's whole point is
        # letting a live view read state mid-run, and per-task usage/cost
        # (outcomes) plus per-task elapsed time (task_started_at) aren't
        # otherwise visible anywhere: Agent.run() only returns a usage
        # total at completion, nothing incremental.
        self.outcomes: dict[str, TaskOutcome] = {}
        self.task_started_at: dict[str, float] = {}
        self.task_ended_at: dict[str, float] = {}
        # Single-task control (ui/live.py's `x` and `R` keys). Requests are
        # queued rather than applied where they're made: the caller is a key
        # reader on another thread, and cancelling an asyncio.Task or moving
        # a graph node from under a running loop is only safe on the loop's
        # own thread. run() drains these once per iteration.
        self._control_lock = threading.Lock()
        self._cancel_requests: set[str] = set()
        self._retry_requests: set[str] = set()

    # -- single-task control (thread-safe; applied by run()) ----------------
    def request_cancel(self, task_id: str) -> None:
        """Ask for one task to be dropped. Safe from any thread, safe to
        call for an unknown or already-finished task (it's ignored)."""
        with self._control_lock:
            self._cancel_requests.add(task_id)

    def request_retry(self, task_id: str) -> None:
        """Ask for one failed/cancelled task to be queued again. Safe from
        any thread; ignored if the task isn't in a retryable state.

        Only effective while the run is still live. Once every task has
        reached a terminal state the loop exits and returns its result, so
        a retry requested after that has nothing left to process — in
        practice you retry a failed lane while its siblings are still
        working, which is exactly when the key is reachable anyway. (Found
        by an end-to-end pty test whose first version pressed the key one
        task too late.)"""
        with self._control_lock:
            self._retry_requests.add(task_id)

    def request_steer(self, task_id: str, note: str) -> bool:
        """Hand a mid-run instruction to one task's agent.

        Unlike cancel and retry this is *not* queued for run() to apply:
        it changes nothing the scheduler owns — no graph node, no lease,
        no future — so there is nothing that has to happen on the loop's
        own thread. It goes straight to the agent, whose own inbox lock
        makes the call safe from the key-reader thread.

        Returns whether it was delivered. False covers every honest miss
        — no steer sink wired up, an unknown task, a task that is not
        running right now, an empty note — and the caller is expected to
        say so rather than let the key look like it worked.
        """
        if self._steer is None:
            return False
        return self._steer(task_id, note)

    async def _drain_control_requests(self, running: dict[str, asyncio.Task]) -> None:
        """Apply queued cancel/retry requests. Async because a cancelled
        future still has to be awaited — dropping it without reaping it
        leaves asyncio complaining that a task was destroyed while pending.

        Cancelling only detaches the task from the scheduler: the work runs
        in a thread (asyncio.to_thread, since Agent.run is synchronous) and
        a Python thread cannot be killed, so the agent finishes its current
        model call before it actually goes away. Its result is discarded and
        its lease is freed immediately, which is what unblocks the graph.
        The pre-existing stopped_early path has the same property."""
        with self._control_lock:
            cancels = self._cancel_requests
            retries = self._retry_requests
            self._cancel_requests = set()
            self._retry_requests = set()

        cancelled_futures = []
        for task_id in cancels:
            if task_id not in self.graph.tasks:
                continue
            future = running.pop(task_id, None)
            if future is not None:
                future.cancel()
                cancelled_futures.append(future)
                self.task_ended_at[task_id] = time.perf_counter()
            # Release the lease whether or not it was running: a task
            # cancelled between acquire and launch would otherwise hold a
            # path claim nothing will ever release, stalling the graph.
            self.leases.release(task_id)
            if self.graph.tasks[task_id].status not in _FINISHED_STATES:
                self.graph.mark_cancelled(task_id)
                _log.info("task '%s' cancelled on request", task_id)

        if cancelled_futures:
            await asyncio.gather(*cancelled_futures, return_exceptions=True)

        for task_id in retries:
            if task_id in self.graph.tasks and self.graph.reset_for_retry(task_id):
                self.outcomes.pop(task_id, None)
                self.task_started_at.pop(task_id, None)
                self.task_ended_at.pop(task_id, None)
                _log.info("task '%s' queued for retry", task_id)

    def _has_pending_retries(self) -> bool:
        """Whether a retry is queued but not yet applied. run()'s loop
        condition consults this: a retry un-finishes the graph, so a request
        queued by the last iteration's on_tick would otherwise be dropped
        when the while check saw every task in a terminal state and exited.
        Cancels need no such guard — they can only finish a graph sooner."""
        with self._control_lock:
            return bool(self._retry_requests)

    def _tick(self) -> None:
        """Fired once per loop iteration (see run(), via try/finally, so
        every break/continue path still ticks) and once more after
        stopped_early cleanup — a live view (ui/live.py) reads self.graph /
        self.budget / self.leases from here to redraw. Exceptions from a
        misbehaving callback must not take the scheduler down with them."""
        if self._on_tick is None:
            return
        try:
            self._on_tick()
        except Exception:
            _log.exception("on_tick callback raised; ignoring")

    async def run(self) -> SchedulerResult:
        started = time.perf_counter()
        running: dict[str, asyncio.Task] = {}
        stopped_early = False

        while not self.graph.is_finished() or self._has_pending_retries():
            # try/finally, not a tick() call at every break/continue site:
            # on_tick must fire once per iteration on *every* path through
            # this body (early stall, budget breach, plain timeout, a
            # completion) without duplicating the call at each exit.
            try:
                if self._should_stop is not None and self._should_stop():
                    stopped_early = True
                    break

                # Before launching: a cancel may free a lease that lets
                # something else start this same round, and a retry may add
                # a newly-ready task.
                await self._drain_control_requests(running)

                launched_this_round = self._launch_ready(running)

                if self.graph.is_finished():
                    # _launch_ready can itself finish the graph (e.g. a budget
                    # breach marks the last ready task failed without ever
                    # launching it) — that's a normal, successful stop, not a
                    # stall, so the outer while's own condition would already
                    # exit next iteration; breaking now just skips a needless
                    # asyncio.wait() with nothing running.
                    break

                if not running:
                    if not launched_this_round and not self.graph.ready_tasks():
                        # Nothing running, nothing ready, graph genuinely not
                        # finished: every remaining task is blocked/waiting on
                        # something that will never resolve (e.g. a lease held
                        # by a task that already finished but wasn't released —
                        # a bug, not a normal state) rather than genuinely stuck.
                        _log.warning("scheduler stalled: nothing running or ready, "
                                     "but the graph isn't finished")
                        stopped_early = True
                    break

                # Bounded wait, not an unbounded FIRST_COMPLETED: should_stop is
                # only rechecked at the top of this loop, so if every running
                # task takes longer than this timeout, an unbounded wait would
                # make a stop request (Ctrl-C) sit unnoticed until some task
                # finishes on its own — found by a test asserting cancellation
                # actually happens promptly, not just eventually. The same
                # timeout doubles as on_tick's cadence (default 0.2s = 5Hz,
                # comfortably under the design's 15fps redraw ceiling).
                done, _pending = await asyncio.wait(
                    running.values(), return_when=asyncio.FIRST_COMPLETED,
                    timeout=self._should_stop_poll_interval,
                )
                if not done:
                    continue  # nothing finished within the poll window; recheck should_stop
                for fut in done:
                    task_id = next(tid for tid, t in running.items() if t is fut)
                    del running[task_id]
                    outcome = await fut
                    self.outcomes[task_id] = outcome
                    self.task_ended_at[task_id] = time.perf_counter()
                    self.leases.release(task_id)
                    try:
                        self.budget.record(task_id, outcome.usage)
                    except BudgetExceeded as exc:
                        # The task itself already ran and its outcome stands —
                        # a breach discovered on completion doesn't retroactively
                        # undo work that already happened. Its only effect is on
                        # what gets to launch *next* (check_before_launch, in
                        # _launch_ready) — logged here so the reason is visible.
                        _log.warning("%s", exc)
                    self.blackboard.post(Finding(
                        task_id=task_id, role=self.graph.tasks[task_id].role_id,
                        kind="task_result", summary=outcome.summary, refs=outcome.refs,
                    ))
                    if outcome.ok:
                        self.graph.mark_done(task_id)
                    else:
                        self.graph.mark_failed(task_id)
                        _log.warning("task '%s' failed: %s", task_id, outcome.error or outcome.summary)
            finally:
                self._tick()

        if stopped_early:
            for task_id, fut in running.items():
                fut.cancel()
                self.leases.release(task_id)
                self.graph.mark_cancelled(task_id)
                self.task_ended_at[task_id] = time.perf_counter()
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)
            self._tick()  # the loop's per-iteration tick predates this cleanup;
            # without this, a live view's last frame would miss the cancellations.

        return SchedulerResult(
            graph=self.graph, blackboard=self.blackboard, budget=self.budget,
            outcomes=self.outcomes, stopped_early=stopped_early,
            elapsed=time.perf_counter() - started,
        )

    def _launch_ready(self, running: dict[str, asyncio.Task]) -> bool:
        """Start as many ready, lease-clear, budget-clear tasks as the
        concurrency cap allows. Returns whether anything was launched this
        round, so the caller can distinguish "waiting on leases/budget"
        from "genuinely nothing left to do"."""
        launched = False
        for task in self.graph.ready_tasks():
            if len(running) >= self.max_concurrent_agents:
                break
            if task.id in running:
                continue
            if self.leases.conflicts_with_running(task.path_claims) is not None:
                continue  # stays ready; retried next round once the holder finishes
            try:
                self.budget.check_before_launch(task.id)
            except BudgetExceeded as exc:
                _log.warning("task '%s' cannot start: %s", task.id, exc)
                self.graph.mark_failed(task.id)
                continue
            self.leases.acquire(task.id, task.path_claims)
            self.graph.mark_running(task.id)
            self.task_started_at[task.id] = time.perf_counter()
            running[task.id] = asyncio.ensure_future(self._run_task(task))
            launched = True
        return launched
