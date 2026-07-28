"""use_skill — the agent-invoked half of progressive disclosure.

Every agent with tool access always sees a name+description catalog
(skills.skills_catalog_block, injected in agent/loop.py's
_build_system_prompt) and calls this tool to pull a specific skill's
full body into context once it decides one applies — see
relaycli/skills/__init__.py's module docstring for the full design and
the security reasoning this tool's AUTO_SOURCES check exists for.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from relaycli.skills import AUTO_SOURCES, discover_skills
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry


class UseSkillArgs(BaseModel):
    name: str = Field(description="Skill name, exactly as shown in your system prompt's SKILLS catalog.")


def use_skill(args: UseSkillArgs, ctx: ToolContext | None) -> ToolResult:
    project_root = ctx.project.root if ctx is not None else None
    skill = discover_skills(project_root).get(args.name)
    if skill is None:
        return ToolResult.error(f"no skill named '{args.name}'", summary="skill not found")
    if skill.source not in AUTO_SOURCES:
        # Matches auto_match's own boundary (skills/__init__.py's module
        # docstring) — a project-sourced skill's name and description
        # could themselves be written to entice exactly this call.
        return ToolResult.error(
            f"skill '{args.name}' is project-sourced and can't be loaded this way — "
            "only skills the user installed (built in, or ~/.relaycli/skills) can be. "
            "The user can run /skill to activate it explicitly.",
            summary="skill source not allowed",
        )
    return ToolResult(ok=True, output=skill.body, summary=f"loaded skill '{args.name}'")


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(
        name="use_skill",
        description="Load a skill's full instructions into context. Call this when a "
                     "skill in your SKILLS catalog looks relevant to the current request.",
        args_model=UseSkillArgs, func=use_skill,
    ))
