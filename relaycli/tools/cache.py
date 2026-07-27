"""Per-session file-content cache, keyed on (path, mtime, size).

Shared by read_file and search's pure-Python fallback so re-reading an
unchanged file — common once several tool calls or several concurrent
agents touch overlapping parts of a project — costs one disk read instead
of one per call.

Invalidation is two-layered: the (mtime, size) key makes a stale entry a
natural cache miss even if nothing ever calls invalidate() (any write
changes at least one of the two on essentially every real filesystem), and
the write tools additionally call invalidate() proactively to close the
narrow race where an edit doesn't change a file's size and lands inside the
filesystem's mtime resolution window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileCache:
    _entries: dict[Path, tuple[float, int, bytes]] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def read_bytes(self, path: Path) -> bytes:
        """Return path's contents, using a cached copy when the file's
        mtime and size still match what was cached. Raises OSError exactly
        as Path.read_bytes() would, on a missing or unreadable file."""
        stat = path.stat()
        cached = self._entries.get(path)
        if cached is not None and (cached[0], cached[1]) == (stat.st_mtime, stat.st_size):
            self.hits += 1
            return cached[2]
        self.misses += 1
        data = path.read_bytes()
        self._entries[path] = (stat.st_mtime, stat.st_size, data)
        return data

    def invalidate(self, path: Path) -> None:
        self._entries.pop(path, None)

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0
