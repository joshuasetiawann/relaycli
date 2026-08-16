"""Tests for `relaycli plugin ...` (plugin_cli.py).

manager.py reads plugin directories through `loader.PLUGIN_DIRS` (a
module-qualified reference, not a `from loader import PLUGIN_DIRS` copy
— see manager.py's own comment on why that distinction matters), so
patching relaycli.plugins.loader.PLUGIN_DIRS here is what actually
redirects every manager.py call plugin_cli.py's commands make (none of
them pass manager.py's plugin_dir= override — that's for unit tests of
manager.py itself, in test_plugins.py)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

import relaycli.plugins.loader as loader_mod
from relaycli.plugin_cli import plugin_app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(result) -> str:
    """Strip ANSI color codes and collapse whitespace (including line
    wraps Rich may insert mid-phrase) — this environment sets
    FORCE_COLOR, which makes Rich color even CliRunner's captured,
    non-terminal output, and a real user's terminal could do the same;
    asserting on colored text directly is fragile either way."""
    return " ".join(_ANSI_RE.sub("", result.output).split())


@pytest.fixture(autouse=True)
def _isolated_plugin_dir(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "installed"
    monkeypatch.setattr(loader_mod, "PLUGIN_DIRS", [plugin_dir])
    return plugin_dir


def _pkg_source(tmp_path, name="demo", capabilities=("read",)) -> Path:
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("def on_tool_start(**kw):\n    return None\n", encoding="utf-8")
    caps = ", ".join(f'"{c}"' for c in capabilities)
    (src / "plugin.toml").write_text(
        f'name = "{name}"\nversion = "1.0"\ndescription = "test plugin"\ncapabilities = [{caps}]\n', encoding="utf-8"
    )
    return src


def test_list_empty():
    result = runner.invoke(plugin_app, ["list"])
    assert result.exit_code == 0
    assert "no plugins installed" in _plain(result)


def test_install_with_yes_flag_skips_prompt(tmp_path):
    src = _pkg_source(tmp_path)
    result = runner.invoke(plugin_app, ["install", str(src), "-y"])
    assert result.exit_code == 0, result.output
    assert "installed" in _plain(result)
    assert "demo" in _plain(result)


def test_install_shows_declared_capabilities_before_confirming(tmp_path):
    src = _pkg_source(tmp_path, capabilities=("read", "exec"))
    result = runner.invoke(plugin_app, ["install", str(src), "-y"])
    assert "read, exec" in _plain(result)


def test_install_without_yes_requires_confirmation(tmp_path):
    src = _pkg_source(tmp_path)
    result = runner.invoke(plugin_app, ["install", str(src)], input="n\n")
    assert result.exit_code == 1
    assert "cancelled" in _plain(result)


def test_install_confirmed_interactively(tmp_path):
    src = _pkg_source(tmp_path)
    result = runner.invoke(plugin_app, ["install", str(src)], input="y\n")
    assert result.exit_code == 0
    assert "installed" in _plain(result)


def test_install_nonexistent_source_fails_cleanly(tmp_path):
    result = runner.invoke(plugin_app, ["install", str(tmp_path / "nope"), "-y"])
    assert result.exit_code == 2
    assert "does not exist" in _plain(result)


def test_install_duplicate_without_force_fails(tmp_path):
    src = _pkg_source(tmp_path)
    runner.invoke(plugin_app, ["install", str(src), "-y"])
    result = runner.invoke(plugin_app, ["install", str(src), "-y"])
    assert result.exit_code == 1
    assert "already installed" in _plain(result)


def test_install_duplicate_with_force_succeeds(tmp_path):
    src = _pkg_source(tmp_path)
    runner.invoke(plugin_app, ["install", str(src), "-y"])
    result = runner.invoke(plugin_app, ["install", str(src), "-y", "--force"])
    assert result.exit_code == 0


def test_list_shows_installed_plugin(tmp_path):
    src = _pkg_source(tmp_path)
    runner.invoke(plugin_app, ["install", str(src), "-y"])
    result = _plain(runner.invoke(plugin_app, ["list"]))
    assert "demo" in result
    assert "1.0" in result
    assert "read" in result


def test_info_shows_details(tmp_path):
    src = _pkg_source(tmp_path)
    runner.invoke(plugin_app, ["install", str(src), "-y"])
    result = runner.invoke(plugin_app, ["info", "demo"])
    assert result.exit_code == 0
    plain = _plain(result)
    assert "test plugin" in plain
    assert "read" in plain
    assert "tool_start" in plain  # hook discovered from on_tool_start


def test_info_unknown_plugin_fails():
    result = runner.invoke(plugin_app, ["info", "nope"])
    assert result.exit_code == 1
    assert "no plugin named" in _plain(result)


def test_remove_uninstalls(tmp_path):
    src = _pkg_source(tmp_path)
    runner.invoke(plugin_app, ["install", str(src), "-y"])
    result = runner.invoke(plugin_app, ["remove", "demo"])
    assert result.exit_code == 0
    assert "removed" in _plain(result)
    assert _plain(runner.invoke(plugin_app, ["list"])).count("no plugins installed") == 1


def test_remove_unknown_plugin_fails():
    result = runner.invoke(plugin_app, ["remove", "nope"])
    assert result.exit_code == 1
    assert "no plugin named" in _plain(result)
