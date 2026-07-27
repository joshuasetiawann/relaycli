"""Tool registry — define tools, emit JSON schemas, dispatch calls.

Async execution:
- :meth:`Tool.arun` and :meth:`ToolRegistry.arun` provide async counterparts
  for use with ``asyncio.gather``.
- Sync :meth:`Tool.run` / :meth:`ToolRegistry.run` remain for backward compat.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from relaycli.tools.base import rate_limited

if TYPE_CHECKING:
    from relaycli.tools.base import ToolContext

ToolFunc = Callable[..., Any]


class ToolError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    func: ToolFunc

    def json_schema(self) -> dict[str, Any]:
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": params}}

    @rate_limited
    def run(self, arguments: str | dict[str, Any] | None, ctx: ToolContext | None = None) -> Any:
        parsed = self._parse_arguments(arguments)
        return self.func(parsed, ctx)

    @rate_limited
    async def arun(self, arguments: str | dict[str, Any] | None, ctx: ToolContext | None = None) -> Any:
        parsed = self._parse_arguments(arguments)
        result = self.func(parsed, ctx)
        if inspect.isawaitable(result):
            return await result
        return result

    def _parse_arguments(self, arguments: str | dict[str, Any] | None) -> BaseModel:
        if isinstance(arguments, str):
            raw = arguments.strip()
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                raise ToolError(f"Tool '{self.name}' received malformed JSON: {exc}") from exc
        else:
            data = arguments or {}
        if not isinstance(data, dict):
            raise ToolError(f"Tool '{self.name}' expects an object, got {type(data).__name__}.")
        data = {k: v for k, v in data.items() if v is not None}
        try:
            return self.args_model.model_validate(data)
        except Exception as exc:
            raise ToolError(f"Invalid arguments for '{self.name}': {exc}") from exc


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, args_model: type[BaseModel]) -> Callable[[ToolFunc], ToolFunc]:
        def decorator(func: ToolFunc) -> ToolFunc:
            self.add(Tool(name=name, description=description, args_model=args_model, func=func))
            return func
        return decorator

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [t.json_schema() for t in self._tools.values()]

    def run(self, name: str, arguments: str | dict[str, Any] | None, ctx: ToolContext | None = None) -> Any:
        tool = self.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(self.names()) or '(none)'}")
        return tool.run(arguments, ctx)

    async def arun(self, name: str, arguments: str | dict[str, Any] | None, ctx: ToolContext | None = None) -> Any:
        tool = self.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(self.names()) or '(none)'}")
        return await tool.arun(arguments, ctx)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def clear(self) -> None:
        self._tools.clear()


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    _register_defaults(reg)
    return reg


def _register_defaults(reg: ToolRegistry) -> None:
    from relaycli.tools import (
        list_dir as _list_dir,
        read_file as _read_file,
        write_file as _write_file,
        edit_file as _edit_file,
        create_folder as _create_folder,
        find_files as _find_files,
        run_command as _run_command,
        background as _background,
        search as _search,
        remember as _remember,
        webfetch as _webfetch,
        question as _question,
        todo as _todo,
        git_tool as _git,
        apply_patch as _apply_patch,
        think as _think,
        websearch as _websearch,
    )
    _list_dir.register(reg)
    _read_file.register(reg)
    _write_file.register(reg)
    _edit_file.register(reg)
    _create_folder.register(reg)
    _find_files.register(reg)
    _run_command.register(reg)
    _background.register(reg)
    _search.register(reg)
    _remember.register(reg)
    _webfetch.register(reg)
    _question.register(reg)
    _todo.register(reg)
    _git.register(reg)
    _apply_patch.register(reg)
    _think.register(reg)
    _websearch.register(reg)


def planner_registry() -> ToolRegistry:
    from relaycli.tools import list_dir as _list_dir, find_files as _find_files
    from relaycli.tools import read_file as _read_file, search as _search
    reg = ToolRegistry()
    _list_dir.register(reg)
    _find_files.register(reg)
    _read_file.register(reg)
    _search.register(reg)
    return reg


def reviewer_registry() -> ToolRegistry:
    from relaycli.tools import list_dir as _list_dir, find_files as _find_files
    from relaycli.tools import read_file as _read_file, run_command as _run_command, search as _search
    from relaycli.tools import background as _background
    reg = ToolRegistry()
    _list_dir.register(reg)
    _find_files.register(reg)
    _read_file.register(reg)
    _run_command.register(reg)
    _search.register(reg)
    _background.register_check_only(reg)
    return reg
