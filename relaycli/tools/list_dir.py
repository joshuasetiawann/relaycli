"""list_dir tool — one directory level, read-only, no permission gate."""

from __future__ import annotations

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli.context import PathSafetyError
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

NAME = "list_dir"
DESCRIPTION = (
    "List one directory level: subdirectories (trailing '/') then files with "
    "sizes. Read-only. Use this instead of shelling out to ls."
)

_MAX_ENTRIES = 200


class ListDirArgs(BaseModel):
    path: str = Field(
        default=".", description="Directory path relative to the project root."
    )


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}K"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}M"
    return f"{n / (1024 * 1024 * 1024):.1f}G"


def list_dir(args: ListDirArgs, ctx: ToolContext) -> ToolResult:
    proj = ctx.project
    try:
        path = proj.resolve(args.path, must_exist=True)
    except PathSafetyError as exc:
        return ToolResult.error(str(exc), summary=f"list {args.path} (refused)")

    if not path.is_dir():
        return ToolResult.error(
            f"'{args.path}' is not a directory.", summary=f"list {args.path} (refused)"
        )

    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return ToolResult.error(f"Could not list '{args.path}': {exc}")

    rel = proj.relative(path)
    lines = []
    for entry in entries[:_MAX_ENTRIES]:
        if entry.is_dir():
            lines.append(f"{entry.name}/")
        else:
            try:
                lines.append(f"{entry.name}  ({_fmt_size(entry.stat().st_size)})")
            except OSError:
                lines.append(entry.name)
    if len(entries) > _MAX_ENTRIES:
        lines.append(f"[... {len(entries) - _MAX_ENTRIES} more entries not shown ...]")
    if not lines:
        lines = ["(empty directory)"]

    return ToolResult(
        ok=True,
        output=f"{rel}:\n" + "\n".join(lines),
        summary=f"list {escape(rel)} ({len(entries)} entries)",
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=ListDirArgs, func=list_dir))
