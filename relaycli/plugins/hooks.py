"""Hook registry — broadcast events to all loaded plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class HookRegistry:
    _hooks: dict[str, list[Callable]] = field(default_factory=dict)

    def register(self, hook_name: str, handler: Callable) -> None:
        self._hooks.setdefault(hook_name, []).append(handler)

    def unregister(self, hook_name: str, handler: Callable) -> None:
        handlers = self._hooks.get(hook_name)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def trigger(self, hook_name: str, **kwargs) -> list[Any]:
        results: list[Any] = []
        for handler in self._hooks.get(hook_name, []):
            try:
                result = handler(**kwargs)
                results.append(result)
            except Exception:
                pass
        return results

    def has_hooks(self, hook_name: str) -> bool:
        return bool(self._hooks.get(hook_name))


hook_registry = HookRegistry()
