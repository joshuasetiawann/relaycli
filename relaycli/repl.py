"""Interactive REPL (prompt_toolkit) + slash-commands.

This is presentation + entry only: it drives the existing Agent without
touching agent/tool/permission internals. Each user line runs one agent loop
and streams the output through :class:`relaycli.render.RichReporter`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from relaycli.agent import Agent
from relaycli.config import CONFIG_DIR, PermissionMode, Settings, ensure_config_dir
from relaycli.context import ProjectContext
from relaycli.llm import is_warm, key_status, preflight_settings
from relaycli.permissions import PermissionManager
from relaycli.render import (
    RichReporter,
    render_help,
    render_setup_panel,
    render_task_summary,
    render_welcome,
    short_model_name,
)


# The slash menu's source of truth: command -> (argument hint, description).
# Keep descriptions in step with render_help() — same commands, shorter text
# (the popup gives each entry one line).
SLASH_COMMANDS: dict[str, tuple[str, str]] = {
    "model": ("[name]", "show or switch the model"),
    "mode": ("[m]", "permission mode: suggest | auto-edit | full-auto"),
    "relay": ("[on|off]", "toggle the Planner → Coder → Reviewer pipeline"),
    "agents": ("[r on|off]", "show relay agents; toggle explorer/tester"),
    "skill": ("[name]", "toggle a skill on/off for this session"),
    "skills": ("", "list available skills (● = active)"),
    "config": ("", "roles, per-role models & provider keys (persistent)"),
    "settings": ("", "general preferences (mode, theme, context)"),
    "memory": ("", "show long-term memory (global + project)"),
    "desktop": ("", "open the desktop web UI in your browser"),
    "mcp": ("", "show MCP connectors and their tools"),
    "diff": ("", "show uncommitted changes (git diff)"),
    "clear": ("", "reset the conversation"),
    "help": ("", "show all commands and keys"),
    "exit": ("", "quit (aliases: exit, quit, Ctrl-D)"),
    "quit": ("", "quit (alias of /exit)"),
}

# First-argument suggestions. /model offers curated common ids — completion
# candidates only, not a whitelist: any id can still be typed in full.
_ARG_COMPLETIONS: dict[str, tuple[str, ...]] = {
    "mode": ("suggest", "auto-edit", "full-auto"),
    "relay": ("on", "off"),
    "agents": ("explorer", "tester", "tasks"),
    "model": (
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "groq/llama-3.3-70b-versatile",
        "mistral-large-latest",
        # OpenRouter suggestions are open-weights only (user preference;
        # verified against the live /models API 2026-07-03 — all have public
        # HF weights + tool support). :free variants cost nothing but share
        # capacity and can be rate-limited.
        "openrouter/cohere/north-mini-code:free",
        "openrouter/qwen/qwen3-coder:free",
        "openrouter/qwen/qwen3-coder-next",
        "openrouter/deepseek/deepseek-v4-flash",
        "openrouter/z-ai/glm-4.7",
        "openrouter/moonshotai/kimi-k2.6",
        "openrouter/openai/gpt-oss-120b:free",
        "ollama_chat/llama3.1",
    ),
}


# Claude Code-ish chrome: orange caret, quiet gray toolbar (no reverse
# video), dark completion menu with a subtle selection highlight.
_PT_STYLE = Style.from_dict(
    {
        "prompt": "#D97757 bold",
        "bottom-toolbar": "noreverse fg:#808080 bg:default",
        "completion-menu": "bg:#1c1c1c fg:#b8b8b8",
        "completion-menu.completion.current": "bg:#3a3a3a fg:#ffffff",
        "completion-menu.meta.completion": "bg:#1c1c1c fg:#6a6a6a",
        "completion-menu.meta.completion.current": "bg:#3a3a3a fg:#b8b8b8",
        "scrollbar.background": "bg:#1c1c1c",
        "scrollbar.button": "bg:#3a3a3a",
    }
)


class SlashCompleter(Completer):
    """Claude Code-style popup: type ``/`` and the commands appear.

    Completes only two shapes — a command name being typed at the start of
    the line, and that command's first argument. Plain text and multiline
    buffers (pastes) yield nothing, so the menu never pops mid-request.

    ``arg_providers`` maps a command to a callable returning its live
    argument candidates (e.g. discovered skill names), layered over the
    static ``_ARG_COMPLETIONS``.
    """

    def __init__(self, arg_providers: dict | None = None) -> None:
        self._arg_providers = arg_providers or {}

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if "\n" in document.text or not text.startswith("/"):
            return
        head, sep, arg = text[1:].partition(" ")
        if not sep:  # still typing the command name: filter by prefix
            for name, (hint, desc) in SLASH_COMMANDS.items():
                if name.startswith(head.lower()):
                    display = f"/{name} {hint}".rstrip()
                    yield Completion(
                        "/" + name,
                        start_position=-len(text),
                        display=display,
                        display_meta=desc,
                    )
            return
        if " " in arg:  # only the first argument is completable
            return
        cmd = head.lower()
        provider = self._arg_providers.get(cmd)
        options = tuple(provider()) if provider else _ARG_COMPLETIONS.get(cmd, ())
        for option in options:
            if option.startswith(arg):
                yield Completion(option, start_position=-len(arg))


class Repl:
    """A persistent interactive RelayCLI session."""

    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()
        self.project = ProjectContext(Path.cwd())
        self.permissions = PermissionManager(settings.permission_mode, console=self.console)
        from relaycli.mcp import enabled_servers, extend_registry
        from relaycli.tools import default_registry

        # Connecting to an MCP server can block for up to INIT_TIMEOUT (60s)
        # per server — e.g. an npx cold download or a hung process — and
        # this all runs before the welcome banner. Say so, or a slow/broken
        # connector reads as RelayCLI hanging on startup with no explanation.
        servers = enabled_servers()
        if servers:
            self.console.print(
                f"[dim]connecting to {len(servers)} MCP connector"
                f"{'s' if len(servers) != 1 else ''} "
                f"({', '.join(servers)})…[/dim]"
            )
        self.agent = Agent(
            settings,
            console=self.console,
            project=self.project,
            permissions=self.permissions,
            registry=extend_registry(default_registry(), console=self.console),
        )
        from relaycli.skills import discover_skills

        self.skills = discover_skills(self.project.root)
        self.active_skills: list[str] = []  # activation order = prompt order
        self._desktop_url: str | None = None  # set once /desktop starts the server

    # -- entry -----------------------------------------------------------
    def run(self) -> None:
        if not sys.stdin.isatty():
            self.console.print(
                "[red]No interactive terminal detected.[/red] "
                "Use [bold]relaycli -p \"<request>\"[/bold] for non-interactive runs."
            )
            raise SystemExit(2)

        self._print_banner()
        session = self._build_prompt_session()

        while True:
            try:
                line = session.prompt(self._prompt_text())
            except EOFError:  # Ctrl-D
                break
            except KeyboardInterrupt:  # Ctrl-C clears the current line
                continue

            line = line.strip()
            if not line:
                continue

            if self._handle_line(line):
                break

        self.console.print("[dim]bye.[/dim]")

    # -- prompt setup ----------------------------------------------------
    def _build_prompt_session(self) -> PromptSession:
        # 0700 dir + 0600 history: the history records typed prompts (which may
        # contain secrets), so keep it unreadable by other local users.
        ensure_config_dir()
        history_path = CONFIG_DIR / "history"
        try:
            history_path.touch(mode=0o600, exist_ok=True)
            os.chmod(history_path, 0o600)  # touch mode is subject to umask
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
            history=history,
            key_bindings=kb,
            multiline=True,
            completer=SlashCompleter(
                arg_providers={"skill": lambda: sorted(self.skills) + ["auto"]}
            ),
            complete_while_typing=True,
            bottom_toolbar=self._toolbar,
            style=_PT_STYLE,
        )

    @staticmethod
    def _submit_or_complete(buffer) -> None:
        """Enter accepts the highlighted completion when the menu is open,
        and submits otherwise — matches Claude Code muscle memory. A menu
        that is open but has no highlighted entry does not swallow Enter.
        """
        state = buffer.complete_state
        if state and state.current_completion:
            buffer.apply_completion(state.current_completion)
            return
        buffer.validate_and_handle()

    def _toolbar(self) -> str:
        """Live status line at the bottom of the terminal.

        Rendered by prompt_toolkit per keystroke, so /model, /mode and
        /relay changes show up on the very next prompt. Mode is read from
        ``self.permissions`` (what this REPL's agent actually enforces), not
        ``self.settings`` — if /desktop is open, the web UI's mode toggle
        mutates the shared Settings independently, and the toolbar must keep
        showing what THIS session enforces, not what a browser tab last set.
        """
        relay = "relay on" if self.settings.relay_enabled else "relay off"
        parts = (
            short_model_name(self.settings.model),
            str(self.permissions.mode),
            relay,
            "/help",
        )
        return " " + " · ".join(parts) + " "

    def _prompt_text(self) -> list[tuple[str, str]]:
        # A bare Claude-style caret: the session status (model · mode ·
        # relay) lives in the bottom toolbar, so the prompt stays minimal.
        return [("class:prompt", "❯ ")]

    def _print_banner(self) -> None:
        render_welcome(
            self.console, self.settings, self.project.root, key_status(self.settings)
        )
        # Non-blocking by design: the user can still /model or !cmd their way
        # out; only a real request would fail.
        problem = preflight_settings(self.settings)
        if problem:
            render_setup_panel(self.console, problem, self.settings.detected_providers())
        if self.settings.permission_mode is PermissionMode.full_auto:
            self._full_auto_banner()
        self.console.print()

    def _full_auto_banner(self) -> None:
        self.console.print(
            "[bold yellow]⚠ full-auto:[/bold yellow] edits and commands run "
            "without asking."
        )

    def _print_routing(self) -> None:
        from relaycli.render import render_routing_banner

        render_routing_banner(self.console, self.settings)

    # -- input dispatch ----------------------------------------------------
    def _handle_line(self, line: str) -> bool:
        """Dispatch one stripped, non-empty input line.

        Returns True when the REPL should exit. Order matters: command-ish
        shapes (slash, bang, leading dash, bare aliases) are intercepted so
        they are never sent to the model by accident.
        """
        if line.startswith("/"):
            return self._handle_slash(line)
        if line.startswith("!"):
            self._run_user_shell(line[1:].strip())
            return False
        if line.startswith("-"):
            flag = escape(line.split()[0])
            self.console.print(
                f"[yellow]Flags like [bold]{flag}[/bold] belong on the relaycli "
                f"command line, not inside the session.[/yellow] "
                f"[dim]Try [cyan]/help[/cyan] for session commands, or rephrase "
                f"without the leading dash to send it to the model.[/dim]"
            )
            return False
        lowered = line.lower()
        if lowered in ("help", "?"):
            render_help(self.console)
            return False
        if lowered in ("exit", "quit"):
            return True
        self._run_agent(line)
        return False

    def _run_user_shell(self, cmd: str) -> None:
        """Run a user-typed ``!cmd`` in the project root.

        Not permission-gated on purpose: the user typed it in their own
        terminal, so it is exactly as trusted as their shell. Output is
        captured (not streamed) so the Rich console stays consistent.
        """
        if not cmd:
            self.console.print("[dim]usage: !<command>   e.g. !git status[/dim]")
            return
        if "\n" in cmd:
            # A multiline buffer here is almost always a stray paste; running
            # every embedded line through the shell ungated is too surprising.
            self.console.print(
                "[yellow]Multiline !command not run (pasted text?).[/yellow] "
                "[dim]Run shell commands one line at a time.[/dim]"
            )
            return
        try:
            # errors="replace": commands may emit non-UTF-8 bytes (binaries,
            # odd encodings) and a strict decode would crash the whole REPL.
            proc = subprocess.run(
                cmd, shell=True, cwd=self.project.root, capture_output=True,
                text=True, errors="replace",
            )
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

    # -- running a task --------------------------------------------------
    def _warm_note(self) -> None:
        # LiteLLM is imported lazily on the first model call and that import
        # can take a long time from a cold disk; say so or it reads as a hang.
        if not is_warm():
            self.console.print(
                "[dim]loading provider libraries — the first call can take a while…[/dim]"
            )

    def _auto_skills_for(self, request: str) -> list[str]:
        """Per-request auto-activated skill names (announced, never silent)."""
        if not self.settings.skills_auto:
            return []
        from relaycli.skills import auto_match

        names = auto_match(self.skills, request, active=tuple(self.active_skills))
        for name in names:
            self.console.print(f"[dim]✦ auto-skill: [cyan]{escape(name)}[/cyan][/dim]")
        return names

    def _run_agent(self, request: str) -> None:
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
        finally:
            reporter.close()  # an error/Ctrl-C must not leave the spinner live
            if auto:  # auto picks last one request; manual toggles persist
                self.agent.set_skills_block(self._skills_block())
        render_task_summary(self.console, result, reporter.tools_used)
        self._maybe_setup_hint(result)

    def _run_relay(self, request: str, auto_skills: list[str] | None = None) -> None:
        from relaycli.relay import Relay
        from relaycli.render import RelayRichObserver, render_relay_summary

        self._warm_note()

        # A fresh Relay per request is by design: each request is a fresh
        # pipeline (the constructor is cheap; roles are built per run).
        relay = Relay(
            self.settings, console=self.console, project=self.project,
            permissions=self.permissions,
            skills_block=self._skills_block(extra=auto_skills or ()),
        )
        observer = RelayRichObserver(self.console)
        try:
            result = relay.run(request, observer=observer)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted — back to prompt.[/yellow]")
            return
        finally:
            observer.close()  # an error/Ctrl-C must not leave a spinner live
        render_relay_summary(self.console, result)
        self._maybe_setup_hint(result)

    def _maybe_setup_hint(self, result) -> None:
        """Re-show setup guidance after a run failed on a missing credential.

        Startup preflight can't catch this case: the model (or a relay role
        model) may have been switched mid-session with /model or /relay.
        """
        if result.stopped_reason != "error":
            return
        text = result.final_text or ""
        if "No API key configured" not in text:
            return
        problem = preflight_settings(self.settings) or text
        render_setup_panel(self.console, problem, self.settings.detected_providers())

    # -- slash commands --------------------------------------------------
    def _handle_slash(self, line: str) -> bool:
        """Handle a slash command. Returns True if the REPL should exit."""
        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("exit", "quit"):
            return True
        if cmd == "help":
            render_help(self.console)
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
            from relaycli.config_menu import run_configuration
            run_configuration(self.console)
        elif cmd == "settings":
            from relaycli.config_menu import run_settings
            run_settings(self.console)
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
            self.console.print(f"[red]Unknown command:[/red] /{cmd}  (try /help)")
        return False

    def _cmd_model(self, name: str) -> None:
        if not name:
            self.console.print(f"model: [green]{escape(self.settings.model)}[/green]")
            return
        self.settings.model = name
        self.agent.refresh_system_prompt()  # keeps token counting + prompt in sync
        self.console.print(f"model → [green]{escape(name)}[/green]")

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
        self.console.print(f"mode → [yellow]{mode}[/yellow]")
        if mode is PermissionMode.full_auto:
            self._full_auto_banner()

    def _cmd_relay(self, value: str) -> None:
        if not value:
            state = "on" if self.settings.relay_enabled else "off"
            self.console.print(f"relay: [cyan]{state}[/cyan]")
            if self.settings.relay_enabled:
                self._print_routing()
            return
        if value not in ("on", "off"):
            self.console.print("[red]Usage:[/red] /relay \\[on|off]")
            return
        self.settings.relay_enabled = value == "on"
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
        from relaycli.router import Role, resolve_model, role_enabled

        if arg:
            parts = arg.split()
            if (len(parts) == 2 and parts[0] in ("explorer", "tester", "tasks")
                    and parts[1] in ("on", "off")):
                field = "relay_split_tasks" if parts[0] == "tasks" else f"relay_{parts[0]}"
                setattr(self.settings, field, parts[1] == "on")
                self.console.print(f"agent {parts[0]} → [cyan]{parts[1]}[/cyan]")
                if parts[1] == "on" and not self.settings.relay_enabled:
                    self.console.print(
                        "[dim]note: agents run inside the relay pipeline — "
                        "/relay on to use them.[/dim]"
                    )
                return
            self.console.print(
                "[red]Usage:[/red] /agents \\[explorer|tester|tasks on|off]"
            )
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
            table.add_row(
                f"{dot} {role}",
                f"{style}{escape(model)}{end}",
                f"{style}{self._ROLE_PURPOSE[str(role)]}{end}",
            )
        self.console.print(table)
        relay_state = "on" if self.settings.relay_enabled else "off"
        split_state = "on" if self.settings.relay_split_tasks else "off"
        self.console.print(
            f"[dim]relay {relay_state} · task-split {split_state} (delegates each "
            f"plan task to a specialist) · /agents explorer|tester|tasks on|off[/dim]"
        )
        # In task-split mode the Planner can hand a step to any enabled roster
        # specialist; show which are available (configure via /config).
        if self.settings.relay_split_tasks:
            from relaycli.roster import enabled_specialists

            specialists = enabled_specialists()
            if specialists:
                self.console.print(
                    f"[dim]specialists (task-split): {escape(', '.join(specialists))}"
                    f"  · enable more with /config[/dim]"
                )

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
            self.console.print(
                f"[red]Unknown skill:[/red] {escape(name)}  (see /skills)"
            )
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
        """`/skill auto [on|off]` — toggle per-request skill auto-activation."""
        if not args:
            state = "on" if self.settings.skills_auto else "off"
            self.console.print(f"skill auto: [cyan]{state}[/cyan]")
            return
        if args[0] not in ("on", "off"):
            self.console.print("[red]Usage:[/red] /skill auto \\[on|off]")
            return
        value = args[0] == "on"
        self.settings.skills_auto = value
        # Persist as a flat config.toml key (same path relay_enabled uses).
        try:
            from relaycli.appconfig import load_app_config, save_app_config

            cfg = load_app_config()
            cfg._raw["skills_auto"] = value
            save_app_config(cfg)
        except OSError:
            pass  # session toggle still applies
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
        self.console.print(
            f"[dim]/skill <name> toggles · auto-activation {auto_state} "
            f"(/skill auto on|off) · active skills steer the agent (and the "
            f"relay coder) · drop your own .md in ~/.relaycli/skills/[/dim]"
        )

    def _cmd_mcp(self) -> None:
        from relaycli.mcp import server_status

        rows = server_status()
        if not rows:
            self.console.print(
                "[dim]no MCP connectors configured — add one with "
                "[cyan]relaycli mcp add <preset>[/cyan] (see relaycli mcp list).[/dim]"
            )
            return
        from rich.table import Table

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("server", no_wrap=True)
        table.add_column("state", no_wrap=True)
        table.add_column("tools", no_wrap=True)
        table.add_column("command")
        for row in rows:
            style = {"running": "green", "failed": "red"}.get(row["state"], "dim")
            table.add_row(
                escape(row["name"]),
                f"[{style}]{row['state']}[/{style}]",
                str(row["tools"]),
                f"[dim]{escape(row['command'])}[/dim]",
            )
        self.console.print(table)
        self.console.print(
            "[dim]tools appear to the agent as mcp_<server>_<tool> · every call "
            "asks first (like run_command) · manage with relaycli mcp[/dim]"
        )

    def _cmd_desktop(self) -> None:
        """Start (once) the desktop web UI on a daemon thread and open it."""
        from relaycli.web import _open_browser, serve_background

        if self._desktop_url is None:
            try:
                _server, self._desktop_url = serve_background(self.settings)
            except OSError as exc:
                self.console.print(f"[red]desktop failed to start:[/red] {exc}")
                return
        _open_browser(self._desktop_url)
        self.console.print(
            f"desktop → [cyan]{self._desktop_url}[/cyan]  "
            f"[dim](loopback only · shares this session's settings · "
            f"stays up until you quit)[/dim]"
        )

    def _cmd_memory(self) -> None:
        from relaycli import memory

        shown = False
        for label, path in (
            ("global", memory.GLOBAL_MEMORY),
            ("project", memory.project_memory_path(self.project.root)),
        ):
            text = memory.read_memory(path)
            if not text:
                continue
            shown = True
            self.console.print(f"[bold]{label}[/bold] [dim]{escape(str(path))}[/dim]")
            self.console.print(escape(text))
            self.console.print()
        if not shown:
            self.console.print(
                "[dim]memory is empty — the agent saves facts with the remember "
                "tool, or edit the files yourself:[/dim]"
            )
            self.console.print(f"[dim]  global   {escape(str(memory.GLOBAL_MEMORY))}[/dim]")
            self.console.print(
                f"[dim]  project  "
                f"{escape(str(memory.project_memory_path(self.project.root)))}[/dim]"
            )

    def _cmd_diff(self) -> None:
        if not (self.project.root / ".git").exists():
            self.console.print("[dim]not a git repository — no diff available.[/dim]")
            return
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.project.root), "diff", "--no-color"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.console.print(f"[red]git diff failed:[/red] {exc}")
            return
        out = proc.stdout.strip()
        if not out:
            self.console.print("[dim]no uncommitted changes.[/dim]")
            return
        self.console.print(Syntax(out, "diff", theme="ansi_dark", background_color="default"))


def run_repl(settings: Settings, console: Console | None = None) -> None:
    """Convenience entry point used by the CLI."""
    Repl(settings, console=console).run()
