"""First-run setup wizard for RelayCLI."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from relaycli.appconfig import ProviderConfig, load_app_config, save_app_config
from relaycli.config import PermissionMode, Settings, reload_settings
from relaycli.llm import best_ollama_model, ollama_host_label, ollama_models

PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
DEFAULT_OPENROUTER_MODEL = "openrouter/qwen/qwen3-coder:free"
SERVICE_PROFILES = ("ollama", "web", "postgres", "n8n")
SERVICE_DESCRIPTIONS = {
    "ollama": "local model server on 127.0.0.1:11434",
    "web": "RelayCLI browser UI on 127.0.0.1:8484",
    "postgres": "local Postgres database on 127.0.0.1:5432",
    "n8n": "local n8n automation UI on 127.0.0.1:5678",
}


@dataclass
class InitPlan:
    model: str
    mode: PermissionMode = PermissionMode.suggest
    services: list[str] = field(default_factory=list)
    provider_env: str | None = None
    notes: list[str] = field(default_factory=list)


def detected_key_providers() -> list[str]:
    return [name for name, env in PROVIDER_ENV.items() if os.environ.get(env)]


def choose_default_model(settings: Settings) -> tuple[str, list[str]]:
    """Choose the least surprising first-run model for this machine."""
    notes: list[str] = []
    if local := best_ollama_model(settings):
        notes.append(f"Ollama detected at {ollama_host_label(settings)}")
        return local, notes
    if os.environ.get("OPENROUTER_API_KEY"):
        notes.append("OPENROUTER_API_KEY detected")
        return DEFAULT_OPENROUTER_MODEL, notes
    for provider, env in PROVIDER_ENV.items():
        if os.environ.get(env):
            notes.append(f"{env} detected")
            if provider == "openai":
                return "gpt-4o-mini", notes
            if provider == "anthropic":
                return "claude-3-5-haiku-latest", notes
            if provider == "gemini":
                return "gemini/gemini-1.5-flash", notes
            if provider == "groq":
                return "groq/llama-3.3-70b-versatile", notes
            if provider == "mistral":
                return "mistral/mistral-small-latest", notes
    notes.append("no provider key or local Ollama model detected")
    return DEFAULT_OPENROUTER_MODEL, notes


def normalize_services(raw: str | None) -> list[str]:
    if not raw:
        return []
    wanted = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = [name for name in wanted if name not in SERVICE_PROFILES]
    if unknown:
        raise typer.BadParameter(
            f"unknown service(s): {', '.join(unknown)}; choose from {', '.join(SERVICE_PROFILES)}"
        )
    return list(dict.fromkeys(wanted))


def build_plan(
    settings: Settings,
    *,
    model: str | None = None,
    mode: str | None = None,
    services: str | None = None,
) -> InitPlan:
    notes: list[str] = []
    chosen = (model or "").strip()
    if not chosen or chosen == "auto":
        chosen, notes = choose_default_model(settings)
    try:
        chosen_mode = PermissionMode(mode or settings.permission_mode)
    except ValueError as exc:
        raise typer.BadParameter("mode must be suggest, auto-edit, or full-auto") from exc
    provider_env = None
    head = chosen.split("/", 1)[0]
    if head in PROVIDER_ENV and os.environ.get(PROVIDER_ENV[head]):
        provider_env = PROVIDER_ENV[head]
    elif chosen.startswith("gpt-") and os.environ.get("OPENAI_API_KEY"):
        provider_env = "OPENAI_API_KEY"
    return InitPlan(
        model=chosen,
        mode=chosen_mode,
        services=normalize_services(services),
        provider_env=provider_env,
        notes=notes,
    )


def save_plan(plan: InitPlan) -> None:
    cfg = load_app_config()
    cfg._raw["model"] = plan.model
    cfg._raw["permission_mode"] = str(plan.mode)
    if plan.provider_env:
        provider = next((p for p, env in PROVIDER_ENV.items() if env == plan.provider_env), None)
        if provider:
            pc = cfg.providers.get(provider) or ProviderConfig()
            pc.api_key = f"env:{plan.provider_env}"
            cfg.providers[provider] = pc
    save_app_config(cfg)
    reload_settings()


def start_services(services: list[str], console: Console) -> int:
    if not services:
        return 0
    cmd = ["docker", "compose"]
    for service in services:
        cmd += ["--profile", service]
    cmd += ["up", "-d"]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    try:
        completed = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        console.print("[yellow]docker compose not found; services were not started.[/yellow]")
        return 127
    return completed.returncode


def render_plan(console: Console, settings: Settings, plan: InitPlan) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("model", f"[green]{escape(plan.model)}[/green]")
    table.add_row("mode", f"[yellow]{escape(str(plan.mode))}[/yellow]")
    table.add_row("services", escape(", ".join(plan.services) or "none"))
    installed = ollama_models(settings)
    if installed:
        table.add_row("ollama", escape(f"{len(installed)} model(s): {', '.join(installed[:4])}"))
    for note in plan.notes:
        table.add_row("note", escape(note))
    console.print(Panel(table, title="relaycli init", title_align="left", border_style="#2D5BFF"))


def run_init(
    *,
    console: Console | None = None,
    model: str | None = None,
    mode: str | None = None,
    services: str | None = None,
    yes: bool = False,
    start: bool = False,
) -> InitPlan:
    console = console or Console()
    settings = Settings()
    plan = build_plan(settings, model=model, mode=mode, services=services)

    if not yes:
        render_plan(console, settings, plan)
        if not typer.confirm("Write this setup to ~/.relaycli/config.toml?", default=True):
            raise typer.Exit(code=1)
        if not plan.services:
            service_hint = ", ".join(f"{name} ({desc})" for name, desc in SERVICE_DESCRIPTIONS.items())
            console.print(f"[dim]Optional services: {escape(service_hint)}[/dim]")
            picked = typer.prompt(
                "Start optional services? (comma names; blank for none)",
                default="",
                show_default=False,
            )
            plan.services = normalize_services(picked)
        start = bool(plan.services) and typer.confirm("Start selected services with docker compose?", default=False)

    save_plan(plan)
    render_plan(console, settings, plan)
    console.print(f"[green]saved[/green] [dim]{load_app_config().path}[/dim]")
    if start:
        code = start_services(plan.services, console)
        if code:
            raise typer.Exit(code=code)
    console.print("[dim]Next: relaycli doctor[/dim]")
    return plan
