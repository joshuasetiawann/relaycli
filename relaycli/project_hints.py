"""Small project snapshots that help local models choose real paths."""

from __future__ import annotations

import os
from pathlib import Path

from relaycli.context import ALWAYS_IGNORE_DIRS, ProjectContext

_WEB_SUFFIXES = {
    ".html",
    ".css",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
}
_WEB_FILENAMES = {
    "index.html",
    "styles.css",
    "style.css",
    "app.css",
    "app.js",
    "main.js",
    "script.js",
    "package.json",
    "vite.config.js",
    "vite.config.ts",
}


def project_prompt_block(project: ProjectContext) -> str:
    """Return a concise, safe path hint block for the agent system prompt."""

    top = _top_level_entries(project)
    web = likely_web_files(project, limit=24)
    lines = ["", "", "Project snapshot:"]
    if top:
        lines.append("- top-level entries: " + ", ".join(top))
    else:
        lines.append("- top-level entries: (empty project)")
    if web:
        lines.append("- likely editable web files:")
        lines.extend(f"  - {path}" for path in web)
        lines.append(
            "- If changing an existing website, read the matching files above first. "
            "Do not assume `src/index.html` unless it is listed."
        )
    else:
        lines.append("- likely editable web files: none detected")
    return "\n".join(lines)


def missing_path_hint(project: ProjectContext, requested: str, *, limit: int = 8) -> str:
    """Suggest existing likely paths after a model guesses a missing path."""

    req_name = Path(requested).name.lower()
    candidates = [
        path for path in likely_web_files(project, limit=50)
        if Path(path).name.lower() == req_name
    ]
    if not candidates and Path(requested).suffix.lower() in _WEB_SUFFIXES:
        candidates = likely_web_files(project, limit=limit)
    if not candidates:
        return "\n\nNo matching file was found. Use list_dir or find_files to locate the correct path before retrying."
    shown = ", ".join(candidates[:limit])
    return (
        "\n\nExisting likely paths: "
        f"{shown}. Use one of these exact relative paths, or run list_dir/find_files first."
    )


def likely_web_files(project: ProjectContext, *, limit: int = 24) -> list[str]:
    files: list[str] = []
    for path in _iter_project_files(project, max_depth=5, max_files=2500):
        name = path.name.lower()
        if name in _WEB_FILENAMES or path.suffix.lower() in _WEB_SUFFIXES:
            files.append(project.relative(path))
    files.sort(key=_web_priority)
    return files[:limit]


def _top_level_entries(project: ProjectContext, *, limit: int = 40) -> list[str]:
    try:
        entries = sorted(project.root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    names: list[str] = []
    for entry in entries:
        if _skip_name(entry.name) or project.is_secret(entry):
            continue
        names.append(entry.name + ("/" if entry.is_dir() else ""))
        if len(names) >= limit:
            break
    return names


def _iter_project_files(
    project: ProjectContext, *, max_depth: int, max_files: int
) -> list[Path]:
    found: list[Path] = []
    root = project.root
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        try:
            rel_dir = current_path.resolve().relative_to(root)
        except (OSError, ValueError):
            dirs[:] = []
            continue
        depth = 0 if rel_dir == Path(".") else len(rel_dir.parts)
        if depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if not _skip_name(d)]

        for filename in files:
            if _skip_name(filename):
                continue
            path = current_path / filename
            if project.is_secret(path):
                continue
            found.append(path)
            if len(found) >= max_files:
                return found
    return found


def _skip_name(name: str) -> bool:
    return name in ALWAYS_IGNORE_DIRS or name.startswith(".")


def _web_priority(rel: str) -> tuple[int, int, str]:
    path = Path(rel)
    name = path.name.lower()
    if name == "index.html":
        rank = 0
    elif name in {"styles.css", "style.css", "app.css"}:
        rank = 1
    elif name in {"app.js", "main.js", "script.js"}:
        rank = 2
    elif path.suffix.lower() == ".html":
        rank = 3
    elif path.suffix.lower() == ".css":
        rank = 4
    else:
        rank = 5
    return (rank, len(path.parts), rel.lower())
