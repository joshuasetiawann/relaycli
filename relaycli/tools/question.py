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
    from relaycli.heuristics import load_heuristics

    # Concrete file work already has a safe default: build the named thing in
    # the named place. Asking about style or file structure only burns local
    # model turns and can trap small models in a clarification loop.
    if ctx.request and load_heuristics().looks_like_actionable_file_request(ctx.request):
        return ToolResult(
            ok=True,
            output=(
                "The request is concrete. Do not ask a preference question; "
                "choose sensible defaults, create the requested files, and verify them now."
            ),
            summary="question auto-resolved",
            meta={"auto_resolved": True},
        )
    if ctx.questions_asked >= 1:
        return ToolResult(
            ok=True,
            output=(
                "Clarification limit reached. Continue with the user's answer "
                "and reasonable defaults; do not ask another question."
            ),
            summary="question limit reached",
            meta={"auto_resolved": True},
        )
    ctx.questions_asked += 1
    if not getattr(ctx.permissions, "_assume_yes", False):
        from rich.prompt import Prompt

        answer = Prompt.ask(args.question, choices=args.options, console=console)
        return ToolResult(ok=True, output=answer, summary="user answered question")
    if args.options:
        return ToolResult(
            ok=True,
            output=f"Auto-selected default option: {args.options[0]}",
            summary="question auto-resolved",
            meta={"auto_resolved": True, "answer": args.options[0]},
        )
    return ToolResult(
        ok=True,
        output="Use sensible defaults and continue.",
        summary="question auto-resolved",
        meta={"auto_resolved": True},
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="question", description=(
                    "Ask at most one blocking clarification when the request is truly ambiguous. "
                    "Never ask about optional preferences when sensible defaults can complete the work."
                 ),
                 args_model=QuestionArgs, func=ask_question))
