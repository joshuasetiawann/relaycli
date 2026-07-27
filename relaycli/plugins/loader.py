"""Plugin loader — discover and load plugins from configured paths."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relaycli.core.config import CONFIG_DIR
from relaycli.core.logging import get_logger

_log = get_logger(__name__)


@dataclass
class Plugin:
    name: str
    module: object | None = None
    version: str = ""
    description: str = ""
    hooks: dict[str, list[Any]] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


PLUGIN_DIRS: list[Path] = [
    CONFIG_DIR / "plugins",
]


def discover_plugins(extra_dirs: list[Path] | None = None) -> list[Path]:
    found: list[Path] = []
    for d in PLUGIN_DIRS + (extra_dirs or []):
        if not d.exists():
            continue
        for entry in sorted(d.iterdir()):
            if entry.suffix == ".py" and not entry.name.startswith("_"):
                found.append(entry)
            elif entry.is_dir() and (entry / "__init__.py").exists():
                found.append(entry)
    return found


def load_plugin(path: Path) -> Plugin:
    """Load a single plugin. Never raises and never silently disappears a
    failure: a plugin that fails to import is still returned, with `.error`
    set and the traceback logged, so a typo in one plugin is visible instead
    of the plugin just vanishing from the loaded set."""
    fallback_name = path.name if path.is_dir() else path.stem
    try:
        if path.is_dir():
            spec = importlib.util.spec_from_file_location(path.name, str(path / "__init__.py"))
        else:
            spec = importlib.util.spec_from_file_location(path.stem, str(path))
        if spec is None or spec.loader is None:
            message = f"could not create an import spec for {path}"
            _log.error("plugin '%s' failed to load: %s", fallback_name, message)
            return Plugin(name=fallback_name, error=message)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        name = getattr(module, "__plugin_name__", fallback_name)
        version = getattr(module, "__version__", "")
        description = getattr(module, "__description__", "")
        hooks: dict[str, list[Any]] = {}
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if attr_name.startswith("on_") and callable(attr):
                hook_name = attr_name[3:]
                hooks.setdefault(hook_name, []).append(attr)
        return Plugin(name=name, module=module, version=version, description=description, hooks=hooks)
    except Exception as exc:
        _log.error("plugin '%s' failed to load: %s", fallback_name, exc, exc_info=True)
        return Plugin(name=fallback_name, error=f"{type(exc).__name__}: {exc}")


def load_all_plugins(extra_dirs: list[Path] | None = None) -> dict[str, Plugin]:
    """Load every discovered plugin, keyed by name. Failed plugins are
    included (Plugin.ok is False, Plugin.error explains why) rather than
    dropped, so callers can report load failures instead of undercounting
    silently."""
    plugins: dict[str, Plugin] = {}
    for path in discover_plugins(extra_dirs):
        plugin = load_plugin(path)
        plugins[plugin.name] = plugin
    return plugins
