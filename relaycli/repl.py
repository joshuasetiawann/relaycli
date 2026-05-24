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
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from relaycli.agent import Agent
from relaycli.config import CONFIG_DIR, PermissionMode, Settings, ensure_config_dir
from relaycli.context import ProjectContext
from relaycli.permissions import PermissionManager
from relaycli.render import RichReporter, render_task_summary

_HELP = """[bold]Slash commands[/bold]
  [cyan]/model[/cyan] <name>                switch the model (e.g. gpt-4o-mini, ollama_chat/llama3.1)
  [cyan]/mode[/cyan]  <suggest|auto-edit|full-auto>   switch permission mode
  [cyan]/relay[/cyan] \\[on|off]             toggle the Planner → Coder → Reviewer pipeline
  [cyan]/diff[/cyan]                        show changes in the working tree (git diff)
  [cyan]/clear[/cyan]                       reset the conversation
  [cyan]/help[/cyan]                        show this help
  [cyan]/exit[/cyan]                        quit
[dim]Enter submits · Alt+Enter inserts a newline · Ctrl-D exits[/dim]"""


class Repl:
    """A persistent interactive RelayCLI session."""

    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()
        self.project = ProjectContext(Path.cwd())
        self.permissions = PermissionManager(settings.permission_mode, console=self.console)
        self.agent = Agent(
            settings,
            console=self.console,
            project=self.project,
            permissions=self.permissions,
        )

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

            if line.startswith("/"):
                if self._handle_slash(line):
                    break
                continue

            self._run_agent(line)

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
            event.current_buffer.validate_and_handle()

        @kb.add("escape", "enter")
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        return PromptSession(history=history, key_bindings=kb, multiline=True)

    def _prompt_text(self) -> str:
        return f"relaycli ({self.settings.permission_mode})› "

    def _print_banner(self) -> None:
        self.console.print(
            f"[bold cyan]RelayCLI[/bold cyan]  "
            f"[dim]cwd[/dim] {self.project.root}  "
            f"[dim]model[/dim] [green]{escape(self.settings.model)}[/green]  "
            f"[dim]mode[/dim] [yellow]{self.settings.permission_mode}[/yellow]"
        )
        if self.settings.relay_enabled:
            self._print_routing()
        if self.settings.permission_mode is PermissionMode.full_auto:
            self._full_auto_banner()
        self.console.print("[dim]Type a request, or /help for commands.[/dim]\n")

    def _full_auto_banner(self) -> None:
        self.console.print(
            "[bold yellow]⚠ full-auto:[/bold yellow] edits and commands run "
            "without asking."
        )

    def _print_routing(self) -> None:
        from relaycli.render import render_routing_banner

        render_routing_banner(self.console, self.settings)

    # -- running a task --------------------------------------------------
    def _run_agent(self, request: str) -> None:
        if self.settings.relay_enabled:
            self._run_relay(request)
            return
        reporter = RichReporter(self.console)
        try:
            result = self.agent.run(request, reporter=reporter)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted — back to prompt.[/yellow]")
            return
        render_task_summary(self.console, result, reporter.tools_used)

    def _run_relay(self, request: str) -> None:
        from relaycli.relay import Relay
        from relaycli.render import RelayRichObserver, render_relay_summary

        # A fresh Relay per request is by design: each request is a fresh
        # pipeline (the constructor is cheap; roles are built per run).
        relay = Relay(
            self.settings, console=self.console, project=self.project,
            permissions=self.permissions,
        )
        observer = RelayRichObserver(self.console)
        try:
            result = relay.run(request, observer=observer)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted — back to prompt.[/yellow]")
            return
        render_relay_summary(self.console, result)

    # -- slash commands --------------------------------------------------
    def _handle_slash(self, line: str) -> bool:
        """Handle a slash command. Returns True if the REPL should exit."""
        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("exit", "quit"):
            return True
        if cmd == "help":
            self.console.print(_HELP)
        elif cmd == "model":
            self._cmd_model(arg)
        elif cmd == "mode":
            self._cmd_mode(arg)
        elif cmd == "relay":
            self._cmd_relay(arg)
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
            self.console.print(f"mode: [yellow]{self.settings.permission_mode}[/yellow]")
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
