from __future__ import annotations

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry


class ThinkArgs(BaseModel):
    thought: str = Field(description="The model's reasoning and thought process")


def think(args: ThinkArgs, ctx: ToolContext | None) -> ToolResult:
    return ToolResult(ok=True, output=f"Thought recorded: {args.thought}", summary="reasoning step")


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="think", description="Record a reasoning step. Use this to work through complex problems step by step before taking action.",
                 args_model=ThinkArgs, func=think))
