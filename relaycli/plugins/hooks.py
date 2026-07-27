"""Hook registry — broadcast events to all loaded plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from relaycli.core.logging import get_logger

_log = get_logger(__name__)


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
        """Call every handler registered for hook_name. One handler raising
        does not stop the others — but unlike a bare swallow, the failure is
        logged with the hook name and handler identity so a broken plugin is
        diagnosable instead of just quietly contributing nothing."""
        results: list[Any] = []
        for handler in self._hooks.get(hook_name, []):
            try:
                result = handler(**kwargs)
                results.append(result)
            except Exception:
                handler_name = getattr(handler, "__qualname__", repr(handler))
                _log.error(
                    "hook '%s' handler %s raised", hook_name, handler_name, exc_info=True
                )
        return results

    def has_hooks(self, hook_name: str) -> bool:
        return bool(self._hooks.get(hook_name))


hook_registry = HookRegistry()
