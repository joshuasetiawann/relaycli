"""Durable memory — global and project-level notes the agent reads every session."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from relaycli.core.config import CONFIG_DIR
from relaycli.tools.base import atomic_write

GLOBAL_MEMORY = CONFIG_DIR / "memory.md"
MEMORY_CAP_CHARS = 4000
FACT_MAX_CHARS = 500


def project_memory_path(root: Path) -> Path:
    return root / ".relaycli" / "memory.md"


def read_memory(path: Path, cap: int = MEMORY_CAP_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) <= cap:
        return text
    tail = text[-cap:]
    _, sep, rest = tail.partition("\n")
    return rest if sep else tail


def append_memory(path: Path, fact: str) -> str:
    line = " ".join(fact.split())
    if len(line) > FACT_MAX_CHARS:
        line = line[: FACT_MAX_CHARS - 1] + "…"
    stamp = _dt.date.today().isoformat()
    entry = f"- [{stamp}] {line}"
    existing = ""
    try:
        existing = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    body = (existing.rstrip() + "\n" if existing.strip() else "") + entry + "\n"
    atomic_write(path, body)
    return entry


def memory_prompt_block(project_root: Path, global_path: Path | None = None) -> str:
    global_path = global_path or GLOBAL_MEMORY
    global_text = read_memory(global_path)
    project_text = read_memory(project_memory_path(project_root))
    if not global_text and not project_text:
        return ""
    parts = ["", "MEMORY — notes from earlier sessions. Background only:"]
    if global_text:
        parts.append(f"\n## Global notes\n{global_text}")
    if project_text:
        parts.append(f"\n## Project notes\n{project_text}")
    return "\n".join(parts)
