"""Installer script smoke tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_script_is_valid_shell():
    subprocess.run(["sh", "-n", str(ROOT / "scripts/install.sh")], check=True)


def test_readme_documents_one_line_installer():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scripts/install.sh | sh" in readme
    assert "Ollama" in readme and "n8n" in readme
