"""Shared types for tools: the execution context and a structured result.

``ToolContext`` is the dependency-injection seam that makes every tool
independently testable — pass a context with a temp project root, a chosen
permission mode, and a console, then call the tool directly.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from relaycli.context import ProjectContext
from relaycli.permissions import PermissionManager


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, never truncating on failure.

    Writes to a temporary file in the same directory, fsyncs, then
    ``os.replace``s it into place — so a mid-write failure (ENOSPC, quota) or an
    interruption leaves the original file intact rather than truncated/partial.
    The destination's existing mode is preserved; new files honor the umask.
    """
    data = text.encode("utf-8")
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)

    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        current = os.umask(0)
        os.umask(current)
        mode = 0o666 & ~current

    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".relaytmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class ToolContext:
    """Everything a tool needs to run safely."""

    project: ProjectContext
    permissions: PermissionManager
    console: Console = field(default_factory=Console)
    read_files: set[str] = field(default_factory=set)
    require_read_before_edit: bool = False


@dataclass
class ToolResult:
    """Structured tool output.

    ``output`` is the text sent back to the model. ``summary`` is the short
    activity line shown to the human (e.g. ``edit api/users.py (+12 -3)``).
    """

    ok: bool
    output: str
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # what the agent sends back to the model
        return self.output

    @classmethod
    def error(cls, message: str, *, summary: str = "") -> "ToolResult":
        return cls(ok=False, output=message, summary=summary or message)
