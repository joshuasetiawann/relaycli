"""Blackboard — the token-cost fix for concurrent agents.

Without it, four agents each reading the same six files pays for that
content four times. With it, whichever agent reads a file first posts a
brief; the others read the brief instead of the file. Expected to matter
more than any other optimization in Part A on a real codebase (§3.4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    task_id: str
    role: str
    kind: str  # e.g. "file_survey", "test_result", "note"
    summary: str
    refs: tuple[str, ...] = ()
    posted_at: float = field(default_factory=time.time)


@dataclass
class Blackboard:
    """Append-only. Nothing is ever removed or edited — a stale finding is
    superseded by a newer one on the same topic, not overwritten, so the
    full history stays inspectable (useful for the UI's transcript and for
    debugging why an agent made a given decision)."""

    _findings: list[Finding] = field(default_factory=list)

    def post(self, finding: Finding) -> None:
        self._findings.append(finding)

    def all(self) -> list[Finding]:
        return list(self._findings)

    def by_kind(self, kind: str) -> list[Finding]:
        return [f for f in self._findings if f.kind == kind]

    def by_task(self, task_id: str) -> list[Finding]:
        return [f for f in self._findings if f.task_id == task_id]

    def refs_covered(self) -> set[str]:
        """Every path any finding has already reported on — the read side
        of the token-cost fix: a task about to read_file first checks
        whether this set already covers the path, and if so reads the
        existing finding's summary instead of the file itself."""
        out: set[str] = set()
        for f in self._findings:
            out.update(f.refs)
        return out

    def find_covering(self, ref: str) -> Finding | None:
        """The most recent finding whose refs include `ref`, if any."""
        for finding in reversed(self._findings):
            if ref in finding.refs:
                return finding
        return None
