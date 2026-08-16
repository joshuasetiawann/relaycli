"""Tests for the plugin loader and hook registry — first coverage for this
subsystem. Focused on Stage 1 of the v2 orchestrator prompt: a plugin (or
one of its hooks) that raises must be visible and diagnosable, never
silently dropped."""

from __future__ import annotations

from pathlib import Path

import pytest

from relaycli.plugins.hooks import HookRegistry
from relaycli.plugins.loader import Plugin, discover_plugins, load_all_plugins, load_plugin


# --- loader: discovery -------------------------------------------------
def test_discover_plugins_finds_py_files_and_packages(tmp_path):
    (tmp_path / "one.py").write_text("__plugin_name__ = 'one'\n", encoding="utf-8")
    (tmp_path / "_ignored.py").write_text("should not be discovered\n", encoding="utf-8")
    (tmp_path / "not_a_plugin.txt").write_text("irrelevant\n", encoding="utf-8")
    pkg = tmp_path / "pkgplugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__plugin_name__ = 'pkgplugin'\n", encoding="utf-8")
    empty_dir = tmp_path / "not_a_package"
    empty_dir.mkdir()

    found = discover_plugins(extra_dirs=[tmp_path])
    names = {p.name for p in found}
    assert names == {"one.py", "pkgplugin"}


def test_discover_plugins_skips_missing_dirs():
    assert discover_plugins(extra_dirs=[Path("/no/such/dir/at/all")]) == []


# --- loader: successful load --------------------------------------------
def test_load_plugin_reads_metadata_and_hooks(tmp_path):
    (tmp_path / "greeter.py").write_text(
        "__plugin_name__ = 'greeter'\n"
        "__version__ = '1.2.3'\n"
        "__description__ = 'says hi'\n"
        "def on_session_start(**kw):\n"
        "    return 'started'\n", encoding="utf-8"
    )
    plugin = load_plugin(tmp_path / "greeter.py")
    assert plugin.ok
    assert plugin.error is None
    assert plugin.name == "greeter"
    assert plugin.version == "1.2.3"
    assert plugin.description == "says hi"
    assert "session_start" in plugin.hooks
    assert plugin.hooks["session_start"][0]() == "started"


def test_load_plugin_defaults_name_to_filename_without_dunder(tmp_path):
    (tmp_path / "nameless.py").write_text("x = 1\n", encoding="utf-8")
    plugin = load_plugin(tmp_path / "nameless.py")
    assert plugin.ok
    assert plugin.name == "nameless"
    assert plugin.version == ""
    assert plugin.hooks == {}


def test_load_plugin_loads_a_package_directory(tmp_path):
    pkg = tmp_path / "pkgplugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__plugin_name__ = 'pkgplugin'\n", encoding="utf-8")
    plugin = load_plugin(pkg)
    assert plugin.ok
    assert plugin.name == "pkgplugin"


# --- loader: failure is visible, not silent (the Stage 1 fix) ----------
def test_load_plugin_syntax_error_is_visible_not_dropped(tmp_path):
    (tmp_path / "broken.py").write_text("this is not valid python !!\n", encoding="utf-8")
    plugin = load_plugin(tmp_path / "broken.py")
    assert isinstance(plugin, Plugin)
    assert not plugin.ok
    assert plugin.name == "broken"
    assert "SyntaxError" in plugin.error


def test_load_plugin_runtime_error_at_import_time_is_visible(tmp_path):
    (tmp_path / "explodes.py").write_text("raise RuntimeError('boom at import')\n", encoding="utf-8")
    plugin = load_plugin(tmp_path / "explodes.py")
    assert not plugin.ok
    assert "boom at import" in plugin.error


