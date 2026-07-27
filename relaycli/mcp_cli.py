"""`relaycli mcp` — manage MCP connector servers from the command line."""

from __future__ import annotations

import shutil

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from relaycli.mcp import (
    PRESETS,
    MCPClient,
    MCPError,
    configured_servers,
    remove_server as _remove,
    save_server,
    server_status,
)

mcp_app = typer.Typer(
    name="mcp",
    help="Manage MCP connectors (external tool servers).",
    no_args_is_help=True,
)
console = Console()


@mcp_app.command("list")
def list_() -> None:
    """Configured servers and available presets."""
    rows = server_status()
    if rows:
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("server", no_wrap=True)
        table.add_column("state", no_wrap=True)
        table.add_column("tools", no_wrap=True)
        table.add_column("command")
        for row in rows:
            table.add_row(
                escape(row["name"]), row["state"], str(row["tools"]), escape(row["command"])
            )
        console.print(table)
    else:
        console.print("[dim]no MCP servers configured.[/dim]")
    console.print()
    console.print("[bold]presets[/bold] [dim](relaycli mcp add <preset>)[/dim]")
    for name, preset in PRESETS.items():
        runtime = preset["requires"]
        have = "" if shutil.which(runtime) else f"  [yellow](needs {runtime})[/yellow]"
        console.print(f"  [cyan]{name}[/cyan] — {preset['note']}{have}")


@mcp_app.command()
def add(
    name: str = typer.Argument(help="Preset name, or a new server name with --command."),
    command: str = typer.Option(
        None, "--command", help="Full server command (for non-preset servers)."
    ),
    env: list[str] = typer.Option(
        [], "--env", help="KEY=VALUE or KEY=env:VAR (repeatable, env: preferred)."
    ),
) -> None:
    """Add a connector from a preset, or a custom one with --command."""
    env_map: dict[str, str] = {}
    for item in env:
        key, sep, value = item.partition("=")
        if not sep or not key:
            console.print(f"[red]invalid --env '{escape(item)}' (use KEY=VALUE).[/red]")
            raise typer.Exit(code=2)
        env_map[key] = value

    if command:
        import shlex

        argv = shlex.split(command)
    elif name in PRESETS:
        preset = PRESETS[name]
        argv = list(preset["command"])
        env_map = {**preset.get("env", {}), **env_map}
        runtime = preset["requires"]
        if not shutil.which(runtime):
            console.print(
                f"[yellow]note: '{runtime}' is not on PATH — install it before "
                f"the '{name}' connector can start.[/yellow]"
            )
    else:
        console.print(
            f"[red]unknown preset '{escape(name)}'.[/red] "
            f"Presets: {', '.join(PRESETS)} — or pass --command."
        )
        raise typer.Exit(code=2)

    save_server(name, argv, env_map)
    console.print(f"mcp [cyan]{escape(name)}[/cyan] → configured  [dim](relaycli mcp test {escape(name)})[/dim]")


@mcp_app.command()
def remove(name: str) -> None:
    """Remove a configured connector."""
    if _remove(name):
        console.print(f"mcp [cyan]{escape(name)}[/cyan] → removed")
    else:
        console.print(f"[red]no MCP server named '{escape(name)}'.[/red]")
        raise typer.Exit(code=1)


@mcp_app.command()
def test(name: str) -> None:
    """Start a configured server once and list its tools."""
    config = configured_servers().get(name)
    if config is None:
        console.print(f"[red]no MCP server named '{escape(name)}'.[/red]")
        raise typer.Exit(code=1)
    client = MCPClient(config)
    try:
        with console.status(f"starting {escape(name)}…"):
            client.start()
        console.print(f"[green]✓[/green] {escape(name)} — {len(client.tools)} tool(s)")
        for tool in client.tools:
            desc = (tool.get("description") or "").strip().splitlines()
            console.print(
                f"  [cyan]{escape(str(tool.get('name')))}[/cyan]"
                + (f" — {escape(desc[0][:90])}" if desc else "")
            )
    except MCPError as exc:
        console.print(f"[red]✗ {escape(name)}: {escape(str(exc))}[/red]")
        raise typer.Exit(code=1)
    finally:
        client.close()
