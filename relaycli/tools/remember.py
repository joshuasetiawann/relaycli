"""remember tool — save one durable fact to local memory (edit-gated)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli import memory
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

NAME = "remember"
DESCRIPTION = (
    "Save one short durable fact to local memory so future sessions know it. "
    "Use scope 'project' for facts about this codebase (conventions, gotchas, "
    "decisions) and 'global' for facts that hold everywhere (user preferences, "
    "environment). Only save what future sessions genuinely need."
)


class RememberArgs(BaseModel):
    fact: str = Field(description="The fact to remember, one concise sentence.")
    scope: Literal["project", "global"] = Field(
        default="project", description="Where the fact applies."
    )


def remember(args: RememberArgs, ctx: ToolContext) -> ToolResult:
    fact = args.fact.strip()
    if not fact:
        return ToolResult.error("Nothing to remember: 'fact' is empty.")

    if args.scope == "global":
        path = memory.GLOBAL_MEMORY
    else:
        path = memory.project_memory_path(ctx.project.root)

    preview = fact if len(fact) <= 80 else fact[:79] + "…"
    decision = ctx.permissions.confirm(
        "write", prompt_text=f"Remember to {args.scope} memory: {escape(preview)}?"
    )
    if not decision.approved:
        return ToolResult.error(
            f"Remember ({args.scope}) was declined.",
            summary=f"remember {args.scope} (declined)",
        )

    try:
        entry = memory.append_memory(path, fact)
    except OSError as exc:
        return ToolResult.error(f"Could not write memory file: {exc}")

    return ToolResult(
        ok=True,
        output=f"Saved to {args.scope} memory: {entry}",
        summary=f"remember {args.scope}",
        meta={"scope": args.scope},
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=RememberArgs, func=remember))
