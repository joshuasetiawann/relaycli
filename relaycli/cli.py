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
from rich.markup import escape
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

# `relaycli config …` — role/model/tier/key management (see config_cli).
from relaycli.config_cli import config_app  # noqa: E402

app.add_typer(config_app, name="config")

# `relaycli mcp …` — MCP connector management (see mcp_cli).
from relaycli.mcp_cli import mcp_app  # noqa: E402

app.add_typer(mcp_app, name="mcp")


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
    show_version: bool = typer.Option(
        False, "--version", help="Print the RelayCLI version and exit."
    ),
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
    if show_version:
        console.print(f"relaycli {__version__}")
        raise typer.Exit()

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
    from relaycli.frontend_scaffold import create_frontend_scaffold, detect_frontend_scaffold
    from relaycli.intent import local_reply_for
    from relaycli.llm import key_status, preflight_settings
    from relaycli.ollama_runtime import recommended_fast_local_model, slow_local_model_warning
    from relaycli.permissions import PermissionManager
    from relaycli.render import (
        RichReporter,
        render_local_reply,
        render_model_warning,
        render_setup_panel,
        render_status_line,
        render_task_summary,
    )

    reply = local_reply_for(request)
    if reply is not None:
        render_local_reply(console, reply)
        return

    project = ProjectContext(Path(os.getcwd()))
    permissions = PermissionManager(
        settings.permission_mode, console=console, assume_yes=assume_yes
    )
    scaffold = detect_frontend_scaffold(request) if settings.local_scaffolds else None
    if scaffold is not None:
        decision = permissions.confirm(
            "write", prompt_text=f"Create frontend scaffold in {escape(scaffold.folder)}?"
        )
        if not decision.approved:
            console.print("[yellow]scaffold declined.[/yellow]")
            raise typer.Exit(code=1)
        result = create_frontend_scaffold(project, scaffold)
        console.print(f"[green]created[/green] {escape(result.folder)}")
        for rel in result.files:
            console.print(f"  [dim]write[/dim] {escape(rel)}")
        console.print(
            f"[dim]open {escape(result.folder)}/index.html in a browser to preview.[/dim]"
        )
        return

    problem = preflight_settings(settings)
    if problem:
        render_setup_panel(console, problem, settings.detected_providers())
        raise typer.Exit(code=2)

    warning = slow_local_model_warning(settings.model)
    if warning:
        fallback = recommended_fast_local_model(settings)
        if fallback and fallback != settings.model:
            old_model = settings.model
            console.print(f"[yellow]⚠ {escape(warning)}[/yellow]")
            settings.model = fallback
            try:
                from relaycli.appconfig import set_base_model

                set_base_model(fallback)
            except Exception:
                pass
            console.print(
                f"[yellow]model auto-switch:[/yellow] {escape(old_model)} → {escape(fallback)} "
                "[dim](requires full GPU / avoids CPU-GPU fallback)[/dim]"
            )
        else:
            console.print(f"[yellow]⚠ {escape(warning)}[/yellow]")
            raise typer.Exit(code=2)

    skills_block = ""
    if settings.skills_auto:
        from relaycli.skills import auto_match, discover_skills, skills_prompt_block

        skills = discover_skills(project.root)
        auto_names = auto_match(skills, request)
        for name in auto_names:
            console.print(f"[dim]✦ auto-skill: [cyan]{escape(name)}[/cyan][/dim]")
        skills_block = skills_prompt_block([skills[n] for n in auto_names])

    render_status_line(console, settings, project.root, key_status(settings))
    render_model_warning(console, settings)
    if settings.permission_mode is PermissionMode.full_auto:
        console.print(
            "[bold yellow]⚠ full-auto:[/bold yellow] edits and commands run without asking."
        )
    console.print()

    if settings.relay_enabled:
        from relaycli.relay import Relay
        from relaycli.render import (
            RelayRichObserver,
            render_relay_summary,
            render_routing_banner,
        )

        render_routing_banner(console, settings)
        console.print()
        relay_pipeline = Relay(
            settings, console=console, project=project, permissions=permissions,
            skills_block=skills_block,
        )
        observer = RelayRichObserver(console)
        try:
            relay_result = relay_pipeline.run(request, observer=observer)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            raise typer.Exit(code=130)
        finally:
            observer.close()  # an error/Ctrl-C must not leave a spinner live
        render_relay_summary(console, relay_result)
        if relay_result.stopped_reason == "error":
            raise typer.Exit(code=1)
        return

    from relaycli.mcp import extend_registry
    from relaycli.tools import default_registry

    agent = Agent(
        settings, console=console, project=project, permissions=permissions,
        skills_block=skills_block,
        registry=extend_registry(default_registry(), console=console),
    )
    reporter = RichReporter(console)

    try:
        result = agent.run(request, reporter=reporter)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        raise typer.Exit(code=130)
    finally:
        reporter.close()  # an error/Ctrl-C must not leave the spinner live

    render_task_summary(console, result, reporter.tools_used)
    if result.stopped_reason == "error":
        raise typer.Exit(code=1)


