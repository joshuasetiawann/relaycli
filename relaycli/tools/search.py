"""Search tool — grep-like content search across the project."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry


class SearchArgs(BaseModel):
    query: str = Field(description="Regex pattern to search for")
    path: str | None = Field(default=None, description="Directory to search (default: project root)")
    include: str | None = Field(default=None, description="File pattern (e.g. '*.py', '*.{ts,tsx}')")
    max_results: int | None = Field(default=30, ge=1, le=200)

def search(args: SearchArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        base = ctx.project.resolve(args.path) if args.path else ctx.project.root
    except Exception as exc:
        return ToolResult.error(str(exc))
    if not base.is_dir():
        return ToolResult.error(f"Not a directory: {args.path or '.'}")
    include = args.include or ""
    cmd = ["rg", "--no-heading", "--line-number", "--color", "never"]
    if args.max_results:
        cmd.extend(["-m", str(args.max_results)])
    if include:
        cmd.extend(["-g", include])
    cmd.extend([args.query, str(base)])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, errors="replace")
    except FileNotFoundError:
        return _fallback_search(args.query, base, include, args.max_results or 30)
    except OSError as exc:
        return ToolResult.error(str(exc))
    if proc.returncode not in (0, 1):
        return ToolResult.error(f"Search failed: {proc.stderr[:500]}")
    output = proc.stdout.strip()
    if not output:
        return ToolResult(ok=True, output="(no matches)", summary="found 0 matches")
    lines = output.split("\n")
    # Filter ignored files
    if ctx:
        filtered = [l for l in lines if not any(ctx.project.is_ignored(l.split(":")[0]) for part in [l])]
        filtered = lines
    else:
        filtered = lines
    count = len(filtered)
    result = "\n".join(filtered[:args.max_results or 30])
    if count > (args.max_results or 30):
        result += f"\n... and {count - (args.max_results or 30)} more matches"
    return ToolResult(ok=True, output=result, summary=f"found {count} matches")


def _fallback_search(query: str, base: Path, include: str | None, max_results: int) -> ToolResult:
    """Pure-Python fallback when ripgrep is unavailable."""
    matches: list[str] = []
    try:
        for path in base.rglob(include or "*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(query, line):
                    rel = str(path.relative_to(base))
                    matches.append(f"{rel}:{i}:{line[:200]}")
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
    except OSError:
        pass
    return ToolResult(ok=True, output="\n".join(matches) if matches else "(no matches)",
                      summary=f"found {len(matches)} matches")


def register_search(reg: ToolRegistry) -> None:
    reg.add(Tool(name="search", description="Search file contents with regex (uses ripgrep if available)",
                 args_model=SearchArgs, func=search))


def register(reg: ToolRegistry) -> None:
    register_search(reg)
