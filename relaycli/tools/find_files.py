"""find_files tool — glob for files by name pattern, read-only, ungated."""

from __future__ import annotations

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli.context import PathSafetyError
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

NAME = "find_files"
DESCRIPTION = (
    "Find files by glob pattern (e.g. '**/*.tsx', 'src/*.py'). Read-only; "
    "skips dependency/build directories. Use this instead of shelling out "
    "to find."
)

_MAX_RESULTS = 200

# Dependency and build trees: huge, machine-generated, never what the model
# is looking for by name. Same spirit as search's exclusions.
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
     "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)


class FindFilesArgs(BaseModel):
    pattern: str = Field(
        description="Glob pattern relative to the project root, e.g. '**/*.tsx'."
    )
    path: str = Field(
        default=".", description="Directory to search under, relative to the project root."
    )


def find_files(args: FindFilesArgs, ctx: ToolContext) -> ToolResult:
    proj = ctx.project
    try:
        base = proj.resolve(args.path, must_exist=True)
    except PathSafetyError as exc:
        return ToolResult.error(str(exc), summary=f"find {args.pattern} (refused)")
    if not base.is_dir():
        return ToolResult.error(
            f"'{args.path}' is not a directory.", summary=f"find {args.pattern} (refused)"
        )

    pattern = args.pattern.strip()
    if not pattern:
        return ToolResult.error("Empty pattern.", summary="find (empty)")

    try:
        matches = []
        for hit in base.glob(pattern):
            parts = hit.relative_to(base).parts
            if any(p in _SKIP_DIRS for p in parts):
                continue
            if hit.is_file():
                matches.append(proj.relative(hit))
            if len(matches) > _MAX_RESULTS:
                break
    except (OSError, ValueError, NotImplementedError) as exc:
        return ToolResult.error(
            f"Bad glob pattern '{pattern}': {exc}", summary=f"find {escape(pattern)} (error)"
        )

    matches.sort()
    if not matches:
        return ToolResult(
            ok=True,
            output=f"No files match '{pattern}' under '{proj.relative(base)}'.",
            summary=f"find {escape(pattern)} (0 matches)",
        )

    shown = matches[:_MAX_RESULTS]
    lines = list(shown)
    if len(matches) > _MAX_RESULTS:
        lines.append(f"[... more matches not shown; narrow the pattern ...]")
    return ToolResult(
        ok=True,
        output="\n".join(lines),
        summary=f"find {escape(pattern)} ({len(shown)}{'+' if len(matches) > _MAX_RESULTS else ''} matches)",
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=FindFilesArgs, func=find_files))
