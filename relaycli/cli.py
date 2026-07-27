"""RelayCLI — terminal coding agent CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from relaycli import __version__
from relaycli.core.config import Settings, get_settings, PermissionMode
from relaycli.core.llm import key_status
from relaycli.ui.render import render_status_line

console = Console()


# ── Main app ─────────────────────────────────────────────────────────────

app = typer.Typer(
    name="relaycli",
    help="Plan, edit, run, review — from this project root.",
    no_args_is_help=False,
    rich_help_panel=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    prompt: str | None = typer.Option(None, "-p", "--prompt", help="Run a single request non-interactively and exit."),
    model: str | None = typer.Option(None, "-m", "--model", help="Model id (overrides config)."),
    mode: str | None = typer.Option(None, "--mode", help="Permission mode: suggest, auto-edit, full-auto."),
    relay: bool | None = typer.Option(None, "--relay/--no-relay", help="Enable/disable the relay pipeline."),
    desktop: bool = typer.Option(False, "--desktop", help="Start the desktop web UI."),
    port: int = typer.Option(8484, "--port", help="Port for --desktop."),
    version: bool = typer.Option(False, "--version", "-V", help="Show version and exit."),
) -> None:
    if version:
        console.print(f"RelayCLI v{__version__}")
        raise typer.Exit()
    settings = get_settings()
    if model:
        settings.model = model
    if mode:
        try:
            settings.permission_mode = PermissionMode(mode)
        except ValueError:
            console.print(f"[red]Invalid mode '{escape(mode)}'. Use suggest | auto-edit | full-auto.[/red]")
            raise typer.Exit(code=2)
    if relay is not None:
        settings.relay_enabled = relay
    if ctx.invoked_subcommand is not None:
        return
    if desktop:
        from relaycli.ui.web import serve
        serve(settings, port=port)
        return
    if prompt:
        _run_prompt(settings, prompt)
        return
    _run_repl(settings)


def _run_repl(settings: Settings) -> None:
    from relaycli.ui.repl import run_repl
    run_repl(settings, console=console)


def _run_prompt(settings: Settings, text: str) -> None:
    from relaycli.agent.loop import Agent
    from relaycli.core.context import ProjectContext
    from relaycli.core.permissions import PermissionManager
    from relaycli.tools.registry import default_registry
    from relaycli.agent.reporter import PlainReporter
    from relaycli.ui.render import render_task_summary

    project = ProjectContext(Path.cwd())
    permissions = PermissionManager(settings.permission_mode, console=console)
    registry = default_registry()
    agent = Agent(settings, console=console, project=project, permissions=permissions, registry=registry)
    render_status_line(console, settings, project.root, key_status(settings))
    reporter = PlainReporter(console)
    try:
        result = agent.run(text, reporter=reporter)
    finally:
        reporter.close()
    render_task_summary(console, result, reporter.tools_used)


# ── Sub-commands ─────────────────────────────────────────────────────────

@app.command()
def init(
    model: str | None = typer.Option(None, "--model", "-m", help="Model id (auto = detect best)."),
    mode: str | None = typer.Option(None, "--mode", help="Permission mode."),
    services: str | None = typer.Option(None, "--services", help="Docker services (comma names)."),
    yes: bool = typer.Option(False, "-y", help="Skip confirmation."),
    start: bool = typer.Option(False, "--start", help="Start optional services immediately."),
) -> None:
    """Guided first-run setup."""
    from relaycli.onboarding import run_init
    try:
        run_init(console=console, model=model, mode=mode, services=services, yes=yes, start=start)
    except typer.Exit:
        pass


@app.command()
def doctor(
    offline: bool = typer.Option(False, "--offline", help="Skip live checks."),
) -> None:
    """Run a local health check."""
    from relaycli.doctor import render_checks, run_checks
    checks = run_checks(get_settings(), Path.cwd(), live=not offline)
    code = render_checks(console, checks)
    if code:
        raise typer.Exit(code=code)


# ── Sub-typers (command groups) ─────────────────────────────────────────

from relaycli.config.manager import config_app  # noqa: E402
app.add_typer(config_app, name="config", help="Manage roles, models, tiers, and provider keys.")

from relaycli.mcp_cli import mcp_app  # noqa: E402
app.add_typer(mcp_app, name="mcp", help="Manage MCP connectors (external tool servers).")


if __name__ == "__main__":
    app()
