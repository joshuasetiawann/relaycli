"""Configuration manager — read/write config.toml, CLI subcommands."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from relaycli.core.config import CONFIG_FILE, ensure_config_dir, get_settings, reload_settings
from relaycli.core.llm import ollama_models
from relaycli.tools.base import atomic_write
from relaycli.core.roles import BUILTIN_ROLES, TIERS, builtin_role

PROVIDER_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY", "mistral": "MISTRAL_API_KEY", "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "dashscope": "DASHSCOPE_API_KEY", "zhipu": "ZHIPUAI_API_KEY",
}
DEFAULT_PREFERENCES: dict[str, object] = {"permission_mode": "suggest", "theme": "dark", "max_context_tokens": 120_000}
DEFAULT_TIERS: dict[str, str] = {
    "fast": "ollama_chat/qwen2.5-coder:1.5b",
    "balanced": "ollama_chat/qwen2.5-coder:7b",
    "strong": "ollama_chat/qwen2.5-coder:14b",
}
RUNTIME_OPTION_KEYS = frozenset({"permission_mode", "relay_enabled", "relay_explorer", "relay_tester", "relay_split_tasks", "skills_auto", "local_scaffolds", "token_budget"})


@dataclass
class ProviderConfig:
    api_key: str | None = None
    base_url: str | None = None


@dataclass
class RoleConfig:
    enabled: bool | None = None
    model: str | None = None


@dataclass
class AppConfig:
    path: Path
    preferences: dict[str, object] = field(default_factory=dict)
    tiers: dict[str, str] = field(default_factory=dict)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    roles: dict[str, RoleConfig] = field(default_factory=dict)
    _raw: dict = field(default_factory=dict)

    def preference(self, key: str) -> object:
        return self.preferences.get(key, DEFAULT_PREFERENCES.get(key))

    def tier_model(self, tier: str) -> str | None:
        return self.tiers.get(tier) or DEFAULT_TIERS.get(tier)

    def role_enabled(self, role_id: str) -> bool:
        rc = self.roles.get(role_id)
        if rc is not None and rc.enabled is not None:
            return rc.enabled
        b = builtin_role(role_id)
        return b.enabled_by_default if b else True

    def role_assignment(self, role_id: str) -> str:
        rc = self.roles.get(role_id)
        if rc is not None and rc.model:
            return rc.model
        b = builtin_role(role_id)
        return b.default_model_tier if b else "balanced"


def _provider_env(provider: str) -> str:
    return PROVIDER_ENV.get(provider, f"{provider.upper()}_API_KEY")


def load_app_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_FILE
    raw: dict = {}
    if path.exists():
        try:
            with open(path, "rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            raw = {}
    providers = {name: ProviderConfig(api_key=tbl.get("api_key"), base_url=tbl.get("base_url")) for name, tbl in (raw.get("providers") or {}).items() if isinstance(tbl, dict)}
    roles = {rid: RoleConfig(enabled=tbl.get("enabled"), model=tbl.get("model")) for rid, tbl in (raw.get("roles") or {}).items() if isinstance(tbl, dict)}
    return AppConfig(path=path, preferences=dict(raw.get("preferences") or {}), tiers=dict(raw.get("models") or {}), providers=providers, roles=roles, _raw=raw)


def save_app_config(cfg: AppConfig) -> None:
    ensure_config_dir()
    raw = dict(cfg._raw)
    if cfg.preferences:
        raw["preferences"] = dict(cfg.preferences)
    if cfg.tiers:
        raw["models"] = dict(cfg.tiers)
    if cfg.providers:
        raw["providers"] = {name: {k: v for k, v in (("api_key", p.api_key), ("base_url", p.base_url)) if v is not None} for name, p in cfg.providers.items()}
    if cfg.roles:
        raw["roles"] = {rid: {k: v for k, v in (("enabled", r.enabled), ("model", r.model)) if v is not None} for rid, r in cfg.roles.items()}
    cfg._raw = raw
    atomic_write(cfg.path, _dump_toml(raw))
    try:
        os.chmod(cfg.path, 0o600)
    except OSError:
        pass


def set_base_model(model: str, path: Path | None = None) -> None:
    model = model.strip()
    if not model:
        raise ValueError("model id required")
    cfg = load_app_config(path)
    cfg._raw["model"] = model
    recent = cfg._raw.get("recent_models")
    if not isinstance(recent, list):
        recent = []
    cfg._raw["recent_models"] = [model, *[str(m) for m in recent if str(m) != model]][:8]
    save_app_config(cfg)


def set_runtime_option(key: str, value: object, path: Path | None = None) -> None:
    key = key.strip()
    if key not in RUNTIME_OPTION_KEYS and key not in DEFAULT_PREFERENCES:
        raise ValueError(f"unsupported runtime option: {key}")
    cfg = load_app_config(path)
    if key in DEFAULT_PREFERENCES:
        cfg.preferences[key] = value
    if key == "max_context_tokens":
        cfg._raw["token_budget"] = int(value)
    elif key != "theme":
        cfg._raw[key] = str(value) if key == "permission_mode" else value
    save_app_config(cfg)


def recent_models(path: Path | None = None) -> list[str]:
    raw = load_app_config(path)._raw.get("recent_models")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        model = str(item).strip()
        if model and model not in out:
            out.append(model)
    return out


def mask_key(raw: str | None) -> str:
    if not raw:
        return "not set"
    if raw.startswith("env:"):
        return f"via env ({raw[4:]})"
    if len(raw) <= 8:
        return "•" * len(raw)
    return f"{raw[:3]}…{raw[-4:]}"


def resolve_provider_key(cfg: AppConfig, provider: str) -> str | None:
    env_var = _provider_env(provider)
    if os.environ.get(env_var):
        return os.environ[env_var]
    stored = cfg.providers.get(provider)
    if stored is None or not stored.api_key:
        return None
    if stored.api_key.startswith("env:"):
        return os.environ.get(stored.api_key[4:])
    return stored.api_key


def resolve_role_model(cfg: AppConfig, role_id: str) -> tuple[str | None, str | None]:
    assigned = cfg.role_assignment(role_id)
    if assigned in TIERS:
        model = cfg.tier_model(assigned)
        if not model:
            return None, f"tier '{assigned}' has no model set (config tier {assigned} <model-id>)"
        return model, None
    return assigned, None


@dataclass
class ResolvedRole:
    id: str
    display_name: str
    enabled: bool
    assigned: str
    model: str | None
    error: str | None


def effective_roles(cfg: AppConfig) -> list[ResolvedRole]:
    ids: list[str] = [r.id for r in BUILTIN_ROLES]
    for rid in cfg.roles:
        if rid not in ids:
            ids.append(rid)
    out: list[ResolvedRole] = []
    for rid in ids:
        b = builtin_role(rid)
        model, error = resolve_role_model(cfg, rid)
        out.append(ResolvedRole(id=rid, display_name=b.display_name if b else rid, enabled=cfg.role_enabled(rid), assigned=cfg.role_assignment(rid), model=model, error=error))
    return out


# ── CLI subcommands ──────────────────────────────────────────────────────
console = Console()
config_app = typer.Typer(name="config", help="Manage roles, per-role models, tiers, and provider keys.", no_args_is_help=False, invoke_without_command=True)


@config_app.callback(invoke_without_command=True)
def _config_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        from relaycli.config.menu import run_configuration
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


def _model_table(title: str, rows: list[dict]) -> Table:
    table = Table(title=title, show_header=True, header_style="bold", box=None)
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("provider", style="cyan", no_wrap=True)
    table.add_column("model")
    table.add_column("source", style="dim", no_wrap=True)
    table.add_column("note", style="dim")
    for i, row in enumerate(rows, 1):
        model = str(row["id"])
        if row.get("current"):
            model = f"[green]{escape(model)}[/green]"
        else:
            model = escape(model)
        table.add_row(str(i), escape(str(row.get("provider") or row.get("group") or "")), model, escape(str(row.get("source") or "catalog")), escape(str(row.get("desc") or "")))
    return table


@config_app.command("show")
def show() -> None:
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
@config_app.command("model")  # type: ignore
def set_model(role: str, model: str) -> None:
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
    if name not in TIERS:
        _die(f"Unknown tier '{name}'. Tiers: {', '.join(TIERS)}")
    model = model.strip()
    if not model:
        _die("Model id required.")
    cfg = load_app_config()
    cfg.tiers[name] = model
    save_app_config(cfg)
    console.print(f"tier [green]{name}[/green] → {escape(model)}")


@config_app.command("models")
def models(provider: str | None = typer.Option(None, "--provider", "-p", help="Filter provider/group."), search: str | None = typer.Option(None, "--search", "-s", help="Search model ids and notes."), live: bool = typer.Option(True, "--live/--catalog-only", help="Fetch live provider lists when keys exist."), limit: int = typer.Option(24, "--limit", help="Maximum rows to show.")) -> None:
    from relaycli.model_catalog import model_choices
    rows = model_choices(get_settings(), provider_filter=provider, query=search, live=live, timeout=0.35)
    if not rows:
        console.print("[yellow]No matching models.[/yellow]")
        raise typer.Exit(code=1)
    shown = rows[:max(1, limit)]
    console.print(_model_table("Available models", shown))
    if len(rows) > len(shown):
        console.print(f"[dim]{len(rows) - len(shown)} more hidden; narrow with --search/--provider or raise --limit.[/dim]")


@config_app.command("select-model")
def select_model(model: str) -> None:
    model = model.strip()
    if not model:
        _die("Model id required.")
    set_base_model(model)
    reload_settings()
    console.print(f"default model → [green]{escape(model)}[/green]")


@config_app.command("choose-model")
def choose_model(provider: str | None = typer.Option(None, "--provider", "-p"), search: str | None = typer.Option(None, "--search", "-s"), live: bool = typer.Option(True, "--live/--catalog-only")) -> None:
    from relaycli.model_catalog import model_choices
    rows = model_choices(get_settings(), provider_filter=provider, query=search, live=live, timeout=0.35)
    if not rows:
        _die("No matching models.")
    console.print(_model_table("Choose a model", rows))
    answer = typer.prompt("Pick number or model id").strip()
    if answer.isdigit():
        idx = int(answer)
        if idx < 1 or idx > len(rows):
            _die("Pick number is out of range.")
        model = str(rows[idx - 1]["id"])
    else:
        model = answer
    select_model(model)


@config_app.command("ollama")
def ollama(search: str | None = typer.Option(None, "--search", "-s", help="Search installed model names.")) -> None:
    settings = get_settings()
    query = (search or "").lower().strip()
    names = [name for name in ollama_models(settings, timeout=0.8) if not query or query in name.lower()]
    if not names:
        console.print("[yellow]No installed Ollama models found.[/yellow]")
        console.print(f"[dim]host: {settings.ollama_base_url}[/dim]")
        raise typer.Exit(code=1)
    rows = [{"id": f"ollama_chat/{name}", "provider": "ollama", "source": "installed", "desc": f"local · {name}", "current": settings.model == f"ollama_chat/{name}"} for name in names]
    console.print(_model_table("Installed Ollama models", rows))
    console.print(f"[dim]host: {settings.ollama_base_url}[/dim]")


@config_app.command("ollama-pull")
def ollama_pull(model: str) -> None:
    from relaycli.model_catalog import pull_ollama_model
    settings = get_settings()
    console.print(f"Ollama pull → [cyan]{escape(model)}[/cyan]  [dim]({settings.ollama_base_url})[/dim]")
    try:
        name = pull_ollama_model(settings, model)
    except Exception as exc:
        _die(str(exc))
    console.print(f"[green]installed[/green] ollama_chat/{escape(name)}")


@config_app.command("enable")
def enable(role: str) -> None:
    _set_enabled(role, True)


@config_app.command("disable")
def disable(role: str) -> None:
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
def set_key(provider: str, env: str = typer.Option(None, "--env", help="Store an env reference (VAR name)."), value: str = typer.Option(None, "--value", help="Store a literal key (masked; 0600 file).")) -> None:
    if provider not in PROVIDER_ENV:
        _die(f"Unknown provider '{provider}'. Known: {', '.join(PROVIDER_ENV)}")
    if env and value:
        _die("Use only one of --env / --value.")
    if env:
        stored, how = f"env:{env}", f"env reference → {env}"
    elif value:
        stored, how = value.strip(), f"stored literal (masked: {mask_key(value.strip())})"
    else:
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
    console.print(str(load_app_config().path))


# ── TOML writer (stdlib has reader, not writer) ──────────────────────────
def _toml_scalar(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    raise TypeError(f"cannot serialize {type(v).__name__} to TOML")


def _dump_table(name: str, tbl: dict, lines: list[str]) -> None:
    scalars = {k: v for k, v in tbl.items() if not isinstance(v, dict)}
    subtables = {k: v for k, v in tbl.items() if isinstance(v, dict)}
    lines.append(f"[{name}]")
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_scalar(v)}")
    for k, v in subtables.items():
        lines.append("")
        _dump_table(f"{name}.{k}", v, lines)


def _dump_toml(data: dict) -> str:
    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_scalar(v)}")
    for k, v in tables.items():
        lines.append("")
        _dump_table(k, v, lines)
    return "\n".join(lines).lstrip("\n") + "\n"
