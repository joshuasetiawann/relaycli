"""Branch + dirty-file count for the live frame's status bar.

The design's status bar carries `git:feat/lease-queue ±3` (§3's hero
screen), which is two `git` invocations. The live frame repaints at up to
15fps, so this caches: without a TTL it would fork git thirty times a
second to redraw a string that changes maybe once a minute.

Everything here fails soft. A status bar that cannot answer "which
branch" should say nothing about branches, not take the run down — this
runs inside a render path, and no repository question is worth a
traceback over a working agent run.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# Long enough that a 15fps repaint costs nothing, short enough that the
# count still tracks the agents' own edits while you watch them work.
CACHE_TTL_S = 2.0
_TIMEOUT_S = 1.0


@dataclass(frozen=True)
class RepoStatus:
    """`branch` is None outside a repository (or when git is unavailable),
    which the status bar renders by simply omitting the whole git field."""

    branch: str | None = None
    dirty: int = 0


_cache: dict[str, tuple[float, RepoStatus]] = {}


def _git(root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def read_status(root: Path) -> RepoStatus:
    """Uncached read — the tests' entry point, and what `status` calls on
    a cache miss."""
    branch = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch is None:
        return RepoStatus()
    porcelain = _git(root, ["status", "--porcelain"]) or ""
    dirty = sum(1 for line in porcelain.splitlines() if line.strip())
    return RepoStatus(branch=branch.strip() or None, dirty=dirty)


def status(root: Path, *, now: float | None = None) -> RepoStatus:
    """Cached for CACHE_TTL_S per root. `now` is injectable so a test can
    step over the TTL without sleeping."""
    key = str(root)
    moment = time.monotonic() if now is None else now
    cached = _cache.get(key)
    if cached is not None and moment - cached[0] < CACHE_TTL_S:
        return cached[1]
    fresh = read_status(root)
    _cache[key] = (moment, fresh)
    return fresh


def clear_cache() -> None:
    """Drop every cached entry — for tests, which must not inherit another
    test's repository state."""
    _cache.clear()
