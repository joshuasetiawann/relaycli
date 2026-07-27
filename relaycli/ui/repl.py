"""Interactive REPL (prompt_toolkit) + slash-commands."""

from __future__ import annotations

import os
import subprocess
import sys
from difflib import get_close_matches
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from relaycli.core.config import CONFIG_DIR, PermissionMode, Settings, ensure_config_dir, get_settings, reload_settings
from relaycli.core.context import ProjectContext
from relaycli.core.llm import is_warm, key_status, ollama_models, preflight_settings
from relaycli.core.permissions import PermissionManager
from relaycli.ui.render import (
    RichReporter, RelayRichObserver, render_help, render_local_reply,
    render_model_warning, render_relay_summary, render_routing_banner,
    render_setup_panel, render_slash_guide, render_task_summary, render_welcome,
    short_model_name,
)


_PT_STYLE = Style.from_dict({
    "prompt": "#D97757 bold",
    "bottom-toolbar": "noreverse fg:#808080 bg:default",
    "completion-menu": "bg:#1c1c1c fg:#b8b8b8",
    "completion-menu.completion.current": "bg:#3a3a3a fg:#ffffff",
    "completion-menu.meta.completion": "bg:#1c1c1c fg:#6a6a6a",
    "completion-menu.meta.completion.current": "bg:#3a3a3a fg:#b8b8b8",
    "scrollbar.background": "bg:#1c1c1c",
    "scrollbar.button": "bg:#3a3a3a",
})


class SlashCompleter(Completer):
    def __init__(self, arg_providers: dict | None = None) -> None:
        self._arg_providers = arg_providers or {}

    def get_completions(self, document, complete_event):
        from relaycli.slash import ARG_COMPLETIONS as _ARG_COMPLETIONS
        from relaycli.slash import COMMANDS as SLASH_COMMAND_ROWS

        text = document.text_before_cursor
        if "\n" in document.text or not text.startswith("/"):
            return
        head, sep, arg = text[1:].partition(" ")
        if not sep:
            prefix = head.lower()
            rows = [row for row in SLASH_COMMAND_ROWS if row.name.startswith(prefix)]
            if prefix and not rows:
                names = [row.name for row in SLASH_COMMAND_ROWS]
                matched = set(get_close_matches(prefix, names, n=4, cutoff=0.45))
                rows = [row for row in SLASH_COMMAND_ROWS if row.name in matched]
            for row in rows:
                yield Completion("/" + row.name, start_position=-len(text),
                                 display=row.usage, display_meta=f"{row.group} · {row.description}")
            return
        if " " in arg:
            return
        cmd = head.lower()
        provider = self._arg_providers.get(cmd)
        options = tuple(provider()) if provider else _ARG_COMPLETIONS.get(cmd, ())
        for option in options:
            if option.startswith(arg):
                yield Completion(option, start_position=-len(arg))


