"""Installer script smoke tests."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_install_script_is_valid_shell():
    if shutil.which("sh") is None:
        pytest.skip("POSIX installer syntax requires a POSIX shell")
    subprocess.run(["sh", "-n", str(ROOT / "scripts/install.sh")], check=True)


def test_install_script_has_smoke_check_and_repair_path():
    text = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "check_command" in text
    assert "installed_command" in text
    assert '[ -x "$BIN_DIR/relaycli" ]' in text
    assert "repairing with private virtualenv" in text
    assert "import typer, rich, pydantic" in text


def test_readme_documents_one_line_installer():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scripts/install.sh | sh" in readme
    assert "Ollama" in readme and "n8n" in readme


def test_wheel_contains_runtime_assets(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    if importlib.util.find_spec("pip") is not None:
        command = [
            sys.executable, "-m", "pip", "wheel", str(ROOT),
            "-w", str(wheelhouse), "--no-deps",
        ]
    elif uv := shutil.which("uv"):
        # Python installations managed by uv intentionally may not bundle
        # pip.  The artifact contract is what matters here, not which
        # standards-compliant frontend invoked the same build backend.
        command = [uv, "build", "--wheel", "--out-dir", str(wheelhouse), str(ROOT)]
    else:
        pytest.skip("wheel test requires pip or uv")
    subprocess.run(
        command, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    wheel = next(wheelhouse.glob("relaycli-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "relaycli/web_ui.html" in names
    assert "relaycli/styles.css" in names
    assert "relaycli/app.js" in names
    assert "relaycli/tools/create_folder.py" in names
    for skill in ("brainstorm", "debug", "frontend-taste", "ponytail", "tdd", "verify"):
        assert f"relaycli/skills/{skill}.md" in names
