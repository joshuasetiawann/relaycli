"""Plugin manifest — plugin.toml, a plugin directory's declarative
metadata (name/version/description/capabilities/hooks), parsed WITHOUT
executing any of the plugin's Python code. Reading a manifest is always
safe, independent of whether the plugin itself is trusted — that's what
lets `relaycli plugin list`/`info` describe an installed-but-not-yet-
loaded plugin, and what lets `relaycli plugin install` show a user what
a plugin declares *before* copying it into place at all.

CAPABILITIES is deliberately the same vocabulary as core.roles.
CAPABILITIES — one shared meaning for "read/write/exec/net" across
roles and plugins, not two independently-drifting vocabularies.

HONESTY NOTE (read this before assuming more than it says): capabilities
here are a declaration, not a sandbox. relaycli/plugins/loader.py runs a
plugin's Python with the full interpreter's privileges — nothing in this
module or the loader can stop a plugin from doing more than it declared.
The manifest's actual value is (1) a validated, structured description
`install`/`list`/`info` can show a user before they decide to trust a
plugin, matching the same "the user installing it IS the trust act" model
already used for skills/MCP servers in this codebase, and (2) rejecting
a malformed manifest outright — a real, if narrow, gate at install time.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_FILENAME = "plugin.toml"

# Matches relaycli.core.roles.CAPABILITIES exactly — see module docstring.
KNOWN_CAPABILITIES = frozenset({"read", "write", "exec", "net"})


class ManifestError(ValueError):
    """plugin.toml exists but is malformed — missing name:, or an
    unrecognized capability. Distinct from "no manifest" (None), which
    isn't an error — it means the plugin is dunder-attribute-only,
    still loadable exactly as it was before manifests existed."""


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = field(default=())
    # Hook event names the plugin *declares* it attaches to (on_tool_start,
    # etc.) — informational only, for `info` to show without running the
    # plugin's code; the loader still discovers real hooks by introspecting
    # on_* attributes after exec, and does not trust this list for that.
    hooks: tuple[str, ...] = field(default=())


def parse_manifest(text: str) -> PluginManifest:
    """Parse plugin.toml content. Raises ManifestError on anything that
    would silently produce a confusing plugin identity or a capability
    claim nothing recognizes — fail loud here, not as a mysterious
    KeyError three call frames into the loader."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML: {exc}") from exc

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError("plugin.toml needs a non-empty 'name'")

    capabilities = tuple(raw.get("capabilities") or ())
    unknown = [c for c in capabilities if c not in KNOWN_CAPABILITIES]
    if unknown:
        raise ManifestError(
            f"unknown capabilit{'y' if len(unknown) == 1 else 'ies'} {unknown} — "
            f"must be from {sorted(KNOWN_CAPABILITIES)}"
        )

    return PluginManifest(
        name=name.strip(),
        version=str(raw.get("version") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        capabilities=capabilities,
        hooks=tuple(raw.get("hooks") or ()),
    )


def load_manifest(plugin_dir: Path) -> PluginManifest | None:
    """None when plugin_dir has no plugin.toml — not an error, just an
    older-style plugin identified purely by its __plugin_name__ dunder
    (relaycli/plugins/loader.py's original, still-supported mechanism)."""
    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    return parse_manifest(manifest_path.read_text(encoding="utf-8"))
