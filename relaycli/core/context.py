"""Project context — CWD awareness, path safety, .gitignore + secret detection."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

ALWAYS_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", ".venv", "venv", ".env.d", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".relaycli", ".tox",
    ".idea", ".DS_Store",
})
SECRET_NAMES: frozenset[str] = frozenset({
    ".env", ".netrc", ".npmrc", ".pgpass", ".pypirc", ".dockercfg",
    "kubeconfig", "credentials", ".git-credentials",
})
SECRET_PATTERNS: tuple[str, ...] = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", "*.p12", "*.pfx", "*.keystore", "secrets.*",
    "*credentials*", "*_secret*", "*.secret",
)
_SAFE_SECRET_SUFFIXES: tuple[str, ...] = (".example", ".sample", ".template", ".dist")


class PathSafetyError(Exception):
    pass


class ProjectContext:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root: Path = Path(root or Path.cwd()).resolve()

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path:
        raw = Path(path)
        candidate = raw if raw.is_absolute() else (self.root / raw)
        resolved = candidate.resolve()
        if not self._within_root(resolved):
            raise PathSafetyError(f"Path '{path}' resolves outside the project root ({self.root}).")
        if must_exist and not resolved.exists():
            raise PathSafetyError(f"Path '{path}' does not exist.")
        return resolved

    def _within_root(self, resolved: Path) -> bool:
        return resolved == self.root or self.root in resolved.parents

    def relative(self, path: str | Path) -> str:
        """A project-relative path, always with `/` separators.

        This string is model-facing: it lands in the system prompt, in tool
        summaries, and in "use one of these exact relative paths" errors. On
        Windows `str()` produced `belajar-mandarin\\index.html`, so the model
        was shown one spelling and asked for another everywhere else — and
        the same repo read differently on Windows than on CI. Windows accepts
        `/` in every path API, so POSIX is the spelling that works on both.

        A path outside the project keeps the platform's own separator: that
        one is a real absolute path, not a portable project reference.
        """
        p = Path(path)
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return str(p)

    def is_secret(self, path: str | Path) -> bool:
        name = Path(path).name.lower()
        if name.endswith(_SAFE_SECRET_SUFFIXES):
            return False
        if name in SECRET_NAMES:
            return True
        return any(fnmatch.fnmatch(name, pat) for pat in SECRET_PATTERNS)

    def is_ignored(self, path: str | Path) -> bool:
        try:
            resolved = Path(path).resolve()
            rel = resolved.relative_to(self.root)
        except (ValueError, OSError):
            return True
        if any(part in ALWAYS_IGNORE_DIRS for part in rel.parts):
            return True
        if self._is_git_repo():
            return self._git_check_ignore(resolved)
        return self._fallback_ignored(rel)

    def _is_git_repo(self) -> bool:
        return (self.root / ".git").exists()

    def _git_check_ignore(self, resolved: Path) -> bool:
        try:
            proc = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", "--", str(resolved)],
                                  capture_output=True, timeout=5)
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _fallback_ignored(self, rel: Path) -> bool:
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            return False
        rel_str = rel.as_posix()
        try:
            lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return False
        for line in lines:
            pat = line.strip()
            if not pat or pat.startswith("#"):
                continue
            pat = pat.rstrip("/").lstrip("/")
            if fnmatch.fnmatch(rel_str, pat) or fnmatch.fnmatch(rel.name, pat):
                return True
            if rel_str == pat or rel_str.startswith(pat + "/"):
                return True
        return False
