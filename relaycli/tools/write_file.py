"""write_file tool — create or overwrite a file via the diff + permission flow."""

from __future__ import annotations

from pydantic import BaseModel, Field

from rich.markup import escape

from relaycli.context import PathSafetyError
from relaycli.render import render_diff
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult, atomic_write

NAME = "write_file"
DESCRIPTION = (
    "Create a new file or overwrite an existing one with the given content. "
    "Always shows a diff and requires approval according to the permission mode."
)


class WriteFileArgs(BaseModel):
    path: str = Field(description="File path relative to the project root.")
    content: str = Field(description="The full new contents of the file.")


def write_file(args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
    proj = ctx.project
    try:
        path = proj.resolve(args.path)
    except PathSafetyError as exc:
        return ToolResult.error(str(exc), summary=f"write {args.path} (refused)")

    if path.exists() and not path.is_file():
        return ToolResult.error(
            f"'{args.path}' exists and is not a regular file.",
            summary=f"write {args.path} (refused)",
        )

    old = ""
    if path.is_file():
        try:
            old = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.error(f"Could not read existing '{args.path}': {exc}")

    new = args.content
    rel = proj.relative(path)

    if old == new and path.exists():
        return ToolResult(ok=True, output=f"No changes; '{rel}' already has that content.",
                          summary=f"write {rel} (no change)")

    # Always show the diff before applying (in every mode).
    added, removed = render_diff(ctx.console, old, new, rel)

    verb = "Overwrite" if path.exists() else "Create"
    decision = ctx.permissions.confirm("write", prompt_text=f"{verb} {escape(rel)}?")
    if not decision.approved:
        return ToolResult.error(
            f"Write to '{rel}' was declined.", summary=f"write {rel} (declined)"
        )

    try:
        atomic_write(path, new)
    except OSError as exc:
        return ToolResult.error(f"Failed to write '{rel}': {exc}")

    return ToolResult(
        ok=True,
        output=f"Wrote {len(new)} bytes to '{rel}' (+{added} -{removed}).",
        summary=f"write {rel} (+{added} -{removed})",
        meta={"added": added, "removed": removed, "created": old == ""},
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=WriteFileArgs, func=write_file))
