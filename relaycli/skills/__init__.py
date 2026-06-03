"""Skills: reusable working-style instructions the user toggles per session.

A skill is a small markdown file — a ``---`` header carrying ``name:`` and
``description:``, then a body of instructions. Active skills are appended to
the agent's system prompt under an ``ACTIVE SKILLS`` section.

Discovery, later source winning on a name collision:

1. Built-ins shipped with the package (this directory's ``*.md``).
2. User skills in ``~/.relaycli/skills/``.
3. Project skills in ``<project root>/.relaycli/skills/``.

SECURITY: *project* skills are never auto-activated — ``/skill <name>`` is an
explicit user action, and the listing shows each skill's source. A cloned
repository can *offer* a skill but cannot silently steer the agent with one
(the same philosophy as the dotenv field blocklist in config.py). Built-in
and user skills — code the user installed — may opt into per-request
auto-activation via a ``triggers:`` header, matched by :func:`auto_match`
(pure keywords, no model call) and always announced in the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from relaycli.config import CONFIG_DIR

BUILTIN_DIR = Path(__file__).parent
USER_SKILLS_DIR = CONFIG_DIR / "skills"

# Sources trusted for auto-activation: shipped with the package or placed in
# the user's own config dir. Project skills (riding along with a repo) never
# self-activate.
_AUTO_SOURCES = frozenset({"builtin", "user"})


@dataclass(frozen=True)
class Skill:
    """One named, toggleable instruction pack."""

    name: str
    description: str
    body: str
    source: str  # "builtin" | "user" | "project"
    triggers: tuple[str, ...] = field(default=())


def parse_skill(text: str, *, fallback_name: str, source: str) -> Skill:
    """Parse a skill file: optional ``---`` header, then the body.

    Header lines are ``key: value``; unknown keys are ignored. A file with
    no header is still a skill — the filename stem becomes its name.
    """
    name = fallback_name
    description = ""
    triggers: tuple[str, ...] = ()
    body = text.strip()
    if body.startswith("---"):
        head, sep, rest = body[3:].partition("---")
        if sep:
            for line in head.splitlines():
                key, colon, value = line.partition(":")
                if not colon:
                    continue
                key = key.strip().lower()
                if key == "name" and value.strip():
                    name = value.strip()
                elif key == "description":
                    description = value.strip()
                elif key == "triggers":
                    triggers = tuple(
                        t.strip().lower() for t in value.split(",") if t.strip()
                    )
            body = rest.strip()
    return Skill(
        name=name, description=description, body=body, source=source, triggers=triggers
    )


def _load_dir(directory: Path, source: str) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    try:
        entries = sorted(directory.glob("*.md"))
    except OSError:
        return skills
    for path in entries:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # unreadable file: skip, never crash startup
        skill = parse_skill(text, fallback_name=path.stem, source=source)
        skills[skill.name] = skill
    return skills


def discover_skills(project_root: Path | None = None) -> dict[str, Skill]:
    """All available skills by name; user overrides builtin, project overrides both."""
    skills = _load_dir(BUILTIN_DIR, "builtin")
    skills.update(_load_dir(USER_SKILLS_DIR, "user"))
    if project_root is not None:
        skills.update(_load_dir(project_root / ".relaycli" / "skills", "project"))
    return skills


def auto_match(
    skills: dict[str, Skill],
    request: str,
    *,
    active: tuple[str, ...] | list[str] = (),
    limit: int = 2,
) -> list[str]:
    """Names of skills whose triggers match ``request`` (best first, capped).

    Pure keyword matching — no model call, so it costs nothing and cannot be
    steered by anything but the user's own text. Already-active skills and
    project-sourced skills are never returned.
    """
    text = request.lower()
    tokens = set(re.findall(r"[a-z0-9_-]+", text))
    scored: list[tuple[int, str]] = []
    for name, skill in skills.items():
        if name in active or skill.source not in _AUTO_SOURCES or not skill.triggers:
            continue
        score = 0
        for trig in skill.triggers:
            if " " in trig:  # multi-word triggers match as phrases (strong signal)
                if trig in text:
                    score += 2
            elif trig in tokens or (
                len(trig) >= 4 and any(t.startswith(trig) for t in tokens)
            ):
                score += 1
        if score:
            scored.append((score, name))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [name for _, name in scored[:limit]]


def skills_prompt_block(skills: list[Skill]) -> str:
    """The system-prompt section for the active skills ('' when none)."""
    if not skills:
        return ""
    parts = ["", "ACTIVE SKILLS — follow these while working:"]
    for skill in skills:
        parts.append(f"\n## {skill.name}\n{skill.body}")
    return "\n".join(parts)
