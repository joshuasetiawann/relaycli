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
    (tmp_path / "one.py").write_text("__plugin_name__ = 'one'\n")
    (tmp_path / "_ignored.py").write_text("should not be discovered\n")
    (tmp_path / "not_a_plugin.txt").write_text("irrelevant\n")
    pkg = tmp_path / "pkgplugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__plugin_name__ = 'pkgplugin'\n")
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
        "    return 'started'\n"
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
    (tmp_path / "nameless.py").write_text("x = 1\n")
    plugin = load_plugin(tmp_path / "nameless.py")
    assert plugin.ok
    assert plugin.name == "nameless"
    assert plugin.version == ""
    assert plugin.hooks == {}


def test_load_plugin_loads_a_package_directory(tmp_path):
    pkg = tmp_path / "pkgplugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__plugin_name__ = 'pkgplugin'\n")
    plugin = load_plugin(pkg)
    assert plugin.ok
    assert plugin.name == "pkgplugin"


# --- loader: failure is visible, not silent (the Stage 1 fix) ----------
def test_load_plugin_syntax_error_is_visible_not_dropped(tmp_path):
    (tmp_path / "broken.py").write_text("this is not valid python !!\n")
    plugin = load_plugin(tmp_path / "broken.py")
    assert isinstance(plugin, Plugin)
    assert not plugin.ok
    assert plugin.name == "broken"
    assert "SyntaxError" in plugin.error


def test_load_plugin_runtime_error_at_import_time_is_visible(tmp_path):
    (tmp_path / "explodes.py").write_text("raise RuntimeError('boom at import')\n")
    plugin = load_plugin(tmp_path / "explodes.py")
    assert not plugin.ok
    assert "boom at import" in plugin.error


def test_load_all_plugins_includes_failed_plugins_not_just_successful_ones(tmp_path):
    (tmp_path / "good.py").write_text("__plugin_name__ = 'good'\n")
    (tmp_path / "bad.py").write_text("raise ValueError('nope')\n")

    plugins = load_all_plugins(extra_dirs=[tmp_path])

    assert set(plugins) == {"good", "bad"}
    assert plugins["good"].ok
    assert not plugins["bad"].ok
    assert "nope" in plugins["bad"].error


def test_load_plugin_logs_the_failure(tmp_path, caplog):
    (tmp_path / "broken.py").write_text("raise RuntimeError('logged boom')\n")
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