@app.command("init")
def init_command(
    model: str = typer.Option(
        "auto", "--model", "-m", help="Model to save, or 'auto' to prefer detected Ollama/keys."
    ),
    mode: str = typer.Option(
        None, "--mode", help="Permission mode: suggest | auto-edit | full-auto."
    ),
    services: str = typer.Option(
        None, "--services", help="Comma-separated docker compose profiles: ollama,web,postgres,n8n."
    ),
    yes: bool = typer.Option(False, "-y", "--yes", help="Accept the detected setup."),
    start_services: bool = typer.Option(
        False, "--start-services", help="Run docker compose for selected services."
    ),
) -> None:
    """Run the first-time setup wizard."""
    from relaycli.onboarding import run_init

    run_init(
        console=console, model=model, mode=mode, services=services,
        yes=yes, start=start_services,
    )


@app.command()
def web(
    port: int = typer.Option(8484, "--port", help="Port to serve on."),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the browser automatically."
    ),
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="Bind address. 0.0.0.0 exposes the agent — trusted networks only "
             "(intended for Docker with a 127.0.0.1-mapped port).",
    ),
    allow_host: list[str] = typer.Option(
        [], "--allow-host",
        help="Extra Host/Origin hostname to accept (repeatable), e.g. a LAN name.",
    ),
) -> None:
    """Serve the RelayCLI desktop UI (loopback only by default)."""
    from relaycli.web import serve

    serve(
        get_settings(), port, open_browser=open_browser,
        host=host, allow_hosts=set(allow_host),
    )


@app.command()
def desktop(
    port: int = typer.Option(8484, "--port", help="Port on 127.0.0.1 to serve."),
) -> None:
    """Open the RelayCLI desktop UI in your browser (alias of `web --open`)."""
    from relaycli.web import serve

    serve(get_settings(), port, open_browser=True)


@app.command()
def memory() -> None:
    """Show the agent's long-term memory (global + this project)."""
    from relaycli import memory as mem

    for label, path in (
        ("global", mem.GLOBAL_MEMORY),
        ("project", mem.project_memory_path(Path(os.getcwd()))),
    ):
        text = mem.read_memory(path)
        console.print(f"[bold]{label}[/bold] [dim]{escape(str(path))}[/dim]")
        console.print(escape(text) if text else "[dim](empty)[/dim]")
        console.print()


@app.command()
def settings() -> None:
    """Open the interactive Settings screen (general preferences only)."""
    from relaycli.config_menu import run_settings

    run_settings(console)


@app.command()
def doctor(
    offline: bool = typer.Option(
        False, "--offline", help="Skip checks that need the network."
    ),
) -> None:
    """Check that this install is healthy and production-ready."""
    from relaycli.doctor import render_checks, run_checks

    checks = run_checks(get_settings(), Path(os.getcwd()), live=not offline)
    raise typer.Exit(code=render_checks(console, checks))


@app.command()
def version() -> None:
    """Print the RelayCLI version."""
    console.print(f"relaycli {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
