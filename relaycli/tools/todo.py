from __future__ import annotations

from pydantic import BaseModel, Field
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry

_TODOS: list[dict] = []


class TodoAddArgs(BaseModel):
    content: str = Field(description="Todo item content")


def todo_add(args: TodoAddArgs, ctx: ToolContext | None) -> ToolResult:
    _TODOS.append({"content": args.content, "done": False})
    return ToolResult(ok=True, output=f"Added: {args.content}", summary=f"todo #{len(_TODOS)}")


class TodoUpdateArgs(BaseModel):
    index: int = Field(ge=1, description="Todo item number (1-based)")
    done: bool = Field(default=True, description="Mark as done")


def todo_update(args: TodoUpdateArgs, ctx: ToolContext | None) -> ToolResult:
    if args.index < 1 or args.index > len(_TODOS):
        return ToolResult.error(f"Invalid todo #{args.index}. Have {len(_TODOS)} items.")
    _TODOS[args.index - 1]["done"] = args.done
    status = "done" if args.done else "pending"
    return ToolResult(ok=True, output=f"Todo #{args.index} marked as {status}",
                      summary=f"todo #{args.index} → {status}")


class TodoListArgs(BaseModel):
    pass


def todo_list(args: TodoListArgs, ctx: ToolContext | None) -> ToolResult:
    if not _TODOS:
        return ToolResult(ok=True, output="(no todos)")
    lines = []
    for i, t in enumerate(_TODOS, 1):
        mark = "x" if t["done"] else " "
        lines.append(f"[{mark}] {i}. {t['content']}")
    return ToolResult(ok=True, output="\n".join(lines), summary=f"{len(_TODOS)} todos")


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="todo_add", description="Add a todo item to the checklist",
                 args_model=TodoAddArgs, func=todo_add))
    reg.add(Tool(name="todo_update", description="Mark a todo item as done or pending",
                 args_model=TodoUpdateArgs, func=todo_update))
    reg.add(Tool(name="todo_list", description="List all todo items",
                 args_model=TodoListArgs, func=todo_list))
