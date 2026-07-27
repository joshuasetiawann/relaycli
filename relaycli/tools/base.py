"""Shared types: execution context and structured results."""

from __future__ import annotations

import functools
import inspect
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

from relaycli.core.context import ProjectContext
from relaycli.core.permissions import PermissionManager
from relaycli.tools.cache import FileCache

if TYPE_CHECKING:  # avoid an import cycle (tools.base -> agent -> ... -> tools.base)
    from relaycli.agent.budget import BudgetGovernor
    from relaycli.agent.leases import LeaseManager
    from relaycli.core.config import Settings
    from relaycli.core.llm import LLM

# Generous by design: a normal task rarely uses more than a few dozen tool
# calls even across several iterations. This is a backstop against a
# genuinely runaway loop, not a budget users are expected to bump into.
DEFAULT_MAX_CALLS_PER_SESSION = 300


class ToolCallLimitError(RuntimeError):
    """Raised when a session's tool-call budget is exhausted."""


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
    # Configurable per session: pass a higher number (or None to disable
    # entirely) when constructing a ToolContext for a task that genuinely
    # needs more calls than the generous default allows.
    max_calls_per_session: int | None = DEFAULT_MAX_CALLS_PER_SESSION
    calls_made: int = 0
    file_cache: FileCache = field(default_factory=FileCache)
    # Stage 3 (parallel orchestration, --experimental-parallel): set by the
    # scheduler for a task's ToolContext so write tools can enforce path
    # leases. None (the default, and every non-scheduled flow) means "not
    # running under the scheduler" — write tools skip the lease check
    # entirely, matching LeaseManager.check_write_allowed's own
    # task_id=None-always-passes contract.
    lease_manager: LeaseManager | None = None
    current_task_id: str | None = None
    # Only set for a ToolContext handed to a scheduler-run task's agent —
    # lets the spawn_agent tool construct a properly-configured child
    # Agent and charge its usage to the right budget. None everywhere else
    # (spawn_agent refuses cleanly rather than guessing a default).
    settings: Settings | None = None
    llm: LLM | None = None
    budget: BudgetGovernor | None = None
    spawn_depth: int = 0

    async def confirm_async(self, action: str, prompt_text: str) -> bool:
        """Async permission check — delegates to PermissionManager.confirm_async."""
        decision = await self.permissions.confirm_async(action, prompt_text=prompt_text)
        return decision.approved

    def lease_error(self, rel_path: str) -> str | None:
        """None if a write to rel_path is allowed; otherwise the message a
        write tool should return via ToolResult.error(...) ("fail loudly"
        — Part A §3.3 — rather than silently corrupting a file another
        task owns). A local import: agent.leases -> agent (package init)
        -> agent.loop -> ... circles back to this module, so this can't
        be a module-level import here (see git history for the exact
        cycle if this ever needs re-deriving)."""
        if self.lease_manager is None:
            return None
        from relaycli.agent.leases import LeaseError

        try:
            self.lease_manager.check_write_allowed(self.current_task_id, rel_path)
        except LeaseError as exc:
            return str(exc)
        return None


@dataclass
class DiffHunk:
    """One ``@@ ... @@`` hunk from a unified diff."""
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    added: int
    removed: int
    text: str


@dataclass
class FileDiff:
    """Structured diff for one file — lets a UI render files/hunks/+- counts
    directly instead of re-parsing ToolResult.output as text."""
    path: str
    added: int
    removed: int
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False


@dataclass
class ToolResult:
    ok: bool
    output: str
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    diff: list[FileDiff] | None = None

    def __str__(self) -> str:
        return self.output

    @classmethod
    def error(cls, message: str, *, summary: str = "") -> ToolResult:
        return cls(ok=False, output=message, summary=summary or message)


def _check_call_budget(ctx: "ToolContext | None") -> None:
    if ctx is None or ctx.max_calls_per_session is None:
        return
    if ctx.calls_made >= ctx.max_calls_per_session:
        raise ToolCallLimitError(
            f"This session has reached its tool-call limit "
            f"({ctx.max_calls_per_session}) — a guard against a runaway loop, "
            f"not a sign anything is wrong with this request. Start a new "
            f"session, or raise the limit by passing "
            f"max_calls_per_session=<n> (or None to disable it) when "
            f"constructing the ToolContext."
        )
    ctx.calls_made += 1


def rate_limited(func):
    """Enforce ``ctx.max_calls_per_session`` before dispatching a tool call.

    Works on both a sync ``(self, arguments, ctx=None)`` method and an
    ``async`` one — applied to :meth:`Tool.run`/:meth:`Tool.arun` in
    ``registry.py``, so every tool call is covered from one place rather
    than requiring each of the ~20 individual tool modules to opt in.
    """
    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(self, arguments, ctx=None, *args, **kwargs):
            _check_call_budget(ctx)
            return await func(self, arguments, ctx, *args, **kwargs)
        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(self, arguments, ctx=None, *args, **kwargs):
        _check_call_budget(ctx)
        return func(self, arguments, ctx, *args, **kwargs)
    return sync_wrapper
