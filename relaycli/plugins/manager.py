"""Plugin management — install/list/remove/info, the write side of the
plugin system (relaycli/plugins/loader.py is read-only: discover and
load whatever's already in place). Backs `relaycli plugin ...`
(plugin_cli.py).

Install is local-path only, deliberately: copy a directory (or a single
.py file) into ~/.relaycli/plugins/. Fetching a plugin from a URL/registry
is a materially different, much bigger trust question (supply-chain risk
on top of the "arbitrary Python execution" risk every plugin already
carries) and isn't attempted here — a user who wants a remote plugin
clones/downloads it themselves first, same as they would before pointing
`pip install` at a local checkout.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from relaycli.plugins import loader
from relaycli.plugins.loader import Plugin, load_plugin
from relaycli.plugins.manifest import ManifestError, is_safe_plugin_name, load_manifest

# `import loader` + `loader.PLUGIN_DIRS`, not `from loader import PLUGIN_DIRS`
# — the latter binds a second, independent name to the same list object at
# import time; reassigning loader.PLUGIN_DIRS later (as a test monkeypatch,
# or any future caller) then silently doesn't affect this module's copy.
# Every read below goes through the loader module reference so there is
# exactly one name that ever needs patching.


class PluginInstallError(ValueError):
    """The source isn't installable as-is — not a plugin execution
    failure (that's Plugin.error, surfaced only after install succeeds
    and the plugin is actually loaded)."""


@dataclass(frozen=True)
class InstallPreview:
    """What `install_plugin` would do, computed and returned *before*
    anything is copied — the caller (plugin_cli.py) shows this to the
    user, since "the user choosing to install it" is this codebase's
    trust model for a plugin (matching skills/MCP), not any runtime
    sandboxing this preview step can't provide."""

    name: str
    version: str
    description: str
    capabilities: tuple[str, ...]
    source: Path
    destination: Path
    already_installed: bool


def _target_dir(plugin_dir: Path | None) -> Path:
    return plugin_dir if plugin_dir is not None else loader.PLUGIN_DIRS[0]


def preview_install(source: Path, *, plugin_dir: Path | None = None) -> InstallPreview:
    """Validate `source` and describe what installing it would do,
    without copying anything or executing the plugin's code.

    plugin_dir overrides the real ~/.relaycli/plugins/ — tests use this
    to point at a tmp_path instead of monkeypatching module state."""
    source = source.expanduser().resolve()
    if not source.exists():
        raise PluginInstallError(f"'{source}' does not exist")
    is_pkg = source.is_dir()
    if is_pkg and not (source / "__init__.py").is_file():
        raise PluginInstallError(f"'{source}' is a directory but has no __init__.py")
    if not is_pkg and source.suffix != ".py":
        raise PluginInstallError(f"'{source}' is neither a plugin package dir nor a .py file")

    manifest = load_manifest(source) if is_pkg else None  # ManifestError propagates as-is
    name = manifest.name if manifest else source.stem
    # manifest.name is already validated by parse_manifest; source.stem
    # (the no-manifest / single-file fallback) gets the identical check
    # here for the same reason — either one becomes a path component
    # below, and destination's own containment check further down is
    # defense in depth, not a reason to skip validating the name itself.
    if not is_safe_plugin_name(name):
        raise PluginInstallError(
            f"invalid plugin name {name!r} — must be a single path segment, "
            "not '.', '..', or contain '/' or '\\'"
        )
    target_dir = _target_dir(plugin_dir).resolve()
    destination = target_dir / (name if is_pkg else f"{name}.py")
    if destination.parent != target_dir:
        # Should be unreachable given the name check above — kept as an
        # explicit, independent guard (matching core/context.py's
        # ProjectContext.resolve() containment check) rather than trusting
        # a single validation layer to never have a gap.
        raise PluginInstallError(f"refusing to install outside {target_dir}")
    return InstallPreview(
        name=name,
        version=manifest.version if manifest else "",
        description=manifest.description if manifest else "",
        capabilities=manifest.capabilities if manifest else (),
        source=source,
        destination=destination,
        already_installed=destination.exists(),
    )


def install_plugin(source: Path, *, overwrite: bool = False, plugin_dir: Path | None = None) -> Plugin:
    """Copy `source` into the plugins dir and load it. Raises
    PluginInstallError before touching the filesystem if the source is
    invalid or already installed (unless overwrite=True) — installing is
    all-or-nothing, never a partial copy left behind on a rejected call."""
    preview = preview_install(source, plugin_dir=plugin_dir)
    if preview.already_installed and not overwrite:
        raise PluginInstallError(
            f"'{preview.name}' is already installed at {preview.destination} "
            "(pass overwrite=True / --force to replace it)"
        )

    _target_dir(plugin_dir).mkdir(parents=True, exist_ok=True)
    if preview.destination.exists():
        if preview.destination.is_dir():
            shutil.rmtree(preview.destination)
        else:
            preview.destination.unlink()
    if preview.source.is_dir():
        shutil.copytree(preview.source, preview.destination)
    else:
        shutil.copy2(preview.source, preview.destination)

    return load_plugin(preview.destination)


def list_installed(*, plugin_dir: Path | None = None) -> list[Plugin]:
    paths = loader.discover_plugins_in([plugin_dir]) if plugin_dir is not None else loader.discover_plugins()
    return [load_plugin(path) for path in paths]


def get_installed(name: str, *, plugin_dir: Path | None = None) -> Plugin | None:
    for plugin in list_installed(plugin_dir=plugin_dir):
        if plugin.name == name:
            return plugin
    return None


def remove_plugin(name: str, *, plugin_dir: Path | None = None) -> bool:
    """True if something named `name` was found (by discovered plugin
    name, which — per load_plugin — is the manifest name when one
    exists) and removed; False if nothing matched, filesystem
    untouched."""
    paths = loader.discover_plugins_in([plugin_dir]) if plugin_dir is not None else loader.discover_plugins()
    for path in paths:
        plugin = load_plugin(path)
        if plugin.name != name:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    return False
