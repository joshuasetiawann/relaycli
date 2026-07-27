from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry


def _git(args: list[str], cwd: Path | None) -> ToolResult:
    try:
        proc = subprocess.run(
            ["git"] + args, cwd=cwd,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return ToolResult.error("git not found on this system")
    except OSError as exc:
        return ToolResult.error(str(exc))
    except subprocess.TimeoutExpired:
        return ToolResult.error("git command timed out")
    output = proc.stdout.strip()
    if proc.stderr:
        output = output + "\n" + proc.stderr.strip() if output else proc.stderr.strip()
    if proc.returncode != 0:
        return ToolResult(ok=False, output=output or f"exit {proc.returncode}",
                          summary=f"git {args[0]} failed")
    return ToolResult(ok=True, output=output or f"(empty)", summary=f"git {args[0]}")


# Subcommands that are pure read/inspection — no flag combination for any
# of these mutates the repo or working tree, so they run without a
# confirmation prompt (matching read_file/list_dir/search). Everything
# else — including branch/remote/tag/stash/reflog, which all have
# destructive sub-modes (-D, delete, expire, drop, ...) that a naive
# first-word allowlist can't safely distinguish from their read-only
# forms — requires one, same as run_command.
_SAFE_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "blame", "describe",
    "rev-parse", "ls-files", "ls-tree", "cat-file", "shortlog",
})


class GitRunArgs(BaseModel):
    args: str = Field(description="Git arguments (e.g. 'status', 'log --oneline -5', 'diff --stat')")


def git_run(args: GitRunArgs, ctx: ToolContext | None) -> ToolResult:
    import shlex
    parsed = shlex.split(args.args)
    if not parsed:
        return ToolResult.error("No git subcommand given.")

    if parsed[0] not in _SAFE_GIT_SUBCOMMANDS and ctx is not None:
        decision = ctx.permissions.confirm(
            "command", prompt_text=f"run: git {args.args[:120]}"
        )
        if not decision.approved:
            return ToolResult.error(
                f"git command was not approved: git {args.args}",
                summary="git (declined)",
            )

    return _git(parsed, ctx.project.root if ctx else None)


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="git", description="Run any git command (status, log, diff, add, commit, branch, etc.)",
                 args_model=GitRunArgs, func=git_run))
