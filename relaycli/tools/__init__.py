"""Tool registry for RelayCLI.

A tool is defined once with a name, a description, and a Pydantic args schema.
The registry emits the JSON-schema list LiteLLM tool-calling expects and
dispatches validated calls back to the implementation.

Real coding tools (read_file, search, write_file, edit_file, run_command)
arrive in Stage 3. Stage 2 ships only the registry and a throwaway tool used
to prove the round-trip.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

if TYPE_CHECKING:
    from relaycli.tools.base import ToolContext

# Every real tool is invoked as ``func(args, ctx)``. ``ctx`` may be None for
# trivial context-free tools (used in unit tests).
ToolFunc = Callable[..., Any]


class ToolError(RuntimeError):
    """Raised when a tool's arguments are invalid or execution fails cleanly."""


@dataclass
class Tool:
    """A single registered tool."""

    name: str
    description: str
    args_model: type[BaseModel]
    func: ToolFunc

    def json_schema(self) -> dict[str, Any]:
        """Return the OpenAI/LiteLLM function-tool schema for this tool."""
        params = self.args_model.model_json_schema()
        params.pop("title", None)  # noise; the function name carries identity
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }

    def run(
        self, arguments: str | dict[str, Any] | None, ctx: "ToolContext | None" = None
    ) -> Any:
        """Validate ``arguments`` against the schema and invoke the tool.

        ``arguments`` may be the raw JSON string from the model, a dict, or
        ``None``. Invalid JSON or schema-validation failures raise
        :class:`ToolError` with a readable message (never a raw traceback).
        """
        if isinstance(arguments, str):
            raw = arguments.strip()
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                raise ToolError(
                    f"Tool '{self.name}' received malformed JSON arguments: {exc}"
                ) from exc
        else:
            data = arguments or {}

        if not isinstance(data, dict):
            raise ToolError(
                f"Tool '{self.name}' expects an object of arguments, got {type(data).__name__}."
            )

        # Models (especially smaller ones) often emit explicit `null` for optional
        # parameters. Drop None values so the schema's defaults apply instead of
        # failing validation (e.g. {"max_results": null} -> use the default).
        data = {key: value for key, value in data.items() if value is not None}

        try:
            parsed = self.args_model.model_validate(data)
        except Exception as exc:  # pydantic ValidationError
            raise ToolError(f"Invalid arguments for tool '{self.name}': {exc}") from exc

        return self.func(parsed, ctx)


class ToolRegistry:
    """An ordered collection of tools keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self, name: str, description: str, args_model: type[BaseModel]
    ) -> Callable[[ToolFunc], ToolFunc]:
        """Decorator form: register the decorated function as a tool."""

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
        """The full tool-schema list to hand to the model."""
        return [tool.json_schema() for tool in self._tools.values()]

    def run(
        self,
        name: str,
        arguments: str | dict[str, Any] | None,
        ctx: "ToolContext | None" = None,
    ) -> Any:
        tool = self.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(self.names()) or '(none)'}")
        return tool.run(arguments, ctx)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def clear(self) -> None:
        self._tools.clear()


def default_registry() -> ToolRegistry:
    """Build a fresh registry containing all real RelayCLI coding tools."""
    from relaycli.tools import (  # local import avoids import cycles
        background as _background,
        edit_file as _edit_file,
        find_files as _find_files,
        list_dir as _list_dir,
        read_file as _read_file,
        remember as _remember,
        run_command as _run_command,
        search as _search,
        write_file as _write_file,
    )

    reg = ToolRegistry()
    for module in (_list_dir, _find_files, _read_file, _search, _write_file,
                   _edit_file, _run_command, _background, _remember):
        module.register(reg)
    return reg


def planner_registry() -> ToolRegistry:
    """Read-only subset for the relay Planner (cannot edit or run anything).

    Enforcement is by construction: the write/run tools are simply not
    registered, so the model cannot call them no matter what it emits.
    """
    from relaycli.tools import (
        find_files as _find_files,
        list_dir as _list_dir,
        read_file as _read_file,
        search as _search,
    )

    reg = ToolRegistry()
    for module in (_list_dir, _find_files, _read_file, _search):
        module.register(reg)
    return reg


def reviewer_registry() -> ToolRegistry:
    """Reviewer subset: read + search + run_command (to run tests); no writes.

    run_command still goes through the PermissionManager like everywhere else.
    """
    from relaycli.tools import (
        find_files as _find_files,
        list_dir as _list_dir,
        read_file as _read_file,
        run_command as _run_command,
        search as _search,
    )

    reg = ToolRegistry()
    for module in (_list_dir, _find_files, _read_file, _search, _run_command):
        module.register(reg)
    from relaycli.tools import background as _background

    _background.register_check_only(reg)
    return reg


# A process-wide registry (kept for ad-hoc use; sessions build their own).
registry = ToolRegistry()

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolError",
    "ToolFunc",
    "registry",
    "default_registry",
    "planner_registry",
    "reviewer_registry",
]
