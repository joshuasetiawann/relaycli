"""Agent package — re-exports for backward compatibility.

Uses json-repair for robust JSON parsing and heuristics.yaml for
maintainable configuration.
"""

from __future__ import annotations

import json
import re

from json_repair import repair_json

from relaycli.agent.loop import Agent, AgentResult, text_tool_calls
from relaycli.agent.pipeline import Relay, RelayResult
from relaycli.agent.reporter import Reporter, PlainReporter
from relaycli.agent.router import Role, resolve_model, role_enabled, routing_table


def fake_tool_call_text(text: str) -> str | None:
    """Check if text contains JSON that looks like a tool call (backward compat).

    Uses json-repair for robust extraction.
    """
    raw = (text or "").strip()
    data = _json_from_text(raw)
    if isinstance(data, dict):
        for key in ("name", "tool", "tool_name", "function_name"):
            if data.get(key):
                return str(data[key])
        fn = data.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
    if isinstance(data, list):
        for item in data:
            result = fake_tool_call_text(json.dumps(item))
            if result:
                return result
    return None


def _json_from_text(text: str) -> object | None:
    """Extract JSON from text using json-repair, with regex/bracket fallback."""
    raw = (text or "").strip()
    if not raw:
        return None

    candidates: list[str] = []

    # Strategy 1: json_repair on the whole text
    repaired = repair_json(raw)
    if repaired:
        candidates.append(repaired)

    # Strategy 2: json_repair on code-fenced blocks
    for match in re.finditer(
        r"```(?:json|javascript|js)?\s*(.*?)```",
        raw, flags=re.DOTALL | re.IGNORECASE,
    ):
        inner = match.group(1).strip()
        if inner:
            repaired_inner = repair_json(inner)
            if repaired_inner and repaired_inner not in candidates:
                candidates.append(repaired_inner)

    # Strategy 3: direct JSON parse on raw text
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 4: bracket-matching fallback
    blob = _first_json_blob(raw)
    if blob and blob not in candidates:
        candidates.append(blob)

    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _first_json_blob(raw: str) -> str | None:
    """Bracket-matching JSON extraction (fallback when json-repair fails)."""
    start = -1
    for index, char in enumerate(raw):
        if char in "{[":
            start = index
            break
    if start < 0:
        return None
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if char in "}]":
            if not stack or char != stack.pop():
                return None
            if not stack:
                return raw[start:index + 1]
    return None


def _compact(arguments: str, limit: int = 80) -> str:
    one_line = " ".join((arguments or "").split())
    return one_line if len(one_line) <= limit else one_line[:limit - 1] + "\u2026"


__all__ = [
    "Agent", "AgentResult", "Relay", "RelayResult",
    "Reporter", "PlainReporter", "Role", "resolve_model",
    "role_enabled", "routing_table", "fake_tool_call_text",
    "text_tool_calls", "_compact",
]
