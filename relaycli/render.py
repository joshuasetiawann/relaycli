"""Rich-based rendering helpers.

Stage 3 needs colored unified diffs (shown before every file change). The
streaming-text, activity-line, and end-of-task summary rendering are added in
Stage 5; this module is the home for all of it.
"""

from __future__ import annotations

import difflib
import re
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

if TYPE_CHECKING:  # avoid an import cycle (agent -> tools -> render -> agent)
    from pathlib import Path

    from relaycli.agent import AgentResult
    from relaycli.config import Settings
    from relaycli.llm import ToolCall
    from relaycli.relay import RelayResult
    from relaycli.router import Role
    from relaycli.tools.base import ToolResult


def brief_tool_error(text: str, *, limit: int = 260) -> str:
    """One-line tool error detail for human logs."""

    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def make_unified_diff(old: str, new: str, path: str) -> str:
    """Return a unified diff string for ``old`` -> ``new`` (empty if identical)."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    if old and not old.endswith("\n"):
        old_lines[-1] += "\n"
    if new and not new.endswith("\n"):
        new_lines[-1] += "\n"
    diff = difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=3
    )
    return "".join(diff)


def diff_stats(old: str, new: str) -> tuple[int, int]:
    """Return (added_lines, removed_lines) between ``old`` and ``new``."""
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def render_diff(console: Console, old: str, new: str, path: str) -> tuple[int, int]:
    """Print a colored unified diff and return (added, removed) line counts."""
    diff_text = make_unified_diff(old, new, path)
    added, removed = diff_stats(old, new)
    if not diff_text:
        console.print(f"[dim](no changes to {path})[/dim]")
        return (0, 0)
    syntax = Syntax(diff_text, "diff", theme="ansi_dark", background_color="default")
    console.print(syntax)
    console.print(f"[dim]{path}:[/dim] [green]+{added}[/green] [red]-{removed}[/red]")
    return (added, removed)


# Claude Code's brand accent — used for the welcome chrome and the prompt.
CLAUDE_ACCENT = "#D97757"

_STOP_STYLE = {"done": "green", "max_iterations": "yellow", "error": "red",
               "review_exhausted": "yellow", "stopped": "yellow"}
_TOOL_ACTIVITY = {
    "list_dir": "listing directory",
    "find_files": "searching files",
    "search": "searching code",
    "read_file": "reading file",
    "write_file": "writing file",
    "edit_file": "editing file",
    "create_folder": "creating folder",
    "run_command": "running command",
    "run_background": "starting background command",
    "check_process": "checking background command",
    "stop_process": "stopping background command",
    "remember": "saving memory",
}


def friendly_error_text(text: str) -> str:
    """Compact noisy provider errors into output a human can act on."""

    raw = text or ""
    low = raw.lower()
    rate_limited = (
        "ratelimit" in low
        or "rate-limit" in low
        or "rate limited" in low
        or "rate-limited" in low
        or " 429" in f" {low}"
    )
    if "llm error" in low and rate_limited:
        model = ""
        m = re.search(r"for '([^']+)'", raw)
        if m:
            model = f" ({m.group(1)})"
        return (
            f"LLM rate limit{model}: model/provider sedang penuh. "
            "Coba lagi sebentar lagi, ganti model lewat /model, atau pasang "
            "key provider sendiri dengan `relaycli config set-key <provider>`."
        )
    return raw


def render_local_reply(console: Console, reply) -> None:
    """Render a local guide reply without starting the LLM."""

    text = getattr(reply, "text", str(reply))
    console.print(Panel(
        escape(text),
        title="guide",
        title_align="left",
        border_style=CLAUDE_ACCENT,
        expand=False,
    ))


class RichReporter:
    """Rich presentation of an agent run, in Claude Code's visual language.

    Implements the duck-typed Reporter protocol used by :meth:`Agent.run`
    (assistant_token / assistant_end / tool_start / tool_end / iteration). The
    agent and tool logic are untouched; this only renders.

    A dim "working…" spinner runs while waiting on the model: started at each
    loop iteration (right before the LLM call) and stopped before any output —
    first streamed token or first tool event — so it is never live while a
    tool executes (tools print diffs and permission prompts). Terminal-only;
    non-tty consoles get plain output. Callers should ``close()`` in a
    ``finally`` so an LLM error or Ctrl-C never leaves the spinner running.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._streaming = False
        self._buf: list[str] = []
        self.tools_used: list[str] = []
        self._status = None
        self._tool_started: dict[str, float] = {}

    # -- working spinner ---------------------------------------------------
    def _spin(self, message: str = "working… (ctrl-c to interrupt)") -> None:
        if self._status is not None or not self.console.is_terminal:
            return
        self._status = self.console.status(
            f"[dim]{escape(message)}[/dim]", spinner="dots"
        )
        self._status.start()

    def _unspin(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def close(self) -> None:
        """Idempotent cleanup: make sure no live spinner outlives the run."""
        self._unspin()

    # -- reporter protocol ---------------------------------------------------
    def iteration(self, n: int) -> None:
        # model_start prints the visible log line and starts the spinner.
        return

    def model_start(self, n: int, model: str) -> None:
        self._unspin()
        self.console.print(f"[dim]→ model[/dim] step {n} · [bold]{escape(model)}[/bold]")
        if model.startswith(("ollama_chat/", "ollama/")):
            self.console.print(
                "[dim]  Ollama local is generating now · `ollama ps` should show "
                "`100% GPU` when acceleration is active.[/dim]"
            )
        self._spin(f"waiting for {model}… (ctrl-c to interrupt)")

    def model_end(
        self, n: int, model: str, tool_calls: int, has_text: bool, usage
    ) -> None:
        self._unspin()
        if tool_calls:
            detail = f"{tool_calls} tool call{'s' if tool_calls != 1 else ''}"
        else:
            detail = "answer" if has_text else "empty response"
        self.console.print(f"[dim]← model[/dim] {detail} · {usage.total_tokens} tok")

    def model_error(self, n: int, model: str, error: Exception) -> None:
        self._unspin()
        self.console.print("[red]← model error[/red]")

    def assistant_token(self, text: str) -> None:
        self._buf.append(text)

    def assistant_end(self) -> None:
        text = "".join(self._buf)
        self._buf.clear()
        if not text:
            return
        from relaycli.agent import fake_tool_call_text

        if fake_tool_call_text(text):
            self._streaming = False
            return
        self._unspin()
        self.console.file.write("⏺ ")
        self.console.file.write(text)
        if not text.endswith("\n"):
            self.console.file.write("\n")
        self.console.file.flush()
        self._streaming = False

    def assistant_discard(self) -> None:
        self._buf.clear()
        self._streaming = False

    def tool_start(self, call: "ToolCall") -> None:
        self._unspin()
        self._tool_started[call.id] = time.perf_counter()
        from relaycli.agent import _compact

        args = _compact(call.arguments, limit=120)
        suffix = f" [dim]{escape(args)}[/dim]" if args and args != "{}" else ""
        action = _TOOL_ACTIVITY.get(call.name, "using tool")
        self.console.print(
            f"[dim]→ tool[/dim] [bold]{escape(call.name)}[/bold] "
            f"[dim]{escape(action)}[/dim]{suffix}"
        )

    def tool_end(self, call: "ToolCall", result: "ToolResult | None") -> None:
        self.tools_used.append(call.name)
        self._unspin()
        ok = result is not None and result.ok
        dot = "[green]⏺[/green]" if ok else "[red]⏺[/red]"
        # Escape: summaries can embed model-controlled text (commands, paths).
        self.console.print(f"{dot} [bold]{escape(call.name)}[/bold]")
        outcome = "error" if result is None else (result.summary or call.name)
        elapsed = ""
        started = self._tool_started.pop(call.id, None)
        if started is not None:
            elapsed = f" · {time.perf_counter() - started:.1f}s"
        self.console.print(f"  [dim]⎿  {escape(outcome)}{elapsed}[/dim]")
        if result is not None and not result.ok and result.output:
            self.console.print(f"  [red]↳ {escape(brief_tool_error(result.output))}[/red]")


def render_task_summary(
    console: Console, result: "AgentResult", tools_used: list[str] | None = None
) -> None:
    """Print a clean end-of-task summary line."""
    style = _STOP_STYLE.get(result.stopped_reason, "white")

    # On "done" the final text was already streamed token-by-token. On error /
    # max_iterations it was only constructed, never shown — print it or the
    # user gets a silent failure.
    if result.stopped_reason != "done" and getattr(result, "final_text", ""):
        console.print()
        text = friendly_error_text(result.final_text)
        console.print(f"[{style}]{escape(text)}[/{style}]")

    tools_note = ""
    if tools_used:
        from collections import Counter

        counts = Counter(tools_used)
        tools_note = " · " + ", ".join(f"{name}×{n}" if n > 1 else name for name, n in counts.items())

    console.print()
    console.print(
        f"[{style}]■ {result.stopped_reason}[/{style}]  "
        f"[dim]{result.iterations} steps · {result.tool_calls} tool calls"
        f"{tools_note} · {result.usage.total_tokens} tokens · "
        f"${result.usage.cost_usd:.6f} · {result.elapsed:.1f}s[/dim]"
    )


_ROLE_STYLE = {"explorer": "blue", "planner": "cyan", "coder": "magenta",
               "tester": "green", "reviewer": "yellow"}


class RelayRichObserver:
    """Rich presentation of a relay run: role banners + a reporter per role.

    Implements the duck-typed RelayObserver protocol used by
    :meth:`relaycli.relay.Relay.run` (role_start / reporter_for).
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.reporters: list[tuple[str, RichReporter]] = []

    def role_start(self, role: "Role", model: str, cycle: int) -> None:
        style = _ROLE_STYLE.get(str(role), "white")
        cycle_note = f" · cycle {cycle + 1}" if cycle else ""
        self.console.print(
            f"\n[bold {style}]◆ {role}[/bold {style}] [dim]{escape(model)}{cycle_note}[/dim]"
        )

    def reporter_for(self, role: "Role") -> RichReporter:
        reporter = RichReporter(self.console)
        self.reporters.append((str(role), reporter))
        return reporter

    def close(self) -> None:
        """Stop any spinner a role's reporter may have left live (idempotent)."""
        for _, reporter in self.reporters:
            reporter.close()


def render_setup_panel(console: Console, problem: str, detected: dict[str, bool]) -> None:
    """Actionable guidance when the configured model has no usable credential."""
    from relaycli.config import get_settings
    from relaycli.llm import best_ollama_model, ollama_host_label

    settings = get_settings()
    local_model = best_ollama_model(settings)
    lines = [f"[yellow]⚠ {escape(problem)}[/yellow]", ""]
    lines.append("Fastest fixes:")
    if local_model:
        lines.append(
            f"  • relaycli init     (detected Ollama at {escape(ollama_host_label(settings))}; "
            f"can use {escape(local_model)})"
        )
    else:
        lines.append("  • relaycli init     (guided setup for Ollama, OpenRouter, or API keys)")
    lines.append("  • relaycli config set-key <provider> --env <VAR>  (store a key reference)")
    lines.append("")
    lines.append("Manual fixes:")
    # Anchor on our own "Set <VAR> ..." sentence and take the LAST match: the
    # problem string also embeds the model id, which is config-controlled and
    # could be crafted to smuggle a fake *_API_KEY name in front of it.
    hinted = re.findall(r"\bSet ([A-Z][A-Z0-9_]*_API_KEY)\b", problem)
    if hinted:
        lines.append(f"  • export {hinted[-1]}=...     (for the current model)")
    lines.append("  • relaycli -m ollama_chat/llama3.1   (local via Ollama, no key; needs `ollama serve`)")
    lines.append("  • add the key to ~/.relaycli/config.toml or a project .env (names in .env.example)")
    have = [name for name, ok in detected.items() if ok and name != "ollama"]
    if have:
        lines.append("")
        lines.append(f"Keys already detected: {escape(', '.join(have))} — pick one of their models with /model.")
    # Quiet chrome: the ⚠ problem line inside is already yellow — a loud
    # yellow border on top of it reads as two warnings.
    console.print(Panel("\n".join(lines), title="setup needed", title_align="left",
                        border_style="dim", expand=False))


def render_slash_guide(console: Console) -> None:
    """Compact command palette shown by `/` and in the welcome flow."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("/setup", "guided setup: model, keys, Ollama/n8n/web/postgres")
    table.add_row("/model", "switch the model")
    table.add_row("/mode", "suggest | auto-edit | full-auto")
    table.add_row("/agents", "relay roles and task-split specialists")
    table.add_row("/services", "optional Docker services")
    table.add_row("/doctor", "health check")
    table.add_row("/desktop", "browser UI")
    console.print(Panel(
        table,
        title="press / for commands",
        title_align="left",
        border_style="dim",
        expand=False,
    ))


def short_model_name(model: str) -> str:
    """Compact display name: the last path segment of a LiteLLM model id."""
    return model.rsplit("/", 1)[-1] or model


# key_status (relaycli.llm.key_status) -> how the banner shows it.
_KEY_NOTE = {
    "detected": "[green]key detected[/green]",
    "missing": "[bold yellow]key missing ⚠[/bold yellow]",
    "not needed": "[blue]no key needed[/blue]",
}

# PermissionMode value -> what it means for the user, in one clause.
_MODE_MEANING = {
    "suggest": "asks before every edit & command",
    "auto-edit": "applies edits, asks before commands",
    "full-auto": "runs edits & commands without asking",
}


def render_welcome(
    console: Console, settings: "Settings", root: "Path", key_status: str | None
) -> None:
    """The REPL welcome panel: version, cwd, model/key, mode, relay, hints.

    ``key_status`` comes from :func:`relaycli.llm.key_status`; None means
    "unknown provider" and the banner makes no claim about credentials.
    """
    from relaycli import __version__

    model_cell = f"[green]{escape(settings.model)}[/green]"
    note = _KEY_NOTE.get(key_status or "")
    if note:
        model_cell += f"  {note}"

    mode = str(settings.permission_mode)
    mode_cell = f"[yellow]{mode}[/yellow]"
    meaning = _MODE_MEANING.get(mode)
    if meaning:
        mode_cell += f" [dim]— {meaning}[/dim]"

    if settings.relay_enabled:
        from relaycli.router import routing_table

        routes = " · ".join(
            f"{role}:{escape(short_model_name(m))}"
            for role, m in routing_table(settings).items()
        )
        relay_cell = f"[cyan]on[/cyan]  [dim]{routes}[/dim]"
    else:
        relay_cell = "[dim]off — /relay on for planner → coder → reviewer[/dim]"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    # fold: long values (deep cwd paths) wrap instead of being ellipsized —
    # truncating the very info the banner exists to show helps no one.
    grid.add_column(overflow="fold")
    grid.add_row(
        "", f"[bold {CLAUDE_ACCENT}]✻[/bold {CLAUDE_ACCENT}] "
            f"[bold]RelayCLI[/bold] [dim]v{__version__}[/dim] "
            "[dim]agent workspace[/dim]"
    )
    grid.add_row("", "[dim]plan, edit, run, review - from this project root[/dim]")
    grid.add_row("cwd", escape(str(root)))
    grid.add_row("model", model_cell)
    grid.add_row("mode", mode_cell)
    grid.add_row("relay", relay_cell)
    grid.add_row("", "")
    grid.add_row("", '[dim]Try: "explain this repo" · "fix failing tests" · "build a small UI"[/dim]')
    grid.add_row("", "[dim]/ commands · !cmd shell · Ctrl-D quit[/dim]")

    from pathlib import Path as _Path

    if root in (_Path.home(), _Path(_Path.home().anchor)):
        grid.add_row("", "")
        grid.add_row(
            "", "[yellow]⚠ This is your whole home directory — the agent can read "
                "and change anything under it.[/yellow]\n[dim]Better: cd into a "
                "project folder (e.g. mkdir ~/proyek/app && cd ~/proyek/app) and "
                "run relaycli there.[/dim]"
        )
    console.print(Panel(
        grid,
        title="[bold]RelayCLI[/bold]",
        title_align="left",
        subtitle="[dim]type / for commands[/dim]",
        subtitle_align="right",
        border_style=CLAUDE_ACCENT,
        expand=False,
    ))
    render_model_warning(console, settings)


def render_model_warning(console: Console, settings: "Settings") -> None:
    """Warn when a chosen local model may not drive tools reliably."""
    from relaycli.llm import tool_capability_warning

    warning = tool_capability_warning(settings.model)
    if warning:
        console.print(f"[yellow]⚠ {escape(warning)}[/yellow]")


def render_status_line(
    console: Console, settings: "Settings", root: "Path", key_status: str | None = None
) -> None:
    """One-line session status (the one-shot header; same fields as the banner)."""
    parts = [f"[dim]model[/dim] [green]{escape(settings.model)}[/green]"]
    note = _KEY_NOTE.get(key_status or "")
    if note:
        parts.append(note)
    parts.append(f"[dim]mode[/dim] [yellow]{settings.permission_mode}[/yellow]")
    parts.append(f"[dim]cwd[/dim] {escape(str(root))}")
    console.print("  ".join(parts))


def render_help(console: Console) -> None:
    """The REPL /help screen: every accepted input form, aligned."""
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("input", style="cyan", no_wrap=True)
    table.add_column("action")
    table.add_row("<plain text>", "send a request to the agent")
    table.add_row("/", "show the command palette")
    table.add_row("/setup", "guided first-run setup (alias: /init)")
    table.add_row("/init", "alias of /setup")
    table.add_row("/model \\[name]", "show or switch the model (e.g. gpt-4o-mini, ollama_chat/llama3.1)")
    table.add_row("/mode \\[m]", "permission mode: suggest | auto-edit | full-auto")
    table.add_row("/relay \\[on|off]", "toggle the Planner → Coder → Reviewer pipeline")
    table.add_row("/agents \\[r on|off]", "show relay agents; toggle explorer/tester")
    table.add_row("/services \\[start names]", "show/start optional services: ollama, web, postgres, n8n")
    table.add_row("/doctor", "run a local health check")
    table.add_row("/skill \\[name]", "toggle a skill for this session (tdd, debug, ponytail, …)")
    table.add_row("/skill auto \\[on|off]", "toggle per-request skill auto-activation")
    table.add_row("/skills", "list available skills and where they come from")
    table.add_row("/memory", "show long-term memory (global + project)")
    table.add_row("/mcp", "show MCP connectors and their tools")
    table.add_row("/desktop", "open the desktop web UI in your browser")
    table.add_row("/config", "roles, per-role models & provider keys (persistent config)")
    table.add_row("/settings", "general preferences: mode, theme, context limit")
    table.add_row("/diff", "show uncommitted changes (git diff)")
    table.add_row("/clear", "reset the conversation")
    table.add_row("/help", "show this help  (aliases: help, ?)")
    table.add_row("/exit", "quit  (aliases: exit, quit, Ctrl-D)")
    table.add_row("!<cmd>", "run a shell command in the project root (e.g. !git status)")
    table.caption = (
        "Enter submits · Alt+Enter inserts a newline · Ctrl-R searches history · "
        "Ctrl-C clears the line · Ctrl-D quits"
    )
    console.print(Panel(
        table,
        title="[bold]Command palette[/bold]",
        title_align="left",
        border_style=CLAUDE_ACCENT,
        expand=False,
    ))


def render_routing_banner(console: Console, settings: "Settings") -> None:
    """Print the role → model routing line (model ids are untrusted: escape)."""
    from relaycli.router import routing_table

    routes = " · ".join(f"{role}:{m}" for role, m in routing_table(settings).items())
    console.print(f"[dim]relay[/dim] [cyan]on[/cyan]  [dim]{escape(routes)}[/dim]")


def render_relay_summary(console: Console, result: "RelayResult") -> None:
    """Print the end-of-relay summary: notes, per-role lines, and totals."""
    style = _STOP_STYLE.get(result.stopped_reason, "white")

    # error/max_iterations texts are constructed, never streamed. Anything
    # else (done, review_exhausted) was already streamed live by its role —
    # re-printing would duplicate the coder's report.
    if result.stopped_reason in ("error", "max_iterations") and result.final_text:
        console.print()
        text = friendly_error_text(result.final_text)
        console.print(f"[{style}]{escape(text)}[/{style}]")

    for note in result.notes:
        console.print(f"[yellow]⚠ {escape(note)}[/yellow]")

    console.print()
    for run in result.role_runs:
        r = run.result
        role = str(run.role)
        console.print(
            f"[dim]{role:<9} {escape(run.model)} · {r.iterations} steps · "
            f"{r.usage.total_tokens} tokens · ${r.usage.cost_usd:.6f}[/dim]"
        )
    verdict_note = f" · verdict {result.verdict}" if result.verdict else ""
    console.print(
        f"[{style}]■ {result.stopped_reason}[/{style}]  "
        f"[dim]{result.cycles + 1} cycle(s){verdict_note} · "
        f"{result.usage.total_tokens} tokens · ${result.usage.cost_usd:.6f} · "
        f"{result.elapsed:.1f}s[/dim]"
    )
