"""RelayCLI command-line entry point (Typer app).

``relaycli``                  → interactive REPL (default)
``relaycli -p "<request>"``   → run one agent loop non-interactively and exit
``relaycli --model/--mode``   → launch-time overrides
``relaycli config|version``   → diagnostics
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from relaycli import __version__
from relaycli.config import CONFIG_FILE, PermissionMode, Settings, get_settings

app = typer.Typer(
    name="relaycli",
    help="RelayCLI — a provider-agnostic terminal coding agent.",
    add_completion=False,
    no_args_is_help=False,
)

console = Console()


def _apply_overrides(settings: Settings, model: str | None, mode: str | None) -> None:
    if model:
        settings.model = model
    if mode:
        try:
            settings.permission_mode = PermissionMode(mode)
        except ValueError:
            console.print(
                f"[red]Invalid mode '{mode}'.[/red] Use suggest | auto-edit | full-auto."
            )
            raise typer.Exit(code=2)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    prompt: str = typer.Option(
        None, "-p", "--prompt", help="Run a single request non-interactively and exit."
    ),
    model: str = typer.Option(None, "-m", "--model", help="Override the configured model."),
    mode: str = typer.Option(
        None, "--mode", help="Permission mode: suggest | auto-edit | full-auto."
    ),
    yes: bool = typer.Option(
        False, "-y", "--yes", help="Auto-approve prompts (non-interactive one-shot runs)."
    ),
    relay: bool = typer.Option(
        None,
        "--relay/--no-relay",
        help="Run requests through the Planner → Coder → Reviewer relay pipeline.",
    ),
) -> None:
    """Launch the REPL, or run a one-shot request with -p."""
    if ctx.invoked_subcommand is not None:
        return

    settings = get_settings()
    _apply_overrides(settings, model, mode)
    if relay is not None:
        settings.relay_enabled = relay

    if prompt is not None:
        _run_once(settings, prompt, assume_yes=yes)
        return

    # No subcommand and no -p: interactive REPL.
    from relaycli.repl import run_repl

    run_repl(settings, console=console)


def _run_once(settings: Settings, request: str, *, assume_yes: bool) -> None:
    """Execute one agent loop and exit (the -p path)."""
    from relaycli.agent import Agent
    from relaycli.context import ProjectContext
    from relaycli.permissions import PermissionManager
    from relaycli.render import RichReporter, render_task_summary

    project = ProjectContext(Path(os.getcwd()))
    permissions = PermissionManager(
        settings.permission_mode, console=console, assume_yes=assume_yes
    )

    console.print(
        f"[dim]model[/dim] [green]{settings.model}[/green]  "
        f"[dim]mode[/dim] [yellow]{settings.permission_mode}[/yellow]  "
        f"[dim]cwd[/dim] {project.root}"
    )
    if settings.permission_mode is PermissionMode.full_auto:
        console.print(
            "[bold yellow]⚠ full-auto:[/bold yellow] edits and commands run without asking."
        )
    console.print()

    if settings.relay_enabled:
        from relaycli.relay import Relay
        from relaycli.render import RelayRichObserver, render_relay_summary
        from relaycli.router import routing_table

        routes = " · ".join(f"{role}:{m}" for role, m in routing_table(settings).items())
        console.print(f"[dim]relay[/dim] [cyan]on[/cyan]  [dim]{routes}[/dim]\n")
        relay_pipeline = Relay(
            settings, console=console, project=project, permissions=permissions
        )
        observer = RelayRichObserver(console)
        try:
            relay_result = relay_pipeline.run(request, observer=observer)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            raise typer.Exit(code=130)
        render_relay_summary(console, relay_result)
        if relay_result.stopped_reason == "error":
            raise typer.Exit(code=1)
        return

    agent = Agent(settings, console=console, project=project, permissions=permissions)
    reporter = RichReporter(console)

    try:
        result = agent.run(request, reporter=reporter)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        raise typer.Exit(code=130)

    render_task_summary(console, result, reporter.tools_used)
    if result.stopped_reason == "error":
        raise typer.Exit(code=1)


@app.command()
def config() -> None:
    """Show the active configuration and which provider keys are detected."""
    settings = get_settings()

    table = Table(title="RelayCLI configuration", show_header=True, header_style="bold")
    table.add_column("setting", style="cyan", no_wrap=True)
    table.add_column("value")
    table.add_row("model", str(settings.model))
    table.add_row("permission_mode", str(settings.permission_mode))
    table.add_row("max_iterations", str(settings.max_iterations))
    table.add_row("token_budget", str(settings.token_budget))
    table.add_row("relay_enabled", str(settings.relay_enabled))
    table.add_row("max_review_cycles", str(settings.max_review_cycles))
    table.add_row(
        "config file", str(CONFIG_FILE) + ("" if CONFIG_FILE.exists() else "  (not present)")
    )
    console.print(table)

    from relaycli.router import routing_table

    rtable = Table(title="Relay routing", show_header=True, header_style="bold")
    rtable.add_column("role", style="cyan", no_wrap=True)
    rtable.add_column("model")
    for role, resolved in routing_table(settings).items():
        override = getattr(settings, f"{role.value}_model")
        note = "" if override else "  [dim](= model)[/dim]"
        rtable.add_row(str(role), f"{resolved}{note}")
    console.print(rtable)

    ptable = Table(title="Providers", show_header=True, header_style="bold")
    ptable.add_column("provider", style="cyan", no_wrap=True)
    ptable.add_column("status")
    for name, ok in settings.detected_providers().items():
        if name == "ollama":
            ptable.add_row(name, "[blue]no key required[/blue]")
        else:
            ptable.add_row(name, "[green]detected[/green]" if ok else "[dim]missing[/dim]")
    console.print(ptable)


@app.command()
def version() -> None:
    """Print the RelayCLI version."""
    console.print(f"relaycli {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
