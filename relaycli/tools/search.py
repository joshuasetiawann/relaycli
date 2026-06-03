"""search tool — ripgrep-backed code search with a pure-Python fallback."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from relaycli.context import PathSafetyError, ProjectContext
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

NAME = "search"
DESCRIPTION = (
    "Search the project for a pattern (regex by default) and return matching "
    "'path:line: text' results. Uses ripgrep when available; respects "
    ".gitignore and skips secret files."
)

_DEFAULT_MAX_RESULTS = 200
_MAX_LINE_LEN = 400


class SearchArgs(BaseModel):
    query: str = Field(description="Pattern to search for.")
    path: str | None = Field(
        default=None, description="Optional subdirectory/file to scope the search."
    )
    fixed_strings: bool = Field(
        default=False, description="Treat the query as a literal string, not a regex."
    )
    max_results: int = Field(default=_DEFAULT_MAX_RESULTS, ge=1, le=2000)


def search(args: SearchArgs, ctx: ToolContext) -> ToolResult:
    proj = ctx.project
    try:
        base = proj.resolve(args.path, must_exist=True) if args.path else proj.root
    except PathSafetyError as exc:
        return ToolResult.error(str(exc), summary=f"search (refused: {args.path})")

    matches = _ripgrep(args, base, proj)
    if matches is None:  # ripgrep unavailable -> python fallback
        matches = _python_search(args, base, proj)

    if not matches:
        return ToolResult(ok=True, output="No matches found.", summary=f"search '{args.query}' (0)")

    shown = matches[: args.max_results]
    body = "\n".join(shown)
    if len(matches) > len(shown):
        body += f"\n[... {len(matches) - len(shown)} more matches truncated ...]"
    return ToolResult(
        ok=True,
        output=body,
        summary=f"search '{args.query}' ({len(matches)} matches)",
        meta={"count": len(matches)},
    )


def _ripgrep(args: SearchArgs, base: Path, proj: ProjectContext) -> list[str] | None:
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never"]
    if args.fixed_strings:
        cmd.append("--fixed-strings")
    cmd += ["--", args.query, str(base)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return None  # signal: fall back to python
    except subprocess.SubprocessError:
        return []
    if proc.returncode not in (0, 1):  # 1 == no matches
        return []

    results: list[str] = []
    for line in proc.stdout.splitlines():
        parsed = _normalize_rg_line(line, proj)
        if parsed:
            results.append(parsed)
    return results


def _normalize_rg_line(line: str, proj: ProjectContext) -> str | None:
    # rg output: <path>:<lineno>:<text>
    parts = line.split(":", 2)
    if len(parts) < 3:
        return None
    path_str, lineno, text = parts
    path = Path(path_str)
    if proj.is_secret(path):
        return None  # never surface secret-file contents in results
    rel = proj.relative(path)
    return f"{rel}:{lineno}: {text[:_MAX_LINE_LEN].rstrip()}"


def _python_search(args: SearchArgs, base: Path, proj: ProjectContext) -> list[str]:
    if args.fixed_strings:
        pattern = re.compile(re.escape(args.query))
    else:
        try:
            pattern = re.compile(args.query)
        except re.error as exc:
            return [f"(invalid regex: {exc})"]

    results: list[str] = []
    files = [base] if base.is_file() else base.rglob("*")
    for file in files:
        if not file.is_file():
            continue
        if proj.is_secret(file) or proj.is_ignored(file):
            continue
        try:
            with file.open("r", encoding="utf-8", errors="ignore") as fh:
                for lineno, text in enumerate(fh, start=1):
                    if "\x00" in text:
                        break  # binary; stop scanning this file
                    if pattern.search(text):
                        rel = proj.relative(file)
                        results.append(f"{rel}:{lineno}: {text[:_MAX_LINE_LEN].rstrip()}")
                        if len(results) >= args.max_results * 2:
                            return results
        except OSError:
            continue
    return results


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=SearchArgs, func=search))
