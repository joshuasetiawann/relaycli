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
from relaycli.llm import key_status, preflight_settings
from relaycli.permissions import PermissionManager
from relaycli.render import (
    RichReporter,
    render_help,
    render_setup_panel,
    render_task_summary,
    render_welcome,
    short_model_name,
)


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
            event.current_buffer.validate_and_handle()

        @kb.add("escape", "enter")
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        return PromptSession(history=history, key_bindings=kb, multiline=True)

    def _prompt_text(self) -> str:
        parts = [short_model_name(self.settings.model), str(self.settings.permission_mode)]
        if self.settings.relay_enabled:
            parts.append("relay")
        return " · ".join(parts) + " › "

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
        self._maybe_setup_hint(result)

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
