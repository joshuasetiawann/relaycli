"""Remember tool — save durable facts to global or project memory."""

from __future__ import annotations

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry
from relaycli.core import memory as mem


class RememberArgs(BaseModel):
    fact: str = Field(description="A durable fact about the project or user preferences")

def remember(args: RememberArgs, ctx: ToolContext | None) -> ToolResult:
    decision = ctx.permissions.confirm("edit", prompt_text=f"remember: {args.fact[:120]}")
    if not decision.approved:
        return ToolResult.error("Remember was declined.", summary="remember (declined)")
    global_text = mem.append_memory(mem.GLOBAL_MEMORY, args.fact)
    return ToolResult(ok=True, output=f"Saved: {global_text}", summary="remembered")


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="remember", description="Save a durable fact for future sessions",
                 args_model=RememberArgs, func=remember))