def test_load_all_plugins_includes_failed_plugins_not_just_successful_ones(tmp_path):
    (tmp_path / "good.py").write_text("__plugin_name__ = 'good'\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("raise ValueError('nope')\n", encoding="utf-8")

    plugins = load_all_plugins(extra_dirs=[tmp_path])

    assert set(plugins) == {"good", "bad"}
    assert plugins["good"].ok
    assert not plugins["bad"].ok
    assert "nope" in plugins["bad"].error


def test_load_plugin_logs_the_failure(tmp_path, caplog):
    (tmp_path / "broken.py").write_text("raise RuntimeError('logged boom')\n", encoding="utf-8")
    with caplog.at_level("ERROR", logger="relaycli.plugins.loader"):
        load_plugin(tmp_path / "broken.py")
    assert any("broken" in r.message and "failed to load" in r.message for r in caplog.records)


# --- hooks: one bad handler doesn't kill the others, and it's logged ---
def test_hook_registry_trigger_calls_all_handlers():
    reg = HookRegistry()
    calls = []
    reg.register("evt", lambda **kw: calls.append(kw))
    reg.register("evt", lambda **kw: calls.append(kw))
    reg.trigger("evt", x=1)
    assert len(calls) == 2


def test_hook_registry_trigger_survives_one_bad_handler_and_keeps_others():
    reg = HookRegistry()

    def good(**kw):
        return "ok"

    def bad(**kw):
        raise ValueError("boom")

    reg.register("evt", good)
    reg.register("evt", bad)
    results = reg.trigger("evt")
    assert results == ["ok"]


def test_hook_registry_trigger_logs_the_failure(caplog):
    reg = HookRegistry()

    def bad_handler(**kw):
        raise ValueError("logged hook boom")

    reg.register("evt", bad_handler)
    with caplog.at_level("ERROR", logger="relaycli.plugins.hooks"):
        reg.trigger("evt")
    assert any(
        "evt" in r.message and "bad_handler" in r.message for r in caplog.records
    )


def test_hook_registry_unregister_and_has_hooks():
    reg = HookRegistry()
    handler = lambda **kw: None
    reg.register("evt", handler)
    assert reg.has_hooks("evt")
    reg.unregister("evt", handler)
    assert not reg.has_hooks("evt")
    reg.unregister("evt", handler)  # idempotent, no error


def test_hook_registry_has_hooks_false_for_unknown_event():
    reg = HookRegistry()
    assert not reg.has_hooks("nonexistent")


# --- manifest ----------------------------------------------------------
def test_parse_manifest_reads_all_fields():
    from relaycli.plugins.manifest import parse_manifest

    m = parse_manifest(
        'name = "demo"\nversion = "1.0"\ndescription = "d"\n'
        'capabilities = ["read", "write"]\nhooks = ["on_tool_start"]\n'
    )
    assert m.name == "demo"
    assert m.version == "1.0"
    assert m.description == "d"
    assert m.capabilities == ("read", "write")
    assert m.hooks == ("on_tool_start",)


def test_parse_manifest_requires_name():
    from relaycli.plugins.manifest import ManifestError, parse_manifest

    with pytest.raises(ManifestError, match="name"):
        parse_manifest('version = "1.0"\n')


def test_parse_manifest_rejects_unknown_capability():
    from relaycli.plugins.manifest import ManifestError, parse_manifest

    with pytest.raises(ManifestError, match="unknown"):
        parse_manifest('name = "x"\ncapabilities = ["launch_nukes"]\n')


@pytest.mark.parametrize("name", [
    "../../../etc/passwd", "..", ".", "foo/bar", "/etc/passwd", "a/../../b", "foo/",
])
def test_parse_manifest_rejects_path_traversal_in_name(name):
    """Security fix: name becomes a path component in manager.py's
    install_plugin (destination = plugins_dir / name, before
    shutil.copytree/rmtree) — an untrusted manifest must not be able to
    escape the plugins directory through it."""
    from relaycli.plugins.manifest import ManifestError, parse_manifest

    with pytest.raises(ManifestError, match="path segment"):
        parse_manifest(f'name = "{name}"\n')


def test_is_safe_plugin_name():
    from relaycli.plugins.manifest import is_safe_plugin_name

    assert is_safe_plugin_name("my-plugin")
    assert is_safe_plugin_name("foo")
    for unsafe in ("../../etc/passwd", "..", ".", "foo/bar", "/etc/passwd", ""):
        assert not is_safe_plugin_name(unsafe), unsafe


def test_parse_manifest_rejects_invalid_toml():
    from relaycli.plugins.manifest import ManifestError, parse_manifest

    with pytest.raises(ManifestError):
        parse_manifest("not valid { toml [[[")


def test_parse_manifest_capabilities_match_core_roles_vocabulary():
    from relaycli.core.roles import CAPABILITIES
    from relaycli.plugins.manifest import KNOWN_CAPABILITIES

    assert set(CAPABILITIES) == KNOWN_CAPABILITIES


def test_load_manifest_none_when_absent(tmp_path):
    from relaycli.plugins.manifest import load_manifest

    assert load_manifest(tmp_path) is None


def test_load_manifest_reads_plugin_toml(tmp_path):
    from relaycli.plugins.manifest import load_manifest

    (tmp_path / "plugin.toml").write_text('name = "demo"\ncapabilities = ["net"]\n', encoding="utf-8")
    manifest = load_manifest(tmp_path)
    assert manifest.name == "demo"
    assert manifest.capabilities == ("net",)


# --- loader + manifest integration --------------------------------------
def test_load_plugin_prefers_manifest_over_dunders(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__plugin_name__ = 'dunder-name'\n__version__ = '0.0.1'\n", encoding="utf-8")
    (pkg / "plugin.toml").write_text(
        'name = "manifest-name"\nversion = "2.0"\ndescription = "from manifest"\ncapabilities = ["read"]\n', encoding="utf-8"
    )
    plugin = load_plugin(pkg)
    assert plugin.ok
    assert plugin.name == "manifest-name"
    assert plugin.version == "2.0"
    assert plugin.description == "from manifest"
    assert plugin.capabilities == ("read",)
    assert plugin.manifest is not None


def test_load_plugin_without_manifest_still_works(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__plugin_name__ = 'old-style'\n", encoding="utf-8")
    plugin = load_plugin(pkg)
    assert plugin.ok
    assert plugin.name == "old-style"
    assert plugin.capabilities == ()
    assert plugin.manifest is None


def test_load_plugin_invalid_manifest_fails_loud_before_exec(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    # If exec ran despite the bad manifest, this would leave evidence —
    # it must not, since the manifest check happens first.
    (pkg / "__init__.py").write_text("(tmp_path / 'ran.txt').write_text('yes')\n", encoding="utf-8")
    (pkg / "plugin.toml").write_text('capabilities = ["read"]\n', encoding="utf-8")  # missing name
    plugin = load_plugin(pkg)
    assert not plugin.ok
    assert "plugin.toml" in plugin.error
    assert not (pkg / "ran.txt").exists()


def test_single_file_plugins_ignore_sibling_manifests(tmp_path):
    """plugin.toml only applies to package directories — a lone .py file
    plugin has no directory of its own to carry one."""
    (tmp_path / "solo.py").write_text("__plugin_name__ = 'solo'\n", encoding="utf-8")
    (tmp_path / "plugin.toml").write_text('name = "should-not-apply"\n', encoding="utf-8")
    plugin = load_plugin(tmp_path / "solo.py")
    assert plugin.name == "solo"
    assert plugin.manifest is None


# --- manager: install/list/info/remove ----------------------------------
def _pkg_source(tmp_path, name="demo", *, manifest=True, capabilities=("read",)) -> Path:
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("def on_tool_start(**kw):\n    return None\n", encoding="utf-8")
    if manifest:
        caps = ", ".join(f'"{c}"' for c in capabilities)
        (src / "plugin.toml").write_text(
            f'name = "{name}"\nversion = "1.0"\ndescription = "test plugin"\ncapabilities = [{caps}]\n', encoding="utf-8"
        )
    return src


def test_preview_install_describes_without_copying(tmp_path):
    from relaycli.plugins.manager import preview_install

    src = _pkg_source(tmp_path)
    dest_dir = tmp_path / "installed"
    preview = preview_install(src, plugin_dir=dest_dir)
    assert preview.name == "demo"
    assert preview.version == "1.0"
    assert preview.capabilities == ("read",)
    assert preview.already_installed is False
    assert not dest_dir.exists()  # preview must not create anything


def test_preview_install_rejects_missing_source(tmp_path):
    from relaycli.plugins.manager import PluginInstallError, preview_install

    with pytest.raises(PluginInstallError, match="does not exist"):
        preview_install(tmp_path / "nope", plugin_dir=tmp_path / "installed")


def test_preview_install_rejects_dir_without_init(tmp_path):
    from relaycli.plugins.manager import PluginInstallError, preview_install

    bad = tmp_path / "not-a-package"
    bad.mkdir()
    with pytest.raises(PluginInstallError, match="__init__.py"):
        preview_install(bad, plugin_dir=tmp_path / "installed")


def test_preview_install_propagates_manifest_errors(tmp_path):
    from relaycli.plugins.manifest import ManifestError
    from relaycli.plugins.manager import preview_install

    src = tmp_path / "src" / "bad"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "plugin.toml").write_text("capabilities = ['read']", encoding="utf-8")  # valid TOML, but missing required 'name'
    with pytest.raises(ManifestError):
        preview_install(src, plugin_dir=tmp_path / "installed")


def test_install_plugin_rejects_path_traversal_via_manifest_name(tmp_path):
    """The actual attack this session's security review caught: a
    malicious plugin.toml declaring name = "../../../somewhere" must not
    let install_plugin's shutil.copytree escape the plugins directory."""
    from relaycli.plugins.manager import PluginInstallError, install_plugin
    from relaycli.plugins.manifest import ManifestError

    escape_target = tmp_path / "outside" / "should-not-exist"
    src = tmp_path / "src" / "evil"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "plugin.toml").write_text('name = "../outside/should-not-exist"\n', encoding="utf-8")
    plugin_dir = tmp_path / "installed"
    plugin_dir.mkdir()

    with pytest.raises((PluginInstallError, ManifestError)):
        install_plugin(src, plugin_dir=plugin_dir)
    assert not escape_target.exists()
    # Nothing should have landed inside the real plugins dir either.
    assert list(plugin_dir.iterdir()) == []


def test_install_plugin_accepts_a_multi_dot_filename(tmp_path):
    """The no-manifest fallback name (source.stem) runs through the same
    is_safe_plugin_name check as a manifest's name. Path("my.plugin.py")
    .stem is "my.plugin" — internal dots, but still one safe path
    component (not a directory separator or a '..' reference) —
    confirms the validation doesn't produce a false positive on an
    unusual-but-legitimate filename."""
    from relaycli.plugins.manager import install_plugin

    src = tmp_path / "src"
    src.mkdir()
    weird = src / "my.plugin.py"
    weird.write_text("__plugin_name__ = 'weird'\n", encoding="utf-8")
    plugin_dir = tmp_path / "installed"

    plugin = install_plugin(weird, plugin_dir=plugin_dir)
    assert plugin.ok
    assert (plugin_dir / "my.plugin.py").is_file()


def test_install_plugin_copies_and_loads(tmp_path):
    from relaycli.plugins.manager import install_plugin, list_installed

    src = _pkg_source(tmp_path)
    dest_dir = tmp_path / "installed"
    plugin = install_plugin(src, plugin_dir=dest_dir)
    assert plugin.ok
    assert plugin.name == "demo"
    assert (dest_dir / "demo").is_dir()
    assert {p.name for p in list_installed(plugin_dir=dest_dir)} == {"demo"}


def test_install_plugin_single_file(tmp_path):
    from relaycli.plugins.manager import install_plugin

    src = tmp_path / "solo.py"
    src.write_text("__plugin_name__ = 'solo'\n", encoding="utf-8")
    dest_dir = tmp_path / "installed"
    plugin = install_plugin(src, plugin_dir=dest_dir)
    assert plugin.ok
    assert (dest_dir / "solo.py").is_file()


def test_install_plugin_rejects_duplicate_without_overwrite(tmp_path):
    from relaycli.plugins.manager import PluginInstallError, install_plugin

    src = _pkg_source(tmp_path)
    dest_dir = tmp_path / "installed"
    install_plugin(src, plugin_dir=dest_dir)
    with pytest.raises(PluginInstallError, match="already installed"):
        install_plugin(src, plugin_dir=dest_dir)


def test_install_plugin_overwrite_replaces(tmp_path):
    from relaycli.plugins.manager import install_plugin

    src = _pkg_source(tmp_path)
    dest_dir = tmp_path / "installed"
    install_plugin(src, plugin_dir=dest_dir)
    # Change the source, reinstall with overwrite — the new content should land.
    (src / "plugin.toml").write_text('name = "demo"\nversion = "2.0"\ndescription = "d"\ncapabilities = []\n', encoding="utf-8")
    plugin = install_plugin(src, overwrite=True, plugin_dir=dest_dir)
    assert plugin.version == "2.0"


def test_install_plugin_never_partially_copies_on_rejection(tmp_path):
    """A rejected install (duplicate, no overwrite) must leave the
    existing installation exactly as it was — not touch the filesystem
    at all for the rejected attempt."""
    from relaycli.plugins.manager import PluginInstallError, install_plugin

    src = _pkg_source(tmp_path)
    dest_dir = tmp_path / "installed"
    install_plugin(src, plugin_dir=dest_dir)
    before = (dest_dir / "demo" / "plugin.toml").read_text(encoding="utf-8")
    (src / "plugin.toml").write_text('name = "demo"\nversion = "999"\ndescription = "d"\ncapabilities = []\n', encoding="utf-8")
    with pytest.raises(PluginInstallError):
        install_plugin(src, plugin_dir=dest_dir)
    assert (dest_dir / "demo" / "plugin.toml").read_text(encoding="utf-8") == before


def test_get_installed_finds_by_manifest_name(tmp_path):
    from relaycli.plugins.manager import get_installed, install_plugin

    src = _pkg_source(tmp_path)
    dest_dir = tmp_path / "installed"
    install_plugin(src, plugin_dir=dest_dir)
    found = get_installed("demo", plugin_dir=dest_dir)
    assert found is not None
    assert found.description == "test plugin"
    assert get_installed("nope", plugin_dir=dest_dir) is None


def test_remove_plugin_deletes_directory(tmp_path):
    from relaycli.plugins.manager import install_plugin, list_installed, remove_plugin

    src = _pkg_source(tmp_path)
    dest_dir = tmp_path / "installed"
    install_plugin(src, plugin_dir=dest_dir)
    assert remove_plugin("demo", plugin_dir=dest_dir) is True
    assert list_installed(plugin_dir=dest_dir) == []
    assert not (dest_dir / "demo").exists()


def test_remove_plugin_false_when_not_found(tmp_path):
    from relaycli.plugins.manager import remove_plugin

    assert remove_plugin("nope", plugin_dir=tmp_path / "installed") is False


def test_list_installed_with_override_does_not_see_real_plugin_dirs(tmp_path, monkeypatch):
    """The hermeticity guarantee every other test here relies on: an
    explicit plugin_dir must fully replace PLUGIN_DIRS, not add to it —
    caught once already (discover_plugins is additive by design for its
    own callers; manager.py needed discover_plugins_in instead)."""
    import relaycli.plugins.loader as loader_mod
    from relaycli.plugins.manager import install_plugin, list_installed

    real_dir = tmp_path / "real-plugin-dirs"
    real_dir.mkdir()
    monkeypatch.setattr(loader_mod, "PLUGIN_DIRS", [real_dir])
    real_src = _pkg_source(tmp_path, name="real-one")
    install_plugin(real_src, plugin_dir=real_dir)  # lands in the "real" dir

    override_dir = tmp_path / "override"
    assert list_installed(plugin_dir=override_dir) == []  # must not see real-one
