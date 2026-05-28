"""run_command tool — run a shell command in the project root, mode-gated."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Any

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

NAME = "run_command"
DESCRIPTION = (
    "Run a shell command in the project root and return its stdout, stderr and "
    "exit code. Requires approval according to the permission mode."
)

_MAX_OUTPUT_BYTES = 20_000  # bytes of EACH stream captured/returned to the model

# Provider credentials RelayCLI manages: scrubbed from a spawned command's
# environment so an (injected) command like `env` / `printenv` cannot read
# them back and ship them to the model. Kept deliberately narrow so legitimate
# tokens a task may need (GITHUB_TOKEN, AWS_*, etc.) are left intact.
_SENSITIVE_ENV: frozenset[str] = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
        # Providers whose keys the web UI can set at runtime (LiteLLM reads
        # these from the env); scrub them so a spawned command can't read back.
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ZHIPUAI_API_KEY",
        "RELAYCLI_OPENAI_API_KEY",
        "RELAYCLI_ANTHROPIC_API_KEY",
        "RELAYCLI_GEMINI_API_KEY",
        "RELAYCLI_GROQ_API_KEY",
        "RELAYCLI_MISTRAL_API_KEY",
        "RELAYCLI_OPENROUTER_API_KEY",
    }
)


class RunCommandArgs(BaseModel):
    command: str = Field(description="The shell command to run, executed in the project root.")
    timeout: int = Field(
        default=120, ge=1, le=1800, description="Seconds before the command is killed."
    )


def run_command(args: RunCommandArgs, ctx: ToolContext) -> ToolResult:
    command = args.command.strip()
    if not command:
        return ToolResult.error("Empty command.", summary="run (empty)")

    # Show the command (escaped so a model-crafted string cannot inject Rich
    # markup and hide part of itself from the human approver).
    ctx.console.print(f"[bold]$[/bold] {escape(command)}")

    decision = ctx.permissions.confirm("command", prompt_text="Run this command?")
    if not decision.approved:
        return ToolResult.error(
            f"Command was not approved: {command}",
            summary=f"run {escape(_short(command))} (declined)",
        )

    try:
        returncode, stdout, stderr, truncated = _execute_shell(
            command, str(ctx.project.root), args.timeout
        )
    except subprocess.TimeoutExpired:
        return ToolResult.error(
            f"Command timed out after {args.timeout}s: {command}",
            summary=f"run {escape(_short(command))} (timeout)",
        )
    except OSError as exc:
        return ToolResult.error(
            f"Failed to start command: {exc}", summary=f"run {escape(_short(command))} (error)"
        )

    output = _format_output(stdout, stderr, returncode, truncated)
    return ToolResult(
        ok=(returncode == 0),
        output=output,
        summary=f"run {escape(_short(command))} → exit {returncode}",
        meta={"returncode": returncode, "truncated": truncated},
    )


def _scrubbed_env() -> dict[str, str]:
    """A copy of the environment with RelayCLI's provider keys removed."""
    return {k: v for k, v in os.environ.items() if k.upper() not in _SENSITIVE_ENV}


def _kill_process_group(proc: "subprocess.Popen[bytes]") -> None:
    """SIGKILL the command's whole process group (POSIX) so backgrounded /
    piped children don't survive the timeout; fall back to killing the leader."""
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX best effort
            proc.kill()
    except (OSError, ProcessLookupError):
        pass


def _execute_shell(command: str, cwd: str, timeout: int) -> tuple[int, str, str, bool]:
    """Run ``command`` via the shell in its own process group.

    Returns ``(returncode, stdout, stderr, truncated)``. Output is drained
    concurrently with a hard per-stream byte cap so a runaway command (e.g.
    ``yes``) cannot exhaust memory; on cap-overflow or timeout the whole
    process group is killed so orphaned children don't outlive RelayCLI.
    """
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": _scrubbed_env(),
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True  # own process group -> killpg works

    proc = subprocess.Popen(command, shell=True, **popen_kwargs)

    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = {"hit": False}
    lock = threading.Lock()

    def drain(name: str, stream) -> None:
        try:
            for chunk in iter(lambda: stream.read(4096), b""):
                stop = False
                with lock:
                    buf = buffers[name]
                    if len(buf) < _MAX_OUTPUT_BYTES:
                        buf.extend(chunk[: _MAX_OUTPUT_BYTES - len(buf)])
                    if len(buf) >= _MAX_OUTPUT_BYTES:
                        if not overflow["hit"]:
                            overflow["hit"] = True
                            stop = True
                if stop:
                    # Tear the group down now so a blocked writer unblocks and
                    # the process actually exits (don't wait for the timeout).
                    _kill_process_group(proc)
                    break
        finally:
            try:
                stream.close()
            except OSError:
                pass

    threads = [
        threading.Thread(target=drain, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", proc.stderr), daemon=True),
    ]
    for t in threads:
        t.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        proc.wait()

    for t in threads:
        t.join(timeout=2)

    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout)

    stdout = buffers["stdout"].decode("utf-8", errors="replace")
    stderr = buffers["stderr"].decode("utf-8", errors="replace")
    return proc.returncode or 0, stdout, stderr, overflow["hit"]


def _format_output(stdout: str, stderr: str, returncode: int, truncated: bool = False) -> str:
    parts = [f"exit code: {returncode}"]
    if stdout.strip():
        parts.append("stdout:\n" + stdout.rstrip())
    if stderr.strip():
        parts.append("stderr:\n" + stderr.rstrip())
    if not stdout.strip() and not stderr.strip():
        parts.append("(no output)")
    if truncated:
        parts.append(f"[... output truncated at {_MAX_OUTPUT_BYTES} bytes per stream ...]")
    return "\n".join(parts)


def _short(command: str, limit: int = 60) -> str:
    one_line = " ".join(command.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=RunCommandArgs, func=run_command))
