"""`relaycli config …` subcommands — manage roles, models, tiers, and keys.

A thin CLI over :mod:`relaycli.appconfig`. Every change persists atomically to
the ``0600`` config file; secrets are never printed or echoed — only masked
status is shown.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from relaycli.appconfig import (
    PROVIDER_ENV,
    ProviderConfig,
    RoleConfig,
    effective_roles,
    load_app_config,
    mask_key,
    save_app_config,
)
from relaycli.roles import BUILTIN_ROLES, TIERS, builtin_role

console = Console()

config_app = typer.Typer(
    name="config",
    help="Manage roles, per-role models, tiers, and provider keys.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@config_app.callback(invoke_without_command=True)
def _config_root(ctx: typer.Context) -> None:
    # No subcommand → open the interactive Configuration screen. The
    # subcommands stay available for scripting.
    if ctx.invoked_subcommand is None:
        from relaycli.config_menu import run_configuration

        run_configuration(console)


def _die(message: str) -> None:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(code=2)


def _require_role(role_id: str):
    b = builtin_role(role_id)
    if b is None:
        ids = ", ".join(r.id for r in BUILTIN_ROLES)
        _die(f"Unknown role '{role_id}'. Known roles: {ids}")
    return b


@config_app.command("show")
def show() -> None:
    """Print the resolved configuration (keys masked)."""
    cfg = load_app_config()

    prefs = Table(title="Preferences", show_header=True, header_style="bold", box=None)
    prefs.add_column("key", style="cyan", no_wrap=True)
    prefs.add_column("value")
    for key in ("permission_mode", "theme", "max_context_tokens"):
        prefs.add_row(key, escape(str(cfg.preference(key))))
    console.print(prefs)
    console.print()

    tiers = Table(title="Model tiers", show_header=True, header_style="bold", box=None)
    tiers.add_column("tier", style="cyan", no_wrap=True)
    tiers.add_column("model")
    for tier in TIERS:
        tiers.add_row(tier, escape(str(cfg.tier_model(tier) or "[dim]unset[/dim]")))
    console.print(tiers)
    console.print()

    provs = Table(title="Providers", show_header=True, header_style="bold", box=None)
    provs.add_column("provider", style="cyan", no_wrap=True)
    provs.add_column("key")
    for name in PROVIDER_ENV:
        stored = cfg.providers.get(name)
        provs.add_row(name, mask_key(stored.api_key if stored else None))
    console.print(provs)
    console.print()

    roles = Table(title="Roles", show_header=True, header_style="bold", box=None)
    roles.add_column("role", style="cyan", no_wrap=True)
    roles.add_column("enabled")
    roles.add_column("assigned")
    roles.add_column("resolved model")
    for r in effective_roles(cfg):
        mark = "[green]✓[/green]" if r.enabled else "[dim]✗[/dim]"
        resolved = r.error and f"[red]{escape(r.error)}[/red]" or escape(str(r.model))
        roles.add_row(r.id, mark, escape(r.assigned), resolved)
    console.print(roles)
    console.print(f"[dim]config file: {load_app_config().path}[/dim]")


@config_app.command("set-model")
def set_model(role: str, model: str) -> None:
    """Assign a ROLE a concrete MODEL id or a tier name (fast|balanced|strong)."""
    _require_role(role)
    model = model.strip()
    if not model:
        _die("Model id or tier name required.")
    cfg = load_app_config()
    rc = cfg.roles.get(role) or RoleConfig()
    rc.model = model
    cfg.roles[role] = rc
    save_app_config(cfg)
    kind = "tier" if model in TIERS else "model"
    console.print(f"[green]{role}[/green] → {escape(model)}  [dim]({kind})[/dim]")


@config_app.command("tier")
def tier(name: str, model: str) -> None:
    """Set a tier's concrete model. NAME is fast | balanced | strong."""
    if name not in TIERS:
        _die(f"Unknown tier '{name}'. Tiers: {', '.join(TIERS)}")
    model = model.strip()
    if not model:
        _die("Model id required.")
    cfg = load_app_config()
    cfg.tiers[name] = model
    save_app_config(cfg)
    console.print(f"tier [green]{name}[/green] → {escape(model)}")


@config_app.command("enable")
def enable(role: str) -> None:
    """Enable a role."""
    _set_enabled(role, True)


@config_app.command("disable")
def disable(role: str) -> None:
    """Disable a role."""
    _set_enabled(role, False)


def _set_enabled(role: str, value: bool) -> None:
    _require_role(role)
    cfg = load_app_config()
    rc = cfg.roles.get(role) or RoleConfig()
    rc.enabled = value
    cfg.roles[role] = rc
    save_app_config(cfg)
    state = "[green]enabled[/green]" if value else "[dim]disabled[/dim]"
    console.print(f"{role} → {state}")


@config_app.command("set-key")
def set_key(
    provider: str,
    env: str = typer.Option(None, "--env", help="Store an env reference (VAR name)."),
    value: str = typer.Option(None, "--value", help="Store a literal key (masked; 0600 file)."),
) -> None:
    """Set a provider's API key as an env reference (preferred) or a literal.

    The secret is never echoed. With neither flag, defaults to an env
    reference when the provider's standard variable is set.
    """
    if provider not in PROVIDER_ENV:
        _die(f"Unknown provider '{provider}'. Known: {', '.join(PROVIDER_ENV)}")
    if env and value:
        _die("Use only one of --env / --value.")

    if env:
        stored, how = f"env:{env}", f"env reference → {env}"
    elif value:
        stored, how = value.strip(), f"stored literal (masked: {mask_key(value.strip())})"
    else:
        import os

        std = PROVIDER_ENV[provider]
        if os.environ.get(std):
            stored, how = f"env:{std}", f"env reference → {std}"
        else:
            _die(f"No key given. Pass --env {std} (once you export it) or --value <key>.")

    cfg = load_app_config()
    pc = cfg.providers.get(provider) or ProviderConfig()
    pc.api_key = stored
    cfg.providers[provider] = pc
    save_app_config(cfg)
    console.print(f"[green]{provider}[/green] key set  [dim]({how})[/dim]")


@config_app.command("path")
def path() -> None:
    """Print the config file location."""
    console.print(str(load_app_config().path))
