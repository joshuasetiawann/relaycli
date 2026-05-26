"""Rich-based rendering helpers.

Stage 3 needs colored unified diffs (shown before every file change). The
streaming-text, activity-line, and end-of-task summary rendering are added in
Stage 5; this module is the home for all of it.
"""

from __future__ import annotations

import difflib
import re
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
               "review_exhausted": "yellow"}


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
        self.tools_used: list[str] = []
        self._status = None

    # -- working spinner ---------------------------------------------------
    def _spin(self) -> None:
        if self._status is not None or not self.console.is_terminal:
            return
        self._status = self.console.status(
            "[dim]working… (ctrl-c to interrupt)[/dim]", spinner="dots"
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
        self._spin()

    def assistant_token(self, text: str) -> None:
        # Write raw so model text containing brackets isn't parsed as markup.
        if not self._streaming:
            self._unspin()
            self.console.file.write("⏺ ")  # one bullet per assistant block
        self.console.file.write(text)
        self.console.file.flush()
        self._streaming = True

    def assistant_end(self) -> None:
        if self._streaming:
            self.console.file.write("\n")
            self.console.file.flush()
            self._streaming = False

    def tool_start(self, call: "ToolCall") -> None:
        # The tool itself shows diffs / the command line; the compact outcome
        # lines are printed at tool_end once the result is known.
        self._unspin()

    def tool_end(self, call: "ToolCall", result: "ToolResult | None") -> None:
        self.tools_used.append(call.name)
        self._unspin()
        ok = result is not None and result.ok
        dot = "[green]⏺[/green]" if ok else "[red]⏺[/red]"
        # Escape: summaries can embed model-controlled text (commands, paths).
        self.console.print(f"{dot} [bold]{escape(call.name)}[/bold]")
        outcome = "error" if result is None else (result.summary or call.name)
        self.console.print(f"  [dim]⎿  {escape(outcome)}[/dim]")


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
        console.print(f"[{style}]{escape(result.final_text)}[/{style}]")

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


_ROLE_STYLE = {"planner": "cyan", "coder": "magenta", "reviewer": "yellow"}


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
    lines = [f"[yellow]⚠ {escape(problem)}[/yellow]", ""]
    lines.append("Fix any ONE of these, then retry (or switch with /model):")
    # Anchor on our own "Set <VAR> ..." sentence and take the LAST match: the
    # problem string also embeds the model id, which is config-controlled and
    # could be crafted to smuggle a fake *_API_KEY name in front of it.
    hinted = re.findall(r"\bSet ([A-Z][A-Z0-9_]*_API_KEY)\b", problem)
    if hinted:
        lines.append(f"  • export {hinted[-1]}=...     (for the current model)")
    lines.append("  • relaycli -m ollama_chat/llama3.1   (local via Ollama, no key — needs `ollama serve`)")
    lines.append("  • add the key to ~/.relaycli/config.toml or a project .env (names in .env.example)")
    have = [name for name, ok in detected.items() if ok and name != "ollama"]
    if have:
        lines.append("")
        lines.append(f"Keys already detected: {escape(', '.join(have))} — pick one of their models with /model.")
    # Quiet chrome: the ⚠ problem line inside is already yellow — a loud
    # yellow border on top of it reads as two warnings.
    console.print(Panel("\n".join(lines), title="setup needed", title_align="left",
                        border_style="dim", expand=False))


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
        "", f"[bold {CLAUDE_ACCENT}]✻[/bold {CLAUDE_ACCENT}] Welcome to "
            f"[bold]RelayCLI[/bold] [dim]v{__version__}[/dim]!"
    )
    grid.add_row("", "")
    grid.add_row("cwd", escape(str(root)))
    grid.add_row("model", model_cell)
    grid.add_row("mode", mode_cell)
    grid.add_row("relay", relay_cell)
    grid.add_row("", "")
    grid.add_row("", '[dim]Type a request in plain words — e.g. "explain this repo".[/dim]')
    grid.add_row("", "[dim]/help commands · !cmd shell · Ctrl-D quit[/dim]")
    console.print(Panel(grid, border_style=CLAUDE_ACCENT, expand=False))


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
    table.add_row("/model \\[name]", "show or switch the model (e.g. gpt-4o-mini, ollama_chat/llama3.1)")
    table.add_row("/mode \\[m]", "permission mode: suggest | auto-edit | full-auto")
    table.add_row("/relay \\[on|off]", "toggle the Planner → Coder → Reviewer pipeline")
    table.add_row("/diff", "show uncommitted changes (git diff)")
    table.add_row("/clear", "reset the conversation")
    table.add_row("/help", "show this help  (aliases: help, ?)")
    table.add_row("/exit", "quit  (aliases: exit, quit, Ctrl-D)")
    table.add_row("!<cmd>", "run a shell command in the project root (e.g. !git status)")
    console.print(table)
    console.print(
        "[dim]Enter submits · Alt+Enter inserts a newline · "
        "Ctrl-C clears the line · Ctrl-D quits[/dim]"
    )


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
        console.print(f"[{style}]{escape(result.final_text)}[/{style}]")

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

