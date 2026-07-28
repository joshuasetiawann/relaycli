"""`relaycli plugin` — manage plugins (external hook code) from the CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm
from rich.table import Table

from relaycli.plugins import manager
from relaycli.plugins.manifest import ManifestError

plugin_app = typer.Typer(
    name="plugin",
    help="Manage plugins (external hook code).",
    no_args_is_help=True,
)
console = Console()


@plugin_app.command("list")
def list_() -> None:
    """Installed plugins and their load status."""
    plugins = manager.list_installed()
    if not plugins:
        console.print("[dim]no plugins installed.[/dim] (relaycli plugin install <path>)")
        return
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("plugin", no_wrap=True)
    table.add_column("version", no_wrap=True)
    table.add_column("capabilities", no_wrap=True)
    table.add_column("status")
    for plugin in plugins:
        status = "[green]ok[/green]" if plugin.ok else f"[red]error: {escape(plugin.error or '')}[/red]"
        table.add_row(
            escape(plugin.name), escape(plugin.version) or "[dim]—[/dim]",
            escape(", ".join(plugin.capabilities)) or "[dim]—[/dim]", status,
        )
    console.print(table)


@plugin_app.command()
def install(
    source: str = typer.Argument(help="Path to a plugin package directory or a single .py file."),
    force: bool = typer.Option(False, "--force", help="Overwrite if a plugin with this name is already installed."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Install a plugin from a local path.

    Installing a plugin is the trust decision — relaycli loads and runs
    its code with full interpreter privileges from then on, same as any
    Python you'd run yourself; declared capabilities (from plugin.toml,
    if it has one) describe what the author says it needs, not a sandbox
    this command enforces at runtime.
    """
    try:
        preview = manager.preview_install(Path(source))
    except (manager.PluginInstallError, ManifestError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=2)

    console.print(f"[bold]{escape(preview.name)}[/bold]" + (f" v{escape(preview.version)}" if preview.version else ""))
    if preview.description:
        console.print(f"  {escape(preview.description)}")
    console.print(
        "  capabilities: " + (escape(", ".join(preview.capabilities)) if preview.capabilities
                               else "[dim]none declared[/dim]")
    )
    console.print(f"  [dim]{escape(str(preview.source))} → {escape(str(preview.destination))}[/dim]")
    if preview.already_installed and not force:
        console.print(f"[red]'{escape(preview.name)}' is already installed — pass --force to replace it.[/red]")
        raise typer.Exit(code=1)

    if not yes and not Confirm.ask("Install and trust this plugin's code?", default=False, console=console):
        console.print("[yellow]cancelled.[/yellow]")
        raise typer.Exit(code=1)

    try:
        plugin = manager.install_plugin(Path(source), overwrite=force)
    except manager.PluginInstallError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=2)

    if plugin.ok:
        console.print(f"plugin [cyan]{escape(plugin.name)}[/cyan] → installed")
    else:
        console.print(
            f"plugin [cyan]{escape(plugin.name)}[/cyan] → installed, "
            f"but [red]failed to load: {escape(plugin.error or '')}[/red]"
        )
        raise typer.Exit(code=1)


@plugin_app.command()
def remove(name: str) -> None:
    """Remove an installed plugin."""
    if manager.remove_plugin(name):
        console.print(f"plugin [cyan]{escape(name)}[/cyan] → removed")
    else:
        console.print(f"[red]no plugin named '{escape(name)}'.[/red]")
        raise typer.Exit(code=1)


@plugin_app.command()
def info(name: str) -> None:
    """Details for one installed plugin."""
    plugin = manager.get_installed(name)
    if plugin is None:
        console.print(f"[red]no plugin named '{escape(name)}'.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]{escape(plugin.name)}[/bold]" + (f" v{escape(plugin.version)}" if plugin.version else ""))
    if plugin.description:
        console.print(f"  {escape(plugin.description)}")
    console.print(
        "  capabilities: " + (escape(", ".join(plugin.capabilities)) if plugin.capabilities
                               else "[dim]none declared[/dim]")
    )
    console.print("  hooks: " + (escape(", ".join(sorted(plugin.hooks))) if plugin.hooks else "[dim]none[/dim]"))
    if plugin.ok:
        console.print("  status: [green]ok[/green]")
    else:
        console.print(f"  status: [red]error: {escape(plugin.error or '')}[/red]")
