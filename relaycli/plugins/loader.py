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
from relaycli.plugins.manifest import ManifestError, PluginManifest, load_manifest

_log = get_logger(__name__)


@dataclass
class Plugin:
    name: str
    module: object | None = None
    version: str = ""
    description: str = ""
    hooks: dict[str, list[Any]] = field(default_factory=dict)
    error: str | None = None
    # From plugin.toml, if the plugin has one — empty for the older,
    # still-supported dunder-attribute-only style. A declaration, not a
    # sandbox guarantee; see manifest.py's module docstring.
    capabilities: tuple[str, ...] = field(default=())
    manifest: PluginManifest | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


PLUGIN_DIRS: list[Path] = [
    CONFIG_DIR / "plugins",
]


def _scan_dir(d: Path) -> list[Path]:
    if not d.exists():
        return []
    found: list[Path] = []
    for entry in sorted(d.iterdir()):
        if entry.suffix == ".py" and not entry.name.startswith("_"):
            found.append(entry)
        elif entry.is_dir() and (entry / "__init__.py").exists():
            found.append(entry)
    return found


def discover_plugins(extra_dirs: list[Path] | None = None) -> list[Path]:
    """PLUGIN_DIRS plus extra_dirs — additive, for callers that want the
    real plugin dirs plus somewhere else. relaycli.plugins.manager wants
    the opposite (scan *only* an explicit override, e.g. tmp_path in a
    test, never PLUGIN_DIRS too) and uses discover_plugins_in for that."""
    found: list[Path] = []
    for d in PLUGIN_DIRS + (extra_dirs or []):
        found.extend(_scan_dir(d))
    return found


def discover_plugins_in(dirs: list[Path]) -> list[Path]:
    """Scan exactly `dirs`, not PLUGIN_DIRS plus them."""
    found: list[Path] = []
    for d in dirs:
        found.extend(_scan_dir(d))
    return found


def load_plugin(path: Path) -> Plugin:
    """Load a single plugin. Never raises and never silently disappears a
    failure: a plugin that fails to import is still returned, with `.error`
    set and the traceback logged, so a typo in one plugin is visible instead
    of the plugin just vanishing from the loaded set.

    plugin.toml (directory plugins only) is read first, before any Python
    executes — a malformed manifest is a load failure (fail loud, matching
    a real syntax error in the plugin's own code), but a *missing* one
    isn't; that's just the older dunder-attribute-only style, still fully
    supported. When present, the manifest's name/version/description win
    outright over the dunders (no per-field merging) and its capabilities
    ride along on the returned Plugin — see manifest.py for what that
    does and does not guarantee.
    """
    fallback_name = path.name if path.is_dir() else path.stem
    manifest: PluginManifest | None = None
    if path.is_dir():
        try:
            manifest = load_manifest(path)
        except ManifestError as exc:
            _log.error("plugin '%s' has an invalid plugin.toml: %s", fallback_name, exc)
            return Plugin(name=fallback_name, error=f"invalid plugin.toml: {exc}")
    try:
        if path.is_dir():
            spec = importlib.util.spec_from_file_location(path.name, str(path / "__init__.py"))
        else:
            spec = importlib.util.spec_from_file_location(path.stem, str(path))
        if spec is None or spec.loader is None:
            message = f"could not create an import spec for {path}"
            _log.error("plugin '%s' failed to load: %s", fallback_name, message)
            return Plugin(name=fallback_name, error=message, manifest=manifest)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        name = manifest.name if manifest else getattr(module, "__plugin_name__", fallback_name)
        version = manifest.version if manifest else getattr(module, "__version__", "")
        description = manifest.description if manifest else getattr(module, "__description__", "")
        hooks: dict[str, list[Any]] = {}
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if attr_name.startswith("on_") and callable(attr):
                hook_name = attr_name[3:]
                hooks.setdefault(hook_name, []).append(attr)
        return Plugin(
            name=name, module=module, version=version, description=description, hooks=hooks,
            capabilities=manifest.capabilities if manifest else (), manifest=manifest,
        )
    except Exception as exc:
        _log.error("plugin '%s' failed to load: %s", fallback_name, exc, exc_info=True)
        return Plugin(name=fallback_name, error=f"{type(exc).__name__}: {exc}", manifest=manifest)


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
