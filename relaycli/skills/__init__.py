"""Skills: reusable working-style instructions the user toggles per session.

A skill is a small markdown file — a ``---`` header carrying ``name:`` and
``description:``, then a body of instructions. Active skills are appended to
the agent's system prompt under an ``ACTIVE SKILLS`` section.

Discovery, later source winning on a name collision:

1. Built-ins shipped with the package (this directory's ``*.md``).
2. User skills in ``~/.relaycli/skills/``.
3. Project skills in ``<project root>/.relaycli/skills/``.

SECURITY: skills are NEVER auto-activated — ``/skill <name>`` is an explicit
user action, and the listing shows each skill's source. A cloned repository
can *offer* a skill but cannot silently steer the agent with one (the same
philosophy as the dotenv field blocklist in config.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from relaycli.config import CONFIG_DIR

BUILTIN_DIR = Path(__file__).parent
USER_SKILLS_DIR = CONFIG_DIR / "skills"


@dataclass(frozen=True)
class Skill:
    """One named, toggleable instruction pack."""

    name: str
    description: str
    body: str
    source: str  # "builtin" | "user" | "project"


def parse_skill(text: str, *, fallback_name: str, source: str) -> Skill:
    """Parse a skill file: optional ``---`` header, then the body.

    Header lines are ``key: value``; unknown keys are ignored. A file with
    no header is still a skill — the filename stem becomes its name.
    """
    name = fallback_name
    description = ""
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
            body = rest.strip()
    return Skill(name=name, description=description, body=body, source=source)


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


def skills_prompt_block(skills: list[Skill]) -> str:
    """The system-prompt section for the active skills ('' when none)."""
    if not skills:
        return ""
    parts = ["", "ACTIVE SKILLS — follow these while working:"]
    for skill in skills:
        parts.append(f"\n## {skill.name}\n{skill.body}")
    return "\n".join(parts)
