from __future__ import annotations

from pydantic import BaseModel, Field
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry


class QuestionArgs(BaseModel):
    question: str = Field(description="Question to ask the user")
    options: list[str] | None = Field(default=None, description="Multiple choice options")


def ask_question(args: QuestionArgs, ctx: ToolContext | None) -> ToolResult:
    console = ctx.console if ctx else None
    if console is None:
        return ToolResult.error("No console available to ask the user.")
    if args.options:
        from rich.prompt import Prompt
        console.print(f"[cyan]?[/cyan] {args.question}")
        for i, opt in enumerate(args.options, 1):
            console.print(f"  {i}. {opt}")
        answer = Prompt.ask("Your choice", console=console)
        return ToolResult(ok=True, output=f"User chose: {answer.strip()}")
    from rich.prompt import Prompt
    answer = Prompt.ask(f"[cyan]?[/cyan] {args.question}", console=console)
    return ToolResult(ok=True, output=f"User answered: {answer.strip()}")


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="question", description="Ask the user a question during execution",
                 args_model=QuestionArgs, func=ask_question))
