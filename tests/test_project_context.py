"""ProjectContext: the path-safety boundary every tool routes file access
through. Covers the edge cases that matter most for a security boundary -
`..` traversal, symlinks escaping the root, secret-file detection, and both
.gitignore code paths (real git repo vs. the fallback parser) - none of
which had a dedicated test file before.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from relaycli.core.context import PathSafetyError, ProjectContext


# -- path traversal -----------------------------------------------------
def test_resolve_blocks_dotdot_traversal(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    with pytest.raises(PathSafetyError, match="outside the project root"):
        ctx.resolve("../escape.txt")


def test_resolve_blocks_absolute_path_outside_root(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    with pytest.raises(PathSafetyError, match="outside the project root"):
        ctx.resolve("/etc/passwd")


def test_resolve_allows_path_inside_root(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    assert ctx.resolve("a.py") == (tmp_path / "a.py").resolve()


def test_resolve_must_exist_raises_for_missing_path(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    with pytest.raises(PathSafetyError, match="does not exist"):
        ctx.resolve("nope.txt", must_exist=True)


# -- symlinks escaping the root (no prior test covered this at all) --------
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need elevated perms on Windows")
def test_resolve_blocks_symlink_escaping_root(tmp_path: Path):
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    link = project / "innocent_looking_link.txt"
    link.symlink_to(outside)
    try:
        ctx = ProjectContext(project)
        with pytest.raises(PathSafetyError, match="outside the project root"):
            ctx.resolve("innocent_looking_link.txt", must_exist=True)
    finally:
        outside.unlink()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need elevated perms on Windows")
def test_resolve_allows_symlink_that_stays_inside_root(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    real = project / "real.txt"
    real.write_text("fine", encoding="utf-8")
    link = project / "link.txt"
    link.symlink_to(real)

    ctx = ProjectContext(project)
    resolved = ctx.resolve("link.txt", must_exist=True)
    assert resolved == real.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need elevated perms on Windows")
def test_resolve_blocks_symlinked_directory_escaping_root(tmp_path: Path):
    outside_dir = tmp_path.parent / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("nope", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "linked_dir").symlink_to(outside_dir, target_is_directory=True)
    try:
        ctx = ProjectContext(project)
        with pytest.raises(PathSafetyError, match="outside the project root"):
            ctx.resolve("linked_dir/secret.txt", must_exist=True)
    finally:
        import shutil
        shutil.rmtree(outside_dir)


# -- secret-file detection -------------------------------------------------
@pytest.mark.parametrize("name", [".env", ".netrc", "credentials", "id_rsa", "aws_credentials.json"])
def test_is_secret_matches_known_names_and_patterns(tmp_path: Path, name: str):
    ctx = ProjectContext(tmp_path)
    assert ctx.is_secret(tmp_path / name) is True


@pytest.mark.parametrize("name", [".env.example", ".env.sample", "id_rsa.template"])
def test_is_secret_exempts_safe_template_suffixes(tmp_path: Path, name: str):
    ctx = ProjectContext(tmp_path)
    assert ctx.is_secret(tmp_path / name) is False


def test_is_secret_false_for_ordinary_file(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    assert ctx.is_secret(tmp_path / "app.py") is False


# -- .gitignore: the fallback parser (no real git repo present) ------------
def test_is_ignored_fallback_parses_gitignore_without_git_repo(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("build/\n*.log\n# a comment\n", encoding="utf-8")
    ctx = ProjectContext(tmp_path)
    assert ctx._is_git_repo() is False  # sanity: exercising the fallback, not git

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "out.txt").write_text("x", encoding="utf-8")
    assert ctx.is_ignored(build_dir / "out.txt") is True
    assert ctx.is_ignored(tmp_path / "debug.log") is True
    assert ctx.is_ignored(tmp_path / "app.py") is False


def test_is_ignored_no_gitignore_file_present(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    assert ctx.is_ignored(tmp_path / "app.py") is False


def test_is_ignored_always_ignores_known_dirs_even_without_gitignore(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    node_modules = tmp_path / "node_modules" / "pkg"
    node_modules.mkdir(parents=True)
    assert ctx.is_ignored(node_modules / "index.js") is True


def test_is_ignored_path_outside_root_is_ignored(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    assert ctx.is_ignored(tmp_path.parent / "elsewhere.txt") is True


# -- .gitignore: the real-git-repo path (git check-ignore subprocess) -------
def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _git_available(), reason="git not installed")
def test_is_ignored_uses_real_git_check_ignore_in_a_git_repo(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True, timeout=10)
    (tmp_path / ".gitignore").write_text("*.secret\n", encoding="utf-8")
    ctx = ProjectContext(tmp_path)
    assert ctx._is_git_repo() is True  # sanity: exercising the real-git path

    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / "creds.secret").write_text("x", encoding="utf-8")
    assert ctx.is_ignored(tmp_path / "creds.secret") is True
    assert ctx.is_ignored(tmp_path / "app.py") is False


# -- relative() ------------------------------------------------------------
def test_relative_returns_posix_style_path_within_root(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    sub = tmp_path / "a" / "b.py"
    sub.parent.mkdir(parents=True)
    sub.write_text("x", encoding="utf-8")
    # Posix-style on every platform, as the name says: this string is
    # model-facing, and `a\b.py` on Windows contradicted every other path
    # the same model is shown.
    assert ctx.relative(sub) == "a/b.py"


def test_relative_falls_back_to_str_for_unresolvable_path(tmp_path: Path):
    ctx = ProjectContext(tmp_path)
    # A path outside the root can't be made relative_to(root); the method
    # must not raise, only fall back to returning it as-is.
    outside = tmp_path.parent / "elsewhere.txt"
    assert ctx.relative(outside) == str(outside)
