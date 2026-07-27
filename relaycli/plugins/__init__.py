"""Plugin system for RelayCLI extensions."""

from relaycli.plugins.loader import discover_plugins, load_plugin, Plugin
from relaycli.plugins.hooks import HookRegistry, hook_registry

__all__ = ["discover_plugins", "load_plugin", "Plugin", "HookRegistry", "hook_registry"]
