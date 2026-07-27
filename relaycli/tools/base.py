"""Shared types: execution context and structured results."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from relaycli.core.context import ProjectContext
from relaycli.core.permissions import PermissionManager


def atomic_write(path: Path, text: str) -> None:
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
    project: ProjectContext
    permissions: PermissionManager
    console: Console = field(default_factory=Console)
    read_files: set[str] = field(default_factory=set)
    require_read_before_edit: bool = False

    async def confirm_async(self, action: str, prompt_text: str) -> bool:
        """Async permission check — delegates to PermissionManager.confirm_async."""
        decision = await self.permissions.confirm_async(action, prompt_text=prompt_text)
        return decision.approved


@dataclass
class ToolResult:
    ok: bool
    output: str
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.output

    @classmethod
    def error(cls, message: str, *, summary: str = "") -> ToolResult:
        return cls(ok=False, output=message, summary=summary or message)
