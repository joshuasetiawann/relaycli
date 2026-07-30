"""Path leases — the mechanism that makes concurrent agents safe.

Two agents editing one file corrupts it. Rather than hoping agents don't
collide, the scheduler refuses to co-schedule tasks with overlapping
path_claims (serializing them instead), and every write tool checks the
lease and fails loudly without one — so even a task that should have had
exclusive access but doesn't (a scheduler bug, or a tool used outside a
scheduled task entirely) can't silently write to a path another task owns.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import PurePosixPath


class LeaseError(RuntimeError):
    """A write was attempted on a path with no held lease, or a lease was
    requested for a path another task already holds."""


@dataclass(frozen=True)
class Lease:
    task_id: str
    paths: tuple[str, ...]


def _normalize(path: str) -> str:
    """POSIX-style, no leading './', for consistent glob matching
    regardless of how a caller spelled the same path."""
    return str(PurePosixPath(path.replace("\\", "/")))


def claims_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether any glob in `a` could match a path any glob in `b` could
    also match. Conservative by design (a false "overlap" just means two
    tasks serialize instead of running concurrently — safe; a false
    "no overlap" would let two tasks write the same file, which isn't):
    two patterns are treated as overlapping unless proven disjoint by a
    literal (no-wildcard) prefix mismatch.
    """
    for pa in a:
        na = _normalize(pa)
        for pb in b:
            nb = _normalize(pb)
            if fnmatch.fnmatch(na, nb) or fnmatch.fnmatch(nb, na):
                return True
            if _literal_prefix_disjoint(na, nb):
                continue
            return True
    return False


def _literal_prefix_disjoint(a: str, b: str) -> bool:
    """True only when both patterns' portion before their first wildcard
    differs enough that no string could match both — e.g. "src/api/**" vs
    "docs/**" (disjoint) but not "src/**" vs "src/api/**" (one is a
    prefix of the other, so they could still collide)."""
    pa = a.split("*", 1)[0]
    pb = b.split("*", 1)[0]
    return not pa.startswith(pb) and not pb.startswith(pa)


@dataclass
class LeaseManager:
    """Tracks which task holds which paths. Not thread/asyncio-safe on its
    own beyond simple dict operations being atomic under the GIL — the
    scheduler is the only writer, calling from a single coroutine at a
    time between awaits, which is sufficient here."""

    _held: dict[str, Lease] = field(default_factory=dict)  # task_id -> Lease

    def conflicts_with_running(self, path_claims: tuple[str, ...]) -> str | None:
        """The task_id of a currently-held lease that overlaps
        path_claims, or None if it's safe to acquire. Doesn't acquire —
        callers check this before deciding a task is actually runnable
        this round, separate from whether its graph dependencies are met."""
        for lease in self._held.values():
            if claims_overlap(path_claims, lease.paths):
                return lease.task_id
        return None

    def acquire(self, task_id: str, path_claims: tuple[str, ...]) -> Lease:
        conflict = self.conflicts_with_running(path_claims)
        if conflict is not None:
            raise LeaseError(
                f"task '{task_id}' cannot acquire {path_claims}: "
                f"overlaps a lease already held by task '{conflict}'"
            )
        lease = Lease(task_id=task_id, paths=path_claims)
        self._held[task_id] = lease
        return lease

    def paths_held_by(self, task_id: str) -> tuple[str, ...]:
        """The path globs `task_id`'s lease covers, or () if it holds none.
        The UI needs this to name *which* path a waiting lane is contending
        for — holder_of() answers the opposite question (which task owns a
        concrete path) and cannot match a claim that is itself a glob."""
        lease = self._held.get(task_id)
        return lease.paths if lease is not None else ()

    def release(self, task_id: str) -> None:
        self._held.pop(task_id, None)

    def holder_of(self, path: str) -> str | None:
        """Which task's lease covers `path`, if any — for the UI's "held
        by" line and for write tools to check before writing."""
        normalized = _normalize(path)
        for lease in self._held.values():
            if any(fnmatch.fnmatch(normalized, _normalize(p)) for p in lease.paths):
                return lease.task_id
        return None

    def check_write_allowed(self, task_id: str | None, path: str) -> None:
        """Raise LeaseError unless `task_id` holds a lease covering
        `path`. task_id=None means "no active task" (e.g. the plain
        single-agent flow, which never goes through the scheduler) —
        always allowed, since leases only govern scheduled, concurrent
        execution."""
        if task_id is None:
            return
        holder = self.holder_of(path)
        if holder is None:
            raise LeaseError(
                f"task '{task_id}' tried to write '{path}' without a lease covering it. "
                f"path_claims must include this path before the task starts."
            )
        if holder != task_id:
            raise LeaseError(
                f"task '{task_id}' tried to write '{path}', but task '{holder}' holds the lease."
            )

    def held_paths(self) -> dict[str, tuple[str, ...]]:
        return {tid: lease.paths for tid, lease in self._held.items()}
