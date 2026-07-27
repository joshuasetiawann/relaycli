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


@dataclass
class Plugin:
    name: str
    module: object
    version: str = ""
    description: str = ""
    hooks: dict[str, list[Any]] = field(default_factory=dict)


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


def load_plugin(path: Path) -> Plugin | None:
    try:
        if path.is_dir():
            spec = importlib.util.spec_from_file_location(path.name, str(path / "__init__.py"))
        else:
            spec = importlib.util.spec_from_file_location(path.stem, str(path))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        name = getattr(module, "__plugin_name__", path.stem)
        version = getattr(module, "__version__", "")
        description = getattr(module, "__description__", "")
        hooks: dict[str, list[Any]] = {}
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if attr_name.startswith("on_") and callable(attr):
                hook_name = attr_name[3:]
                hooks.setdefault(hook_name, []).append(attr)
        return Plugin(name=name, module=module, version=version, description=description, hooks=hooks)
    except Exception:
        return None


def load_all_plugins(extra_dirs: list[Path] | None = None) -> dict[str, Plugin]:
    plugins: dict[str, Plugin] = {}
    for path in discover_plugins(extra_dirs):
        plugin = load_plugin(path)
        if plugin is not None:
            plugins[plugin.name] = plugin
    return plugins
