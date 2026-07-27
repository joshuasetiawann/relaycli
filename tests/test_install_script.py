"""Installer script smoke tests."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_script_is_valid_shell():
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
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "-w", str(wheelhouse), "--no-deps"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    wheel = next(wheelhouse.glob("relaycli-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "relaycli/web_ui.html" in names
    assert "relaycli/tools/create_folder.py" in names
    for skill in ("brainstorm", "debug", "frontend-taste", "ponytail", "tdd", "verify"):
        assert f"relaycli/skills/{skill}.md" in names
