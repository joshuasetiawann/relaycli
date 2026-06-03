"""Project context: CWD awareness, path safety, .gitignore + secret handling.

This module is the security boundary for every file operation. All tool file
access must route paths through :meth:`ProjectContext.resolve`, which confines
access to the project root and blocks traversal via ``..``, absolute paths,
and symlinks that escape the root.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

# Directories we always treat as ignored, even without a .gitignore.
ALWAYS_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".env.d",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".relaycli",
        ".tox",
        ".idea",
        ".DS_Store",
    }
)

# Exact filenames that are secret-like.
SECRET_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pgpass",
        ".pypirc",
        ".dockercfg",
        "kubeconfig",
        "credentials",
        ".git-credentials",
    }
)

# Glob patterns (matched against the *basename*) that are secret-like.
SECRET_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "secrets.*",
    "*credentials*",  # credentials.json, gcp-credentials.yaml, aws_credentials, ...
    "*_secret*",
    "*.secret",
)

# Suffixes that mark a file as a safe *template*, never a real secret.
_SAFE_SECRET_SUFFIXES: tuple[str, ...] = (".example", ".sample", ".template", ".dist")


class PathSafetyError(Exception):
    """Raised when a requested path escapes the project root."""


class ProjectContext:
    """Knows the project root and enforces safe, confined file access."""

    def __init__(self, root: str | Path | None = None) -> None:
        # Canonicalize the root so later containment checks are reliable.
        self.root: Path = Path(root or Path.cwd()).resolve()

    # -- path safety -----------------------------------------------------
    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path:
        """Resolve ``path`` to an absolute path confined within the root.

        Blocks absolute paths outside the root, ``..`` traversal, and symlinks
        whose real target lies outside the root. Works for not-yet-existing
        paths (e.g. a file about to be written).
        """
        raw = Path(path)
        candidate = raw if raw.is_absolute() else (self.root / raw)

        # Resolve symlinks and ".." for the parts that exist; strict=False so a
        # not-yet-created leaf is fine. The parent chain is fully resolved, so a
        # symlinked directory escaping the root is caught here.
        resolved = candidate.resolve()

        if not self._within_root(resolved):
            raise PathSafetyError(
                f"Path '{path}' resolves outside the project root ({self.root})."
            )
        if must_exist and not resolved.exists():
            raise PathSafetyError(f"Path '{path}' does not exist.")
        return resolved

    def _within_root(self, resolved: Path) -> bool:
        return resolved == self.root or self.root in resolved.parents

    def relative(self, path: str | Path) -> str:
        """Return ``path`` relative to the root for display (best effort)."""
        p = Path(path)
        try:
            return str(p.resolve().relative_to(self.root))
        except (ValueError, OSError):
            return str(p)

    # -- classification --------------------------------------------------
    def is_secret(self, path: str | Path) -> bool:
        """True if the basename looks like a credential/secret file."""
        name = Path(path).name.lower()
        if name.endswith(_SAFE_SECRET_SUFFIXES):
            return False
        if name in SECRET_NAMES:
            return True
        return any(fnmatch.fnmatch(name, pat) for pat in SECRET_PATTERNS)

    def is_ignored(self, path: str | Path) -> bool:
        """True if ``path`` is git-ignored or in an always-ignored directory."""
        try:
            resolved = Path(path).resolve()
            rel = resolved.relative_to(self.root)
        except (ValueError, OSError):
            # Outside the root — treat as ignored for listing/search purposes.
            return True

        if any(part in ALWAYS_IGNORE_DIRS for part in rel.parts):
            return True

        if self._is_git_repo():
            return self._git_check_ignore(resolved)
        return self._fallback_ignored(rel)

    # -- git helpers -----------------------------------------------------
    def _is_git_repo(self) -> bool:
        return (self.root / ".git").exists()

    def _git_check_ignore(self, resolved: Path) -> bool:
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.root), "check-ignore", "-q", "--", str(resolved)],
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0

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
            # directory-prefix match (e.g. "build" ignores "build/x")
            if rel_str == pat or rel_str.startswith(pat + "/"):
                return True
        return False
