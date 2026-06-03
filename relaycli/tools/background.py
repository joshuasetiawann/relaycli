"""Background process tools — start, inspect, and stop long-running commands.

run_command kills anything that outlives its timeout, so a dev server or
watcher can never run through it (observed live: ``npm run dev`` → timeout).
``run_background`` starts the command detached in its own session with output
(stdout+stderr merged) going to a 0600 log file in the system temp dir;
``check_process`` reads status + the log tail; ``stop_process`` kills the
process group.

``shell=True`` is deliberate and matches run_command: the model authors real
shell command lines (pipes, &&). The defense is the PermissionManager gate —
a human approves every command outside full-auto — plus the scrubbed
environment and process-group kill, not argument quoting.

Processes deliberately survive the RelayCLI session — a dev server should
outlive the chat that started it. The activity line and tool output carry
the pid and log path so the human always stays in control. The id registry
is per-process (module-level): ids from an earlier session are unknown, and
the error says how to deal with the process manually.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.run_command import _scrubbed_env, _short

_MAX_TAIL_BYTES = 16_384


@dataclass
class _BgProcess:
    id: str
    command: str
    proc: subprocess.Popen
    log_path: Path


_PROCESSES: dict[str, _BgProcess] = {}
_COUNTER = {"n": 0}


class BgArgs(BaseModel):
    command: str = Field(
        description=(
            "The long-running shell command to start in the background "
            "(dev server, watcher). Runs in the project root."
        )
    )


class CheckArgs(BaseModel):
    id: str = Field(description="Background process id returned by run_background (e.g. 'bg1').")
    tail: int = Field(default=40, ge=1, le=400, description="How many log lines to return.")


class StopArgs(BaseModel):
    id: str = Field(description="Background process id returned by run_background (e.g. 'bg1').")


def run_background(args: BgArgs, ctx: ToolContext) -> ToolResult:
    command = args.command.strip()
    if not command:
        return ToolResult.error("Empty command.", summary="bg run (empty)")

    # Same human-visible echo + permission gate as run_command.
    ctx.console.print(f"[bold]$[/bold] {escape(command)} [dim]&[/dim]")
    decision = ctx.permissions.confirm("command", prompt_text="Run this in the background?")
    if not decision.approved:
        return ToolResult.error(
            f"Command was not approved: {command}",
            summary=f"bg run {escape(_short(command))} (declined)",
        )

    fd, log_name = tempfile.mkstemp(prefix="relaycli-bg-", suffix=".log")  # 0600
    log_path = Path(log_name)
    popen_kwargs: dict = {
        "cwd": str(ctx.project.root),
        "stdout": fd,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": _scrubbed_env(),
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True  # detach: survives the session
    try:
        proc = subprocess.Popen(command, shell=True, **popen_kwargs)
    except OSError as exc:
        os.close(fd)
        return ToolResult.error(
            f"Failed to start command: {exc}",
            summary=f"bg run {escape(_short(command))} (error)",
        )
    finally:
        # The child holds its own copy of the fd; the parent must not leak it.
        try:
            os.close(fd)
        except OSError:
            pass

    _COUNTER["n"] += 1
    bg_id = f"bg{_COUNTER['n']}"
    _PROCESSES[bg_id] = _BgProcess(id=bg_id, command=command, proc=proc, log_path=log_path)

    return ToolResult(
        ok=True,
        output=(
            f"Started '{command}' in the background.\n"
            f"id: {bg_id}  pid: {proc.pid}  log: {log_path}\n"
            f"Use check_process(id='{bg_id}') to see its status and output; "
            f"stop_process(id='{bg_id}') to stop it. It keeps running after "
            f"this session ends."
        ),
        summary=f"bg run {escape(_short(command))} → {bg_id} (pid {proc.pid})",
        meta={"id": bg_id, "pid": proc.pid, "log": str(log_path)},
    )


def _tail(log_path: Path, lines: int) -> str:
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _MAX_TAIL_BYTES))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read log: {exc})"
    tail_lines = data.splitlines()[-lines:]
    return "\n".join(tail_lines) if tail_lines else "(no output yet)"


def check_process(args: CheckArgs, ctx: ToolContext) -> ToolResult:
    entry = _PROCESSES.get(args.id)
    if entry is None:
        return ToolResult.error(
            f"Unknown background process id '{args.id}' (this session started: "
            f"{', '.join(sorted(_PROCESSES)) or 'none'}). Processes from other "
            f"sessions must be managed manually (ps/kill).",
            summary=f"bg check {args.id} (unknown)",
        )
    code = entry.proc.poll()
    status = "running" if code is None else f"exited with code {code}"
    return ToolResult(
        ok=True,
        output=(
            f"{entry.id}: {status}\ncommand: {entry.command}\n"
            f"pid: {entry.proc.pid}  log: {entry.log_path}\n"
            f"--- last {args.tail} log lines ---\n{_tail(entry.log_path, args.tail)}"
        ),
        summary=f"bg check {entry.id} → {status}",
        meta={"id": entry.id, "returncode": code},
    )


def stop_process(args: StopArgs, ctx: ToolContext) -> ToolResult:
    entry = _PROCESSES.get(args.id)
    if entry is None:
        return ToolResult.error(
            f"Unknown background process id '{args.id}'.",
            summary=f"bg stop {args.id} (unknown)",
        )
    if entry.proc.poll() is not None:
        return ToolResult(
            ok=True,
            output=f"{entry.id} already exited with code {entry.proc.poll()}.",
            summary=f"bg stop {entry.id} (already exited)",
        )

    ctx.console.print(
        f"[bold]stop[/bold] {entry.id} [dim]{escape(_short(entry.command))}[/dim]"
    )
    decision = ctx.permissions.confirm(
        "command", prompt_text=f"Stop background process {entry.id}?"
    )
    if not decision.approved:
        return ToolResult.error(
            f"Stopping {entry.id} was not approved.",
            summary=f"bg stop {entry.id} (declined)",
        )

    try:
        if os.name == "posix":
            os.killpg(os.getpgid(entry.proc.pid), signal.SIGTERM)
        else:  # pragma: no cover - non-POSIX best effort
            entry.proc.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        entry.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(entry.proc.pid), signal.SIGKILL)
            else:  # pragma: no cover
                entry.proc.kill()
        except (OSError, ProcessLookupError):
            pass
        entry.proc.wait()

    return ToolResult(
        ok=True,
        output=f"Stopped {entry.id} (was: {entry.command}).",
        summary=f"bg stop {entry.id} (stopped)",
    )


RUN_NAME = "run_background"
RUN_DESCRIPTION = (
    "Start a LONG-RUNNING command (dev server, watcher) in the background and "
    "return an id + log path. Use this instead of run_command for anything "
    "that does not exit on its own. Requires approval like run_command."
)
CHECK_NAME = "check_process"
CHECK_DESCRIPTION = (
    "Check a background process started with run_background: running/exited "
    "state plus the last lines of its output log."
)
STOP_NAME = "stop_process"
STOP_DESCRIPTION = "Stop a background process started with run_background."


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=RUN_NAME, description=RUN_DESCRIPTION, args_model=BgArgs, func=run_background))
    reg.add(Tool(name=CHECK_NAME, description=CHECK_DESCRIPTION, args_model=CheckArgs, func=check_process))
    reg.add(Tool(name=STOP_NAME, description=STOP_DESCRIPTION, args_model=StopArgs, func=stop_process))


def register_check_only(reg: ToolRegistry) -> None:
    """For read-and-verify roles (reviewer/tester): status without control."""
    reg.add(Tool(name=CHECK_NAME, description=CHECK_DESCRIPTION, args_model=CheckArgs, func=check_process))
