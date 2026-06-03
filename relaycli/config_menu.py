"""Interactive Configuration and Settings screens (keyboard-driven, Rich + PT).

STRICT SEPARATION (the whole point):

* **Settings** — general app preferences ONLY (permission mode, theme,
  context-token limit). Small and tidy; nothing about roles/models/keys.
* **Configuration** — a separate surface split into two sub-sections:
  **Providers & Keys** and **Roles & Models**. Never mixes with Settings.

Both are thin views over :mod:`relaycli.appconfig` — no second config store.
Every change persists atomically (``0600``) and takes effect immediately.
The command handlers are pure (mutate config + return a message) so they are
unit-tested directly; the prompt_toolkit loop is the I/O shell around them.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from relaycli.appconfig import (
    PROVIDER_ENV,
    AppConfig,
    ProviderConfig,
    RoleConfig,
    effective_roles,
    load_app_config,
    mask_key,
    save_app_config,
    set_runtime_option,
)
from relaycli.config import PermissionMode
from relaycli.roles import TIERS, builtin_role

ACCENT = "#2D5BFF"


# ── Configuration screen ────────────────────────────────────────────────
@dataclass
class ConfigMenu:
    """State + command handling for the two-section Configuration screen."""

    cfg: AppConfig
    section: str = "roles"  # "roles" | "providers"

    def render(self, console: Console) -> None:
        console.print(Panel(
            f"[bold {ACCENT}]Configuration[/bold {ACCENT}]   "
            f"[dim]providers & keys · roles & models — separate from /settings[/dim]",
            border_style=ACCENT, expand=False,
        ))
        if self.section == "providers":
            self._render_providers(console)
        else:
            self._render_roles(console)
        console.print(
            "[dim]› roles · providers · enable/disable <role> · model <role> "
            "<model|tier> · tier <t> <model> · key <provider> <env:VAR|value> · "
            "q[/dim]"
        )

    def _render_roles(self, console: Console) -> None:
        t = Table(title="Roles & Models", show_header=True, header_style="bold", box=None)
        t.add_column("role", style="cyan", no_wrap=True)
        t.add_column("on")
        t.add_column("assigned")
        t.add_column("resolved model")
        for r in effective_roles(self.cfg):
            mark = "[green]✓[/green]" if r.enabled else "[dim]✗[/dim]"
            resolved = (f"[red]{escape(r.error)}[/red]" if r.error
                        else escape(str(r.model)))
            t.add_row(r.id, mark, escape(r.assigned), resolved)
        console.print(t)
        tt = Table(title="Tiers", show_header=True, header_style="bold", box=None)
        tt.add_column("tier", style="cyan", no_wrap=True)
        tt.add_column("model")
        for tier in TIERS:
            tt.add_row(tier, escape(str(self.cfg.tier_model(tier) or "unset")))
        console.print(tt)

    def _render_providers(self, console: Console) -> None:
        t = Table(title="Providers & Keys", show_header=True, header_style="bold", box=None)
        t.add_column("provider", style="cyan", no_wrap=True)
        t.add_column("key")
        for name in PROVIDER_ENV:
            stored = self.cfg.providers.get(name)
            t.add_row(name, mask_key(stored.api_key if stored else None))
        console.print(t)

    # -- command handling (pure; returns (message, quit)) -----------------
    def handle(self, line: str) -> tuple[str, bool]:
        parts = line.strip().split()
        if not parts:
            return "", False
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("q", "quit", "exit"):
            return "closed configuration.", True
        if cmd in ("roles", "providers"):
            self.section = cmd
            return "", False
        if cmd in ("enable", "disable"):
            return self._enable(args, cmd == "enable")
        if cmd == "model":
            return self._set_model(args)
        if cmd == "tier":
            return self._set_tier(args)
        if cmd == "key":
            return self._set_key(args)
        if cmd == "help":
            return ("roles/providers switch view · enable/disable <role> · "
                    "model <role> <model|tier> · tier <t> <model> · "
                    "key <provider> <env:VAR|value> · q", False)
        return f"unknown command '{escape(cmd)}' (try help)", False

    def _enable(self, args: list[str], value: bool) -> tuple[str, bool]:
        if not args:
            return "usage: enable|disable <role>", False
        role = args[0]
        if builtin_role(role) is None:
            return f"unknown role '{escape(role)}'", False
        rc = self.cfg.roles.get(role) or RoleConfig()
        rc.enabled = value
        self.cfg.roles[role] = rc
        save_app_config(self.cfg)
        return f"{role} {'enabled' if value else 'disabled'}", False

    def _set_model(self, args: list[str]) -> tuple[str, bool]:
        if len(args) < 2:
            return "usage: model <role> <model-id|tier>", False
        role, model = args[0], args[1]
        if builtin_role(role) is None:
            return f"unknown role '{escape(role)}'", False
        rc = self.cfg.roles.get(role) or RoleConfig()
        rc.model = model
        self.cfg.roles[role] = rc
        save_app_config(self.cfg)
        kind = "tier" if model in TIERS else "model"
        return f"{role} → {escape(model)} ({kind})", False

    def _set_tier(self, args: list[str]) -> tuple[str, bool]:
        if len(args) < 2:
            return "usage: tier <fast|balanced|strong> <model-id>", False
        name, model = args[0], args[1]
        if name not in TIERS:
            return f"unknown tier '{escape(name)}' ({', '.join(TIERS)})", False
        self.cfg.tiers[name] = model
        save_app_config(self.cfg)
        return f"tier {name} → {escape(model)}", False

    def _set_key(self, args: list[str]) -> tuple[str, bool]:
        if len(args) < 2:
            return "usage: key <provider> <env:VAR|value>", False
        provider, value = args[0], args[1]
        if provider not in PROVIDER_ENV:
            return f"unknown provider '{escape(provider)}' ({', '.join(PROVIDER_ENV)})", False
        pc = self.cfg.providers.get(provider) or ProviderConfig()
        pc.api_key = value  # "env:VAR" or a literal; never echoed back raw
        self.cfg.providers[provider] = pc
        save_app_config(self.cfg)
        return f"{provider} key set ({mask_key(value)})", False


# ── Settings screen (preferences ONLY) ──────────────────────────────────
@dataclass
class SettingsMenu:
    """General preferences only — deliberately small, distinct from config."""

    cfg: AppConfig

    def render(self, console: Console) -> None:
        console.print(Panel(
            f"[bold {ACCENT}]Settings[/bold {ACCENT}]   "
            f"[dim]general preferences only — roles/models/keys live in /config[/dim]",
            border_style=ACCENT, expand=False,
        ))
        t = Table(show_header=True, header_style="bold", box=None)
        t.add_column("preference", style="cyan", no_wrap=True)
        t.add_column("value")
        t.add_row("permission_mode", escape(str(self.cfg.preference("permission_mode"))))
        t.add_row("theme", escape(str(self.cfg.preference("theme"))))
        t.add_row("max_context_tokens", escape(str(self.cfg.preference("max_context_tokens"))))
        console.print(t)
        console.print(
            "[dim]› mode <suggest|auto-edit|full-auto> · theme <name> · "
            "context <n> · q[/dim]"
        )

    def handle(self, line: str) -> tuple[str, bool]:
        parts = line.strip().split()
        if not parts:
            return "", False
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in ("q", "quit", "exit"):
            return "closed settings.", True
        if cmd == "mode":
            if not args:
                return "usage: mode <suggest|auto-edit|full-auto>", False
            try:
                PermissionMode(args[0])
            except ValueError:
                return "invalid mode (suggest|auto-edit|full-auto)", False
            return self._set("permission_mode", args[0])
        if cmd == "theme":
            if not args:
                return "usage: theme <name>", False
            return self._set("theme", args[0])
        if cmd == "context":
            if not args or not args[0].isdigit():
                return "usage: context <positive integer>", False
            return self._set("max_context_tokens", int(args[0]))
        if cmd == "help":
            return "mode <m> · theme <name> · context <n> · q", False
        return f"unknown command '{escape(cmd)}' (try help)", False

    def _set(self, key: str, value) -> tuple[str, bool]:
        set_runtime_option(key, value, self.cfg.path)
        self.cfg = load_app_config(self.cfg.path)
        return f"{key} → {escape(str(value))}", False


# ── I/O loops (thin shells around the handlers) ─────────────────────────
def _run_loop(console: Console, menu, banner_key: str) -> None:
    from prompt_toolkit import PromptSession

    session = PromptSession()
    while True:
        console.print()
        menu.render(console)
        try:
            line = session.prompt(f"{banner_key} › ")
        except (EOFError, KeyboardInterrupt):
            break
        message, done = menu.handle(line)
        if message:
            console.print(f"[dim]{message}[/dim]")
        if done:
            break


def run_configuration(console: Console | None = None) -> None:
    console = console or Console()
    _run_loop(console, ConfigMenu(load_app_config()), "config")


def run_settings(console: Console | None = None) -> None:
    console = console or Console()
    _run_loop(console, SettingsMenu(load_app_config()), "settings")
