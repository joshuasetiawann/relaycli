"""`relaycli doctor` — production-readiness health checks.

Each check returns a :class:`Check` row; the CLI renders them as a table and
exits non-zero when anything hard-fails. Checks are small pure-ish functions
taking what they probe, so tests can aim them at temp files and stub probes.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from relaycli.config import CONFIG_DIR, CONFIG_FILE, Settings

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

_ICONS = {OK: "[green]✓[/green]", WARN: "[yellow]⚠[/yellow]",
          FAIL: "[red]✗[/red]", SKIP: "[dim]–[/dim]"}


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail | skip
    detail: str = ""


# ── individual checks ────────────────────────────────────────────────────────
def check_config_perms(
    config_file: Path | None = None, config_dir: Path | None = None
) -> list[Check]:
    config_file = config_file or CONFIG_FILE
    config_dir = config_dir or CONFIG_DIR
    checks: list[Check] = []
    if not config_file.exists():
        checks.append(Check("config file", WARN, f"{config_file} does not exist yet"))
        return checks
    mode = stat.S_IMODE(os.stat(config_file).st_mode)
    if mode & 0o077:
        checks.append(Check(
            "config perms", FAIL,
            f"{config_file} is {oct(mode)[-4:]} — keys are readable by others; "
            f"run: chmod 600 {config_file}",
        ))
    else:
        checks.append(Check("config perms", OK, f"{config_file} is 0600"))
    dmode = stat.S_IMODE(os.stat(config_dir).st_mode)
    if dmode & 0o077:
        checks.append(Check(
            "config dir perms", WARN,
            f"{config_dir} is {oct(dmode)[-4:]} — run: chmod 700 {config_dir}",
        ))
    else:
        checks.append(Check("config dir perms", OK, f"{config_dir} is 0700"))
    return checks


def check_openrouter_key(
    settings: Settings, prober: Callable[[str], tuple[int, str]] | None = None
) -> Check:
    """Live-validate the OpenRouter key (the 401 'User not found' trap)."""
    key = settings.openrouter_api_key
    if not key:
        return Check("openrouter key", SKIP, "no key configured")
    if prober is None:
        prober = _probe_openrouter
    try:
        status, detail = prober(key)
    except OSError as exc:
        return Check("openrouter key", SKIP, f"offline? ({exc})")
    except ValueError as exc:
        # A 200 whose body isn't valid JSON/UTF-8 (captive portal, proxy
        # interception, CDN error page) raises JSONDecodeError or
        # UnicodeDecodeError — both ValueError subclasses. A health check
        # must never crash on a malformed upstream response.
        return Check("openrouter key", SKIP, f"unreadable response ({exc})")
    if status == 200:
        return Check("openrouter key", OK, f"live-verified ({detail})" if detail else "live-verified")
    if status == 401:
        return Check(
            "openrouter key", FAIL,
            "OpenRouter rejected the key (revoked/rotated?) — get a new one at "
            "openrouter.ai/settings/keys, then: relaycli config set-key openrouter",
        )
    return Check("openrouter key", WARN, f"unexpected HTTP {status}")


def _probe_openrouter(key: str) -> tuple[int, str]:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
            label = (body.get("data") or {}).get("label") or ""
            return resp.status, label
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def check_key_drift(settings: Settings, project_root: Path) -> Check:
    """Warn when a cwd .env key differs from the config.toml key.

    RelayCLI reads .env only from the directory it runs in — a fresher key
    there silently stops applying anywhere else (the 2026-07-03 incident).
    """
    env_file = project_root / ".env"
    if not env_file.exists():
        return Check("key drift", SKIP, "no .env in this directory")
    import re
    import tomllib

    try:
        env_text = env_file.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'^\s*OPENROUTER_API_KEY\s*=\s*["\']?([^"\'\s]+)', env_text, re.M)
        if not match:
            return Check("key drift", SKIP, ".env has no OPENROUTER_API_KEY")
        toml_key = ""
        if CONFIG_FILE.exists():
            raw = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            toml_key = str(raw.get("OPENROUTER_API_KEY") or "")
        if toml_key and toml_key != match.group(1):
            return Check(
                "key drift", WARN,
                ".env and config.toml hold DIFFERENT OpenRouter keys — outside "
                "this directory the config.toml one wins; sync with: "
                "relaycli config set-key openrouter",
            )
        return Check("key drift", OK, ".env and config.toml agree")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Check("key drift", SKIP, str(exc))


def check_models(settings: Settings) -> list[Check]:
    """Every model the session would use must resolve to a usable credential."""
    from relaycli.llm import LLM

    llm = LLM(settings)
    models = [("model", settings.model)]
    if settings.relay_enabled:
        from relaycli.router import routing_table

        models += [(f"relay {r}", m) for r, m in routing_table(settings).items()]
    checks = []
    for label, model in dict.fromkeys(models):
        problem = llm.preflight(model)
        if problem:
            checks.append(Check(label, FAIL, problem))
        else:
            checks.append(Check(label, OK, model))
    return checks


def check_runtimes() -> list[Check]:
    """Optional runtimes: informational, never a failure."""
    checks = []
    for binary, purpose in (
        ("node", "MCP npx connectors"),
        ("npx", "MCP presets (filesystem/github/postgres/puppeteer)"),
        ("uvx", "MCP fetch preset"),
        ("docker", "docker compose deployment"),
        ("git", "/diff and project context"),
    ):
        path = shutil.which(binary)
        checks.append(Check(
            binary, OK if path else SKIP,
            path or f"not on PATH — needed only for {purpose}",
        ))
    return checks


def check_writable_dirs(project_root: Path) -> list[Check]:
    from relaycli import memory

    checks = []
    for label, path in (
        ("global memory", memory.GLOBAL_MEMORY.parent),
        ("project memory", memory.project_memory_path(project_root).parent),
    ):
        base = path if path.exists() else path.parent
        checks.append(Check(
            label,
            OK if os.access(base, os.W_OK) else FAIL,
            str(path),
        ))
    return checks


def check_mcp() -> Check:
    from relaycli.mcp import configured_servers

    servers = configured_servers()
    if not servers:
        return Check("mcp", SKIP, "no connectors configured (relaycli mcp list)")
    enabled = [n for n, s in servers.items() if s.enabled]
    return Check(
        "mcp", OK,
        f"{len(enabled)} enabled ({', '.join(enabled) or 'none'}) — "
        f"verify with: relaycli mcp test <name>",
    )


# ── runner ────────────────────────────────────────────────────────────────
def run_checks(
    settings: Settings,
    project_root: Path,
    *,
    live: bool = True,
    prober: Callable[[str], tuple[int, str]] | None = None,
) -> list[Check]:
    checks: list[Check] = []
    checks += check_config_perms()
    checks.append(
        check_openrouter_key(settings, prober=prober)
        if live or prober is not None
        else Check("openrouter key", SKIP, "live check disabled (--offline)")
    )
    checks.append(check_key_drift(settings, project_root))
    checks += check_models(settings)
    checks += check_writable_dirs(project_root)
    checks.append(check_mcp())
    checks += check_runtimes()
    return checks


def render_checks(console, checks: list[Check]) -> int:
    """Print the table; return the exit code (1 when anything failed)."""
    from rich.markup import escape
    from rich.table import Table

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("", no_wrap=True)
    table.add_column("check", no_wrap=True)
    table.add_column("detail")
    for check in checks:
        dim = "[dim]" if check.status == SKIP else ""
        end = "[/dim]" if check.status == SKIP else ""
        table.add_row(_ICONS[check.status], f"{dim}{check.name}{end}",
                      f"{dim}{escape(check.detail)}{end}")
    console.print(table)
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    if fails:
        console.print(f"\n[red]{len(fails)} check(s) failed.[/red]")
        return 1
    if warns:
        console.print(f"\n[yellow]healthy with {len(warns)} warning(s).[/yellow]")
    else:
        console.print("\n[green]all good.[/green]")
    return 0
