"""Heuristics loader — load and validate heuristics.yaml at init time.

Extracts hardcoded keyword lists from agent.py into a single YAML config
with safe fallback defaults. The module caches the parsed heuristics once
for the lifetime of the process.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_HEURISTICS_PATH = Path(__file__).with_name("heuristics.yaml")

# ── defaults (used when the file is missing or a key is absent) ─────────────

_DEFAULT_TEXT_TOOL_ALIASES: dict[str, str] = {
    "ls": "list_dir",
    "list_directory": "list_dir",
    "list_files": "list_dir",
    "read": "read_file",
    "open_file": "read_file",
    "cat": "read_file",
    "write": "write_file",
    "create_file": "write_file",
    "save_file": "write_file",
    "replace_file": "write_file",
    "mkdir": "create_folder",
    "create_dir": "create_folder",
    "create_directory": "create_folder",
    "make_directory": "create_folder",
    "run": "run_command",
    "shell": "run_command",
    "run_shell": "run_command",
    "execute_command": "run_command",
    "terminal": "run_command",
}

_DEFAULT_STRING_ARGUMENT_KEYS: dict[str, str] = {
    "list_dir": "path",
    "read_file": "path",
    "create_folder": "path",
    "find_files": "pattern",
    "search": "query",
    "run_command": "command",
    "run_background": "command",
    "check_process": "id",
    "stop_process": "id",
    "webfetch": "url",
    "websearch": "query",
    "git": "args",
    "apply_patch": "patch",
    "todo_add": "content",
    "question": "question",
    "think": "thought",
}

_DEFAULT_CREATE_FOLDER_PATH_KEYS: tuple[str, ...] = (
    "path", "folder_name", "folder_path", "name", "directory", "directory_name", "dir_name",
)

_DEFAULT_FILE_ACTION_TERMS: tuple[str, ...] = (
    "buat", "buatin", "bikin", "create", "build", "generate",
    "tulis", "write", "add", "tambah", "ubah", "edit", "ganti",
    "update", "perbaiki", "perbagus", "fix", "scaffold",
)

_DEFAULT_FILE_TARGET_TERMS: tuple[str, ...] = (
    "website", "web ", " web", "html", "css", "javascript", " js",
    "frontend", "front end", "landing", "halaman", "platform",
    "aplikasi", "file", "folder", "directory", "direktori",
)

_DEFAULT_CLARIFICATION_TERMS: tuple[str, ...] = (
    "don't have enough information",
    "do not have enough information",
    "need more information",
    "provide more details",
    "provide more context",
    "provide the details",
    "provide me with",
    "necessary information",
    "here are the steps",
    "steps to create",
    "you can further customize",
)

_DEFAULT_SHORT_NOOP_REPLIES: frozenset[str] = frozenset({
    "done", "done.", "finished", "finished.", "complete", "complete.",
    "completed", "completed.", "ok", "okay",
    "selesai", "selesai.", "beres", "beres.", "sudah", "sudah.",
})

_DEFAULT_FILE_DONE_CLAIM_TERMS: tuple[str, ...] = (
    "<tool_response>",
    "created file", "created the file", "file created",
    "wrote file", "wrote the file", "write complete",
    "website created", "files created", "created successfully",
    "i created", "i've created", "i have created",
    "created the website", "created the web", "created the app",
    "built the website", "built the web",
    "generated the website",
    "finished the website", "completed the website",
    "the files are ready",
    "index.html to preview", "open index.html", "open the index.html",
    "sudah dibuat", "telah dibuat", "berhasil dibuat", "bisa langsung dibuka",
)

_DEFAULT_FILE_DONE_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:i|we)\s+(?:created|built|generated|wrote|implemented|finished|completed)\b", re.IGNORECASE),
    re.compile(r"\b(?:i(?:'ve| have)|we(?:'ve| have))\s+(?:created|built|generated|written|implemented|finished|completed)\b", re.IGNORECASE),
    re.compile(r"\b(?:created|built|generated|wrote|implemented|finished|completed)\s+(?:the\s+)?(?:website|web|site|app|page|frontend|file|files)\b", re.IGNORECASE),
    re.compile(r"\b(?:sudah|telah|berhasil)\s+(?:saya\s+)?(?:buat|dibuat|membuat|selesaikan|selesai)\b", re.IGNORECASE),
)

# ── data class ─────────────────────────────────────────────────────────────


class Heuristics:
    """Immutable snapshot of the loaded heuristics config."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.text_tool_aliases: dict[str, str] = {
            k.lower(): v for k, v in
            _dict_or(data, "text_tool_aliases", _DEFAULT_TEXT_TOOL_ALIASES).items()
        }
        self.string_argument_keys: dict[str, str] = dict(
            _dict_or(data, "string_argument_keys", _DEFAULT_STRING_ARGUMENT_KEYS)
        )
        self.create_folder_path_keys: tuple[str, ...] = tuple(
            _list_or(data, "create_folder_path_keys", _DEFAULT_CREATE_FOLDER_PATH_KEYS)
        )
        self.file_action_terms: tuple[str, ...] = tuple(
            _list_or(data, "file_action_terms", _DEFAULT_FILE_ACTION_TERMS)
        )
        self.file_target_terms: tuple[str, ...] = tuple(
            _list_or(data, "file_target_terms", _DEFAULT_FILE_TARGET_TERMS)
        )
        self.clarification_or_tutorial_terms: tuple[str, ...] = tuple(
            _list_or(data, "clarification_or_tutorial_terms", _DEFAULT_CLARIFICATION_TERMS)
        )
        self.short_actionable_noop_replies: frozenset[str] = frozenset(
            _list_or(data, "short_actionable_noop_replies", list(_DEFAULT_SHORT_NOOP_REPLIES))
        )
        self.file_done_claim_terms: tuple[str, ...] = tuple(
            _list_or(data, "file_done_claim_terms", _DEFAULT_FILE_DONE_CLAIM_TERMS)
        )
        self.file_done_claim_patterns: tuple[re.Pattern[str], ...] = tuple(
            _compile_patterns(_list_or(data, "file_done_claim_patterns", []))
            or _DEFAULT_FILE_DONE_CLAIM_PATTERNS
        )

    # ── convenience lookups ────────────────────────────────────────────

    def canonical_tool_name(self, name: str | None, known_names: set[str]) -> str | None:
        """Resolve a model-provided tool name to a registered name via aliases."""
        if not isinstance(name, str):
            return None
        stripped = name.strip()
        if stripped in known_names:
            return stripped
        normalized = stripped.lower().replace("-", "_").replace(" ", "_")
        if normalized in known_names:
            return normalized
        return self.text_tool_aliases.get(normalized)

    def looks_like_actionable_file_request(self, request: str) -> bool:
        lowered = f" {request.lower()} "
        return (
            any(term in lowered for term in self.file_action_terms)
            and any(term in lowered for term in self.file_target_terms)
        )

    def is_pure_folder_request(self, request: str) -> bool:
        lowered = f" {request.lower()} "
        folder_terms = {"folder", "directory", "direktori"}
        non_folder_targets = set(
            t for t in self.file_target_terms
            if t.strip() not in folder_terms
        )
        return (
            any(term in lowered for term in folder_terms)
            and not any(term in lowered for term in non_folder_targets)
        )

    def is_short_noop(self, text: str) -> bool:
        return text.strip().lower() in self.short_actionable_noop_replies

    def contains_clarification_or_tutorial(self, text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in self.clarification_or_tutorial_terms)

    def contains_done_claim(self, text: str) -> bool:
        lowered = text.lower()
        if any(term in lowered for term in self.file_done_claim_terms):
            return True
        return any(p.search(text) for p in self.file_done_claim_patterns)


# ── private helpers ────────────────────────────────────────────────────────


def _dict_or(data: dict[str, Any], key: str, default: dict) -> dict:
    raw = data.get(key)
    return dict(raw) if isinstance(raw, dict) else dict(default)


def _list_or(data: dict[str, Any], key: str, default: tuple | list) -> list:
    raw = data.get(key)
    return list(raw) if isinstance(raw, list) else list(default)


def _compile_patterns(raw: list) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for item in raw:
        if isinstance(item, str):
            try:
                compiled.append(re.compile(item, re.IGNORECASE))
            except re.error:
                continue
    return compiled


@lru_cache(maxsize=1)
def load_heuristics() -> Heuristics:
    """Load the YAML config once and return a cached :class:`Heuristics`.

    If the file is missing or malformed, falls back to built-in defaults.
    """
    path = _HEURISTICS_PATH
    if not path.exists():
        return Heuristics({})
    try:
        raw = path.read_text(encoding="utf-8")
        data: dict[str, Any] = yaml.safe_load(raw) or {}
        return Heuristics(data)
    except Exception:
        return Heuristics({})


def reload_heuristics() -> Heuristics:
    """Clear the cache and reload (used by tests)."""
    load_heuristics.cache_clear()
    return load_heuristics()