class Repl:
    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()
        self.project = ProjectContext(Path.cwd())
        self.permissions = PermissionManager(settings.permission_mode, console=self.console)
        self._manual_model_selected = False
        self._manual_slow_warning_shown_for: str | None = None
        self._last_actionable_request: str | None = None
        from relaycli.mcp.bridge import enabled_servers, extend_registry
        from relaycli.tools.registry import default_registry

        servers = enabled_servers()
        if servers:
            self.console.print(f"[dim]connecting to {len(servers)} MCP connector{'s' if len(servers) != 1 else ''} ({', '.join(servers)})…[/dim]")
        from relaycli.agent.loop import Agent
        self.agent = Agent(
            settings, console=self.console, project=self.project,
            permissions=self.permissions, registry=extend_registry(default_registry(), console=self.console),
        )
        from relaycli.skills import discover_skills
        self.skills = discover_skills(self.project.root)
        self.active_skills: list[str] = []
        self._desktop_url: str | None = None

    def run(self) -> None:
        if not sys.stdin.isatty():
            self.console.print("[red]No interactive terminal detected.[/red] Use [bold]relaycli -p \"<request>\"[/bold] for non-interactive runs.")
            raise SystemExit(2)
        self._print_banner()
        session = self._build_prompt_session()
        while True:
            try:
                line = session.prompt(self._prompt_text())
            except EOFError:
                break
            except KeyboardInterrupt:
                continue
            line = line.strip()
            if not line:
                continue
            if self._handle_line(line):
                break
        self.console.print("[dim]bye.[/dim]")

    def _build_prompt_session(self) -> PromptSession:
        ensure_config_dir()
        history_path = CONFIG_DIR / "history"
        try:
            history_path.touch(mode=0o600, exist_ok=True)
            os.chmod(history_path, 0o600)
        except OSError:
            pass
        history = FileHistory(str(history_path))
        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event) -> None:
            self._submit_or_complete(event.current_buffer)

        @kb.add("escape", "enter")
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        return PromptSession(
            history=history, key_bindings=kb, multiline=True,
            completer=SlashCompleter(arg_providers={"skill": lambda: sorted(self.skills) + ["auto"]}),
            complete_while_typing=True, bottom_toolbar=self._toolbar, style=_PT_STYLE,
        )

    @staticmethod
    def _submit_or_complete(buffer) -> None:
        state = buffer.complete_state
        if state and state.current_completion:
            buffer.apply_completion(state.current_completion)
            return
        buffer.validate_and_handle()

    def _toolbar(self) -> str:
        relay = "relay on" if self.settings.relay_enabled else "relay off"
        parts = (f"model {short_model_name(self.settings.model)}", f"mode {self.permissions.mode}", relay, "/help", "/")
        return " " + " · ".join(parts) + " "

    def _prompt_text(self) -> list[tuple[str, str]]:
        return [("class:prompt", "❯ ")]

    def _print_banner(self) -> None:
        render_welcome(self.console, self.settings, self.project.root, key_status(self.settings))
        problem = preflight_settings(self.settings)
        if problem:
            render_setup_panel(self.console, problem, self.settings.detected_providers())
        if self.settings.permission_mode is PermissionMode.full_auto:
            self.console.print("[yellow]full-auto: edits & commands run without asking[/yellow]")

    def _print_routing(self) -> None:
        render_routing_banner(self.console, self.settings)

    def _handle_line(self, line: str) -> bool:
        if line.startswith("/"):
            return self._handle_slash(line)
        if line.startswith("!"):
            self._run_user_shell(line[1:].strip())
            return False
        if line.startswith("-"):
            flag = escape(line.split()[0])
            self.console.print(f"[yellow]Flags like [bold]{flag}[/bold] belong on the relaycli command line, not inside the session.[/yellow] [dim]Try [cyan]/help[/cyan] for session commands, or rephrase without the leading dash to send it to the model.[/dim]")
            return False
        lowered = line.lower()
        if lowered in ("help", "?"):
            render_help(self.console)
            return False
        if lowered in ("exit", "quit"):
            return True
        run_line = self._continuation_for(line) or line
        reply = None if run_line != line else self._local_reply_for(line)
        if reply is not None:
            render_local_reply(self.console, reply)
            return False
        if run_line != line:
            self.console.print("[dim]continuing the previous request with your follow-up[/dim]")
        self._last_actionable_request = run_line
        self._run_agent(run_line)
        return False

    def _run_user_shell(self, cmd: str) -> None:
        if not cmd:
            self.console.print("[dim]usage: !<command>   e.g. !git status[/dim]")
            return
        if "\n" in cmd:
            self.console.print("[yellow]Multiline !command not run (pasted text?).[/yellow] [dim]Run shell commands one line at a time.[/dim]")
            return
        try:
            proc = subprocess.run(cmd, shell=True, cwd=self.project.root, capture_output=True, text=True, errors="replace")
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted — back to prompt.[/yellow]")
            return
        except (OSError, ValueError) as exc:
            self.console.print(f"[red]shell failed:[/red] {escape(str(exc))}")
            return
        if proc.stdout:
            self.console.print(escape(proc.stdout.rstrip("\n")))
        if proc.stderr:
            self.console.print(f"[red]{escape(proc.stderr.rstrip('\n'))}[/red]")
        style = "dim" if proc.returncode == 0 else "red"
        self.console.print(f"[{style}]↳ exit {proc.returncode}[/{style}]")

    def _warm_note(self) -> None:
        if not is_warm():
            self.console.print("[dim]loading provider libraries — the first call can take a while…[/dim]")

    def _auto_skills_for(self, request: str) -> list[str]:
        if not self.settings.skills_auto:
            return []
        from relaycli.skills import auto_match
        names = auto_match(self.skills, request, active=tuple(self.active_skills))
        for name in names:
            self.console.print(f"[dim]✦ auto-skill: [cyan]{escape(name)}[/cyan][/dim]")
        return names

    def _ensure_runnable_local_model(self) -> bool:
        from relaycli.ollama_runtime import slow_local_model_warning
        warning = slow_local_model_warning(self.settings.model)
        if not warning:
            return True
        if self._manual_model_selected:
            self._print_manual_slow_warning_once(warning)
            return True
        self.console.print(f"[yellow]⚠ {escape(warning)}[/yellow]")
        self.console.print("[dim]auto-switch disabled — keeping your model. Consider using a smaller GPU-safe model like `ollama_chat/qwen2.5-coder:0.5b` with `/model` if it's too slow.[/dim]")
        return True

    def _print_manual_slow_warning_once(self, warning: str) -> None:
        if self._manual_slow_warning_shown_for == self.settings.model:
            return
        self.console.print(f"[yellow]⚠ {escape(short_model_name(self.settings.model))} may be slow (needs 100% GPU)[/yellow]")
        self._manual_slow_warning_shown_for = self.settings.model

    def _run_agent(self, request: str) -> None:
        if not self._ensure_runnable_local_model():
            return
        auto = self._auto_skills_for(request)
        if self.settings.relay_enabled:
            self._run_relay(request, auto_skills=auto)
            return
        self._warm_note()
        if auto:
            self.agent.set_skills_block(self._skills_block(extra=auto))
        reporter = RichReporter(self.console)
        try:
            result = self.agent.run(request, reporter=reporter)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted — back to prompt.[/yellow]")
            return
        except Exception as exc:
            self.console.print(f"\n[red]Error: {escape(str(exc).splitlines()[0])}[/red]")
            return
        finally:
            reporter.close()
            if auto:
                self.agent.set_skills_block(self._skills_block())
        render_task_summary(self.console, result, reporter.tools_used)
        self._maybe_setup_hint(result)

    def _run_relay(self, request: str, auto_skills: list[str] | None = None) -> None:
        from relaycli.agent.pipeline import Relay
        if not self._ensure_runnable_local_model():
            return
        self._warm_note()
        relay = Relay(self.settings, console=self.console, project=self.project, permissions=self.permissions, skills_block=self._skills_block(extra=auto_skills or ()))
        observer = RelayRichObserver(self.console)
        try:
            result = relay.run(request, observer=observer)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted — back to prompt.[/yellow]")
            return
        finally:
            observer.close()
        render_relay_summary(self.console, result)
        self._maybe_setup_hint(result)

    def _maybe_setup_hint(self, result) -> None:
        if result.stopped_reason != "error":
            return
        text = result.final_text or ""
        if "No API key configured" not in text:
            return
        problem = preflight_settings(self.settings) or text
        render_setup_panel(self.console, problem, self.settings.detected_providers())

    def _handle_slash(self, line: str) -> bool:
        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        from relaycli.slash import SLASH_COMMANDS
        if not cmd:
            render_slash_guide(self.console)
        elif cmd in ("exit", "quit"):
            return True
        elif cmd == "help":
            render_help(self.console)
        elif cmd in ("setup", "init"):
            self._cmd_setup()
        elif cmd == "model":
            self._cmd_model(arg)
        elif cmd == "mode":
            self._cmd_mode(arg)
        elif cmd == "relay":
            self._cmd_relay(arg)
        elif cmd == "agents":
            self._cmd_agents(arg)
        elif cmd == "skill":
            self._cmd_skill(arg)
        elif cmd == "skills":
            self._cmd_skills()
        elif cmd == "config":
            from relaycli.config.menu import run_configuration
            run_configuration(self.console)
        elif cmd == "settings":
            from relaycli.config.menu import run_settings
            run_settings(self.console)
        elif cmd == "services":
            self._cmd_services(arg)
        elif cmd == "doctor":
            self._cmd_doctor()
        elif cmd == "memory":
            self._cmd_memory()
        elif cmd == "desktop":
            self._cmd_desktop()
        elif cmd == "mcp":
            self._cmd_mcp()
        elif cmd == "diff":
            self._cmd_diff()
        elif cmd == "clear":
            self.agent.session.reset()
            self.console.print("[dim]conversation cleared.[/dim]")
        else:
            suggestion = get_close_matches(cmd, SLASH_COMMANDS, n=1, cutoff=0.55)
            hint = f" Did you mean [cyan]/{suggestion[0]}[/cyan]?" if suggestion else ""
            self.console.print(f"[red]Unknown command:[/red] /{escape(cmd)}.{hint} [dim]Type / to browse commands.[/dim]")
        return False

    def _persist_runtime_option(self, key: str, value: object) -> None:
        try:
            from relaycli.appconfig import set_runtime_option
            set_runtime_option(key, value)
        except Exception:
            pass

    def _cmd_setup(self) -> None:
        from relaycli.onboarding import run_init
        try:
            run_init(console=self.console)
        except typer.Exit as exc:
            if exc.exit_code:
                self.console.print("[dim]setup cancelled.[/dim]")
            return
        fresh = reload_settings()
        self.settings.model = fresh.model
        self.settings.permission_mode = fresh.permission_mode
        self.permissions.set_mode(fresh.permission_mode)
        self.agent.session.model = fresh.model
        self.agent.refresh_system_prompt()
        self.console.print("[dim]setup applied to this session.[/dim]")

    def _cmd_doctor(self) -> None:
        from relaycli.doctor import render_checks, run_checks
        checks = run_checks(self.settings, self.project.root, live=False)
        render_checks(self.console, checks)

    def _cmd_services(self, arg: str) -> None:
        from rich.table import Table
        from relaycli.onboarding import SERVICE_DESCRIPTIONS, normalize_services, start_services

        if not arg:
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
            table.add_column("service", style="cyan", no_wrap=True)
            table.add_column("what it adds")
            for name, desc in SERVICE_DESCRIPTIONS.items():
                table.add_row(name, desc)
            self.console.print(table)
            self.console.print("[dim]/services start ollama,n8n  · uses docker compose profiles[/dim]")
            return
        raw = arg.split(" ", 1)[1] if arg.startswith("start ") else arg
        try:
            services = normalize_services(raw.replace(" ", ","))
        except typer.BadParameter as exc:
            self.console.print(f"[red]{escape(str(exc))}[/red]")
            return
        if not services:
            self.console.print("[dim]usage: /services start ollama,n8n[/dim]")
            return
        code = start_services(services, self.console)
        if code == 0:
            self.console.print(f"[green]services started:[/green] {escape(', '.join(services))}")

    def _cmd_model(self, name: str) -> None:
        from relaycli.model_catalog import model_choices

        arg = (name or "").strip()
        if not arg:
            self.console.print(f"model: [green]{escape(self.settings.model)}[/green]")
            installed = ollama_models(self.settings)
            if installed:
                self.console.print("[dim]local Ollama:[/dim] " + escape(", ".join(f"ollama_chat/{m}" for m in installed[:8])))
            self.console.print("[dim]use: /model search <text> · /model provider <name> · /model <exact-id>[/dim]")
            return
        if arg.startswith("search "):
            query = arg.split(None, 1)[1].strip()
            self._print_model_matches(model_choices(self.settings, query=query, timeout=0.35))
            return
        if arg.startswith("provider "):
            provider = arg.split(None, 1)[1].strip()
            self._print_model_matches(model_choices(self.settings, provider_filter=provider, timeout=0.35))
            return
        if arg in {"ollama", "local"}:
            self._print_model_matches(model_choices(self.settings, provider_filter="ollama", timeout=0.8))
            return
        if arg.startswith(("ollama_chat/", "ollama/")):
            local_name = arg.split("/", 1)[1]
            installed = ollama_models(self.settings, timeout=0.8)
            if installed and local_name not in installed:
                self.console.print(f"[red]Ollama model not installed:[/red] {escape(local_name)}")
                self.console.print("[dim]installed: " + escape(", ".join(installed)) + "[/dim]")
                self.console.print(f"[dim]install it with: relaycli config ollama-pull {escape(local_name)}[/dim]")
                return
        self.settings.model = arg
        self._manual_model_selected = True
        self._manual_slow_warning_shown_for = None
        try:
            from relaycli.appconfig import set_base_model
            set_base_model(arg)
        except Exception:
            pass
        self.agent.refresh_system_prompt()
        self.console.print(f"model → [green]{escape(arg)}[/green]")
        render_model_warning(self.console, self.settings)
        from relaycli.ollama_runtime import slow_local_model_warning
        warning = slow_local_model_warning(self.settings.model)
        if warning:
            self._print_manual_slow_warning_once(warning)

    def _print_model_matches(self, rows: list[dict]) -> None:
        from rich.table import Table
        if not rows:
            self.console.print("[yellow]No matching models.[/yellow]")
            return
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("model")
        table.add_column("provider", no_wrap=True)
        table.add_column("source", no_wrap=True)
        table.add_column("note")
        for row in rows[:24]:
            model = str(row["id"])
            style = "green" if row.get("current") else None
            table.add_row(f"[{style}]{escape(model)}[/{style}]" if style else escape(model), escape(str(row.get("provider") or row.get("group") or "")), escape(str(row.get("source") or "")), escape(str(row.get("desc") or "")))
        self.console.print(table)
        if len(rows) > 24:
            self.console.print(f"[dim]{len(rows) - 24} more hidden; narrow with /model search <text>[/dim]")
        self.console.print("[dim]switch with: /model <exact model id>[/dim]")

    def _cmd_mode(self, value: str) -> None:
        if not value:
            self.console.print(f"mode: [yellow]{self.permissions.mode}[/yellow]")
            return
        try:
            mode = PermissionMode(value)
        except ValueError:
            self.console.print("[red]Invalid mode.[/red] Use suggest | auto-edit | full-auto.")
            return
        self.settings.permission_mode = mode
        self.permissions.set_mode(mode)
        self.agent.refresh_system_prompt()
        self._persist_runtime_option("permission_mode", str(mode))
        self.console.print(f"mode → [yellow]{mode}[/yellow]")
        if mode is PermissionMode.full_auto:
            self.console.print("[bold yellow]⚠ full-auto:[/bold yellow] edits and commands run without asking.")

    def _cmd_relay(self, value: str) -> None:
        if not value:
            state = "on" if self.settings.relay_enabled else "off"
            self.console.print(f"relay: [cyan]{state}[/cyan]")
            if self.settings.relay_enabled:
                self._print_routing()
            return
        if value not in ("on", "off"):
            self.console.print(r"[red]Usage:[/red] /relay [on|off]")
            return
        self.settings.relay_enabled = value == "on"
        self._persist_runtime_option("relay_enabled", self.settings.relay_enabled)
        self.console.print(f"relay → [cyan]{value}[/cyan]")
        if self.settings.relay_enabled:
            self._print_routing()

    _ROLE_PURPOSE = {
        "explorer": "scouts the codebase before planning (read-only, optional)",
        "planner": "writes the implementation plan (read-only)",
        "coder": "does the work — edits files, runs commands",
        "tester": "runs the plan's verification step (optional)",
        "reviewer": "verifies the result; issues approve/revise",
    }

    def _cmd_agents(self, arg: str) -> None:
        from relaycli.agent.router import Role, resolve_model, role_enabled

        if arg:
            parts = arg.split()
            if len(parts) == 2 and parts[0] in ("explorer", "tester", "tasks") and parts[1] in ("on", "off"):
                field = "relay_split_tasks" if parts[0] == "tasks" else f"relay_{parts[0]}"
                setattr(self.settings, field, parts[1] == "on")
                self._persist_runtime_option(field, getattr(self.settings, field))
                self.console.print(f"agent {parts[0]} → [cyan]{parts[1]}[/cyan]")
                if parts[1] == "on" and not self.settings.relay_enabled:
                    self.console.print("[dim]note: agents run inside the relay pipeline — /relay on to use them.[/dim]")
                return
            self.console.print(r"[red]Usage:[/red] /agents [explorer|tester|tasks on|off]")
            return
        from rich.table import Table
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("agent", no_wrap=True)
        table.add_column("model", no_wrap=True)
        table.add_column("purpose")
        for role in Role:
            on = role_enabled(self.settings, role)
            dot = "[green]●[/green]" if on else "[dim]○[/dim]"
            model = short_model_name(resolve_model(self.settings, role))
            style = "" if on else "[dim]"
            end = "" if on else "[/dim]"
            table.add_row(f"{dot} {role}", f"{style}{escape(model)}{end}", f"{style}{self._ROLE_PURPOSE[str(role)]}{end}")
        self.console.print(table)
        relay_state = "on" if self.settings.relay_enabled else "off"
        split_state = "on" if self.settings.relay_split_tasks else "off"
        self.console.print(f"[dim]relay {relay_state} · task-split {split_state} (delegates each plan task to a specialist) · /agents explorer|tester|tasks on|off[/dim]")
        if self.settings.relay_split_tasks:
            from relaycli.roster import enabled_specialists
            specialists = enabled_specialists()
            if specialists:
                self.console.print(f"[dim]specialists (task-split): {escape(', '.join(specialists))}  · enable more with /config[/dim]")

    def _skills_block(self, extra: tuple[str, ...] | list[str] = ()) -> str:
        from relaycli.skills import skills_prompt_block
        names = list(self.active_skills) + [n for n in extra if n not in self.active_skills]
        return skills_prompt_block([self.skills[n] for n in names if n in self.skills])

    def _cmd_skill(self, name: str) -> None:
        if not name:
            self._cmd_skills()
            return
        if name.split()[0] == "auto":
            self._cmd_skill_auto(name.split()[1:])
            return
        skill = self.skills.get(name)
        if skill is None:
            self.console.print(f"[red]Unknown skill:[/red] {escape(name)}  (see /skills)")
            return
        if name in self.active_skills:
            self.active_skills.remove(name)
            state = "[dim]off[/dim]"
        else:
            self.active_skills.append(name)
            state = "[green]on[/green]"
        self.agent.set_skills_block(self._skills_block())
        self.console.print(f"skill {escape(name)} → {state}")

    def _cmd_skill_auto(self, args: list[str]) -> None:
        if not args:
            state = "on" if self.settings.skills_auto else "off"
            self.console.print(f"skill auto: [cyan]{state}[/cyan]")
            return
        if args[0] not in ("on", "off"):
            self.console.print(r"[red]Usage:[/red] /skill auto [on|off]")
            return
        value = args[0] == "on"
        self.settings.skills_auto = value
        self._persist_runtime_option("skills_auto", value)
        self.console.print(f"skill auto → [cyan]{args[0]}[/cyan]")

    def _cmd_skills(self) -> None:
        from rich.table import Table
        if not self.skills:
            self.console.print("[dim]no skills found.[/dim]")
            return
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("skill", no_wrap=True)
        table.add_column("source", style="dim", no_wrap=True)
        table.add_column("description")
        for name in sorted(self.skills):
            skill = self.skills[name]
            dot = "[green]●[/green]" if name in self.active_skills else "[dim]○[/dim]"
            table.add_row(f"{dot} {escape(name)}", skill.source, escape(skill.description))
        self.console.print(table)
        auto_state = "on" if self.settings.skills_auto else "off"
        self.console.print(f"[dim]/skill <name> toggles · auto-activation {auto_state} (/skill auto on|off) · active skills steer the agent (and the relay coder) · drop your own .md in ~/.relaycli/skills/[/dim]")

    def _cmd_mcp(self) -> None:
        from relaycli.mcp.bridge import server_status
        rows = server_status()
        if not rows:
            self.console.print("[dim]no MCP connectors configured — add one with [cyan]relaycli mcp add <preset>[/cyan] (see relaycli mcp list).[/dim]")
            return
        from rich.table import Table
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("server", no_wrap=True)
        table.add_column("state", no_wrap=True)
        table.add_column("tools", no_wrap=True)
        table.add_column("command")
        for row in rows:
            style = {"running": "green", "failed": "red"}.get(row["state"], "dim")
            table.add_row(escape(row["name"]), f"[{style}]{row['state']}[/{style}]", str(row["tools"]), f"[dim]{escape(row['command'])}[/dim]")
        self.console.print(table)
        self.console.print("[dim]tools appear to the agent as mcp_<server>_<tool> · every call asks first (like run_command) · manage with relaycli mcp[/dim]")

    def _cmd_desktop(self) -> None:
        from relaycli.ui.web import _open_browser, serve_background
        if self._desktop_url is None:
            try:
                _server, self._desktop_url = serve_background(self.settings)
            except OSError as exc:
                self.console.print(f"[red]desktop failed to start:[/red] {exc}")
                return
        _open_browser(self._desktop_url)
        self.console.print(f"desktop → [cyan]{self._desktop_url}[/cyan]  [dim](loopback only · shares this session's settings · stays up until you quit)[/dim]")

    def _cmd_memory(self) -> None:
        from relaycli.core.memory import GLOBAL_MEMORY, project_memory_path, read_memory
        shown = False
        for label, path in (("global", GLOBAL_MEMORY), ("project", project_memory_path(self.project.root))):
            text = read_memory(path)
            if not text:
                continue
            shown = True
            self.console.print(f"[bold]{label}[/bold] [dim]{escape(str(path))}[/dim]")
            self.console.print(escape(text))
            self.console.print()
        if not shown:
            self.console.print("[dim]memory is empty — the agent saves facts with the remember tool, or edit the files yourself:[/dim]")
            self.console.print(f"[dim]  global   {escape(str(GLOBAL_MEMORY))}[/dim]")
            self.console.print(f"[dim]  project  {escape(str(project_memory_path(self.project.root)))}[/dim]")

    def _cmd_diff(self) -> None:
        if not (self.project.root / ".git").exists():
            self.console.print("[dim]not a git repository — no diff available.[/dim]")
            return
        try:
            proc = subprocess.run(["git", "-C", str(self.project.root), "diff", "--no-color"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            self.console.print(f"[red]git diff failed:[/red] {exc}")
            return
        out = proc.stdout.strip()
        if not out:
            self.console.print("[dim]no uncommitted changes.[/dim]")
            return
        self.console.print(Syntax(out, "diff", theme="ansi_dark", background_color="default"))

    @staticmethod
    def _continuation_for(text: str, last: str | None = None) -> str | None:
        from relaycli.intent import continuation_for as _cf
        return _cf(text, last)

    @staticmethod
    def _local_reply_for(text: str):
        from relaycli.intent import local_reply_for as _lf
        return _lf(text)


def run_repl(settings: Settings, console: Console | None = None) -> None:
    Repl(settings, console=console).run()
