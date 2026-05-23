"""edit_file tool — apply a targeted find/replace with a diff + permission gate."""

from __future__ import annotations

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli.context import PathSafetyError
from relaycli.render import render_diff
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult, atomic_write

_BINARY_SNIFF = 8192

NAME = "edit_file"
DESCRIPTION = (
    "Make a targeted edit to an existing file by replacing an exact snippet "
    "(old_string) with new_string. old_string must match exactly and, unless "
    "replace_all is set, must be unique. Shows a colored diff and respects the "
    "permission mode."
)


class EditFileArgs(BaseModel):
    path: str = Field(description="File path relative to the project root.")
    old_string: str = Field(description="Exact text to find (include surrounding context to be unique).")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = Field(
        default=False, description="Replace every occurrence instead of requiring a unique match."
    )


def edit_file(args: EditFileArgs, ctx: ToolContext) -> ToolResult:
    proj = ctx.project
    try:
        path = proj.resolve(args.path, must_exist=True)
    except PathSafetyError as exc:
        return ToolResult.error(str(exc), summary=f"edit {args.path} (refused)")

    if not path.is_file():
        return ToolResult.error(
            f"'{args.path}' is not a regular file.", summary=f"edit {args.path} (refused)"
        )

    rel = proj.relative(path)

    if args.old_string == args.new_string:
        return ToolResult.error(
            "old_string and new_string are identical; nothing to change.",
            summary=f"edit {rel} (no-op)",
        )

    # Read raw and decode STRICTLY: a lossy errors="replace" round-trip would
    # silently rewrite any non-UTF-8 byte as U+FFFD, corrupting the file
    # invisibly (both sides of the diff share the same lossy decode).
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return ToolResult.error(f"Could not read '{rel}': {exc}")

    if b"\x00" in raw[:_BINARY_SNIFF]:
        return ToolResult.error(
            f"'{rel}' appears to be binary; refusing to edit it.",
            summary=f"edit {rel} (refused: binary)",
        )
    try:
        old = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ToolResult.error(
            f"'{rel}' is not valid UTF-8; refusing to edit it to avoid corruption.",
            summary=f"edit {rel} (refused: non-utf-8)",
        )

    count = old.count(args.old_string)
    if count == 0:
        return ToolResult.error(
            f"old_string not found in '{rel}'.", summary=f"edit {rel} (not found)"
        )
    if count > 1 and not args.replace_all:
        return ToolResult.error(
            f"old_string occurs {count} times in '{rel}'. Add surrounding context to "
            f"make it unique, or set replace_all=true.",
            summary=f"edit {rel} (ambiguous)",
        )

    if args.replace_all:
        new = old.replace(args.old_string, args.new_string)
    else:
        new = old.replace(args.old_string, args.new_string, 1)

    # Always show the diff before applying.
    added, removed = render_diff(ctx.console, old, new, rel)

    decision = ctx.permissions.confirm("edit", prompt_text=f"Apply edit to {escape(rel)}?")
    if not decision.approved:
        return ToolResult.error(
            f"Edit to '{rel}' was declined.", summary=f"edit {rel} (declined)"
        )

    try:
        atomic_write(path, new)
    except OSError as exc:
        return ToolResult.error(f"Failed to write '{rel}': {exc}")

    return ToolResult(
        ok=True,
        output=f"Edited '{rel}' ({count if args.replace_all else 1} replacement(s), +{added} -{removed}).",
        summary=f"edit {rel} (+{added} -{removed})",
        meta={"added": added, "removed": removed, "replacements": count if args.replace_all else 1},
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=EditFileArgs, func=edit_file))
