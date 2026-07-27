"""Shell tools — run_command and background process management."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry

_PROCESSES: dict[str, subprocess.Popen] = {}
_SECRET_ENV_NAMES = frozenset({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY", "ZHIPUAI_API_KEY", "AWS_SECRET_ACCESS_KEY",
    "AZURE_OPENAI_KEY",
})


class RunCommandArgs(BaseModel):
    command: str = Field(description="Shell command to run")
    timeout: int | None = Field(default=30, description="Timeout in seconds")

def run_command(args: RunCommandArgs, ctx: ToolContext | None) -> ToolResult:
    decision = ctx.permissions.confirm("command", prompt_text=f"run: {args.command[:120]}")
    if not decision.approved:
        return ToolResult.error("Command was declined.", summary="command (declined)")
    env = dict(os.environ)
    for key in _SECRET_ENV_NAMES:
        env.pop(key, None)
    for name in list(env):
        if any(kw in name.lower() for kw in ("key", "secret", "token", "password", "credential")):
            env.pop(name, None)
    try:
        proc = subprocess.run(
            args.command, shell=True, cwd=ctx.project.root if ctx else None,
            capture_output=True, text=True, timeout=args.timeout or 30,
            env=env, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return ToolResult.error(f"Command timed out after {args.timeout}s.", summary="timeout")
    except OSError as exc:
        return ToolResult.error(str(exc), summary="command failed")
    output = ""
    if proc.stdout:
        output += proc.stdout
    if proc.stderr:
        if output:
            output += "\n"
        output += proc.stderr
    if not output:
        output = f"(exit {proc.returncode})"
    return ToolResult(ok=proc.returncode == 0, output=output.strip(),
                      summary=f"exit {proc.returncode}")


def register_run_command(reg: ToolRegistry) -> None:
    reg.add(Tool(name="run_command", description="Run a shell command (use for tests, builds, git)",
                 args_model=RunCommandArgs, func=run_command))


class RunBackgroundArgs(BaseModel):
    command: str = Field(description="Command to start in background")

def run_background(args: RunBackgroundArgs, ctx: ToolContext | None) -> ToolResult:
    pid = f"bg_{len(_PROCESSES) + 1}"
    try:
        proc = subprocess.Popen(
            args.command, shell=True, cwd=ctx.project.root if ctx else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as exc:
        return ToolResult.error(str(exc))
    _PROCESSES[pid] = proc
    return ToolResult(ok=True, output=f"Started background process {pid}: {args.command}", summary=f"bg {pid}")


def register_run_background(reg: ToolRegistry) -> None:
    reg.add(Tool(name="run_background", description="Start a long-running background process",
                 args_model=RunBackgroundArgs, func=run_background))


class CheckProcessArgs(BaseModel):
    id: str = Field(description="Process id from run_background")

def check_process(args: CheckProcessArgs, ctx: ToolContext | None) -> ToolResult:
    proc = _PROCESSES.get(args.id)
    if proc is None:
        return ToolResult.error(f"No such background process: {args.id}")
    rc = proc.poll()
    if rc is None:
        return ToolResult(ok=True, output=f"Process {args.id} is still running.", summary="running")
    stdout, stderr = proc.communicate()
    del _PROCESSES[args.id]
    output = f"Process {args.id} exited ({rc}).\n"
    if stdout:
        output += stdout + "\n"
    if stderr:
        output += stderr
    return ToolResult(ok=rc == 0, output=output.strip(), summary=f"bg {args.id} done ({rc})")


def register_check_process(reg: ToolRegistry) -> None:
    reg.add(Tool(name="check_process", description="Check a background process status",
                 args_model=CheckProcessArgs, func=check_process))


class StopProcessArgs(BaseModel):
    id: str = Field(description="Process id to stop")

def stop_process(args: StopProcessArgs, ctx: ToolContext | None) -> ToolResult:
    proc = _PROCESSES.get(args.id)
    if proc is None:
        return ToolResult.error(f"No such background process: {args.id}")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except OSError:
        pass
    del _PROCESSES[args.id]
    return ToolResult(ok=True, output=f"Stopped process {args.id}.", summary=f"stop {args.id}")


def register_stop_process(reg: ToolRegistry) -> None:
    reg.add(Tool(name="stop_process", description="Stop a background process",
                 args_model=StopProcessArgs, func=stop_process))


def register_background_check(reg: ToolRegistry) -> None:
    """Register only check_process (for reviewer registry)."""
    register_check_process(reg)


def register(reg: ToolRegistry) -> None:
    register_run_command(reg)
    register_run_background(reg)
    register_check_process(reg)
    register_stop_process(reg)
