"""Skills: reusable working-style instructions, progressively disclosed.

A skill is a small markdown file — a ``---`` header carrying ``name:``,
``description:``, optionally ``triggers:`` and ``roles:``, then a body of
instructions. Two ways a skill's body reaches an agent's context:

1. Host-side auto-activation (:func:`auto_match`, unchanged from before
   progressive disclosure): pure keyword matching against ``triggers:``,
   no model call, splices the body directly into the system prompt under
   an ``ACTIVE SKILLS`` section (:func:`skills_prompt_block`) before the
   agent's first turn.
2. Agent-invoked (new): every agent with tool access always sees a
   lightweight name+description catalog in its system prompt
   (:func:`skills_catalog_block`, optionally filtered by ``roles:`` to
   the agent's roster role), and can call the ``use_skill`` tool
   (relaycli/tools/use_skill.py) mid-run to pull a specific skill's full
   body into context — mirroring how Claude Code's own Skill tool works,
   rather than relying solely on host-side keyword matching.

Discovery, later source winning on a name collision:

1. Built-ins shipped with the package (this directory's ``*.md``).
2. User skills in ``~/.relaycli/skills/``.
3. Project skills in ``<project root>/.relaycli/skills/``.

SECURITY: *project* skills are never auto-activated and never loadable via
the ``use_skill`` tool — only :func:`AUTO_SOURCES` (builtin/user) skills can
reach an agent's context through either path; ``/skill <name>`` (an explicit
*user* action, not agent- or auto-triggered) is the only way a project skill
ever activates, and the listing shows each skill's source. A cloned
repository can *offer* a skill but cannot silently steer the agent with one
(the same philosophy as the dotenv field blocklist in config.py) — this
must hold whether the steering attempt is host-side auto-activation or an
agent talked into calling use_skill() on an enticingly-named project skill,
so both paths share the identical AUTO_SOURCES check, not two independent
ones that could drift. Built-in and user skills — code the user installed —
may opt into per-request auto-activation via a ``triggers:`` header, matched
by :func:`auto_match`, and are always announced in the UI.
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
AUTO_SOURCES = frozenset({"builtin", "user"})


@dataclass(frozen=True)
class Skill:
    """One named, toggleable instruction pack."""

    name: str
    description: str
    body: str
    source: str  # "builtin" | "user" | "project"
    triggers: tuple[str, ...] = field(default=())
    # Roster role ids (core/roles.py) this skill is scoped to, e.g.
    # "tester, debugger" — empty means universally relevant. Optional;
    # only affects skills_catalog_block's filtering, never auto_match or
    # /skill (a skill with no roles: header is unaffected either way).
    roles: tuple[str, ...] = field(default=())


def parse_skill(text: str, *, fallback_name: str, source: str) -> Skill:
    """Parse a skill file: optional ``---`` header, then the body.

    Header lines are ``key: value``; unknown keys are ignored. A file with
    no header is still a skill — the filename stem becomes its name.
    """
    name = fallback_name
    description = ""
    triggers: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
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
                elif key == "roles":
                    roles = tuple(
                        r.strip().lower() for r in value.split(",") if r.strip()
                    )
            body = rest.strip()
    return Skill(
        name=name, description=description, body=body, source=source,
        triggers=triggers, roles=roles
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
        if name in active or skill.source not in AUTO_SOURCES or not skill.triggers:
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


def skills_catalog_block(skills: dict[str, Skill], *, role_id: str | None = None) -> str:
    """Progressive disclosure's "always injected" half: name+description
    only (never the body — that's what the use_skill tool is for), so an
    agent knows what exists without paying for what it doesn't use.

    AUTO_SOURCES-gated same as auto_match: a project skill must not be
    advertised here even though it can't be *loaded* here either (see
    the module docstring) — no point tempting an agent with a name it'll
    just get an error calling.

    role_id filters to skills with no roles: header (universally
    relevant) or whose roles: list includes it; role_id=None (no known
    roster role — e.g. the REPL's single-agent mode) skips the filter
    entirely, showing every AUTO_SOURCES skill.
    """
    catalog = [
        skill for skill in skills.values()
        if skill.source in AUTO_SOURCES
        and (role_id is None or not skill.roles or role_id in skill.roles)
    ]
    if not catalog:
        return ""
    parts = [
        "",
        "SKILLS — call use_skill(name) to load one's full instructions "
        "into context when it applies to what you're doing:",
    ]
    for skill in sorted(catalog, key=lambda s: s.name):
        parts.append(f"- {skill.name}: {skill.description}")
    return "\n".join(parts)
