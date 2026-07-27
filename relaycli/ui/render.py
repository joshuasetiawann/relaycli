"""Minimal clean UI — inspired by opencode / claude code."""

from __future__ import annotations

import difflib
import re
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.table import Table

if TYPE_CHECKING:
    from pathlib import Path
    from relaycli.core.config import Settings
    from relaycli.core.llm import ToolCall, Usage
    from relaycli.tools.base import ToolResult
    from relaycli.agent.loop import AgentResult
    from relaycli.agent.pipeline import RelayResult
    from relaycli.agent.router import Role

ACCENT = "#D97757"
_STOP_STYLE = {"done": "green", "max_iterations": "yellow", "error": "red",
               "review_exhausted": "yellow", "stopped": "yellow"}


def brief_tool_error(text: str, *, limit: int = 260) -> str:
    return (re.sub(r"\s+", " ", text).strip()[:limit]).rstrip() + "…" if len(text) > limit else re.sub(r"\s+", " ", text).strip()


def make_unified_diff(old: str, new: str, path: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    if old and not old.endswith("\n"):
        old_lines[-1] += "\n"
    if new and not new.endswith("\n"):
        new_lines[-1] += "\n"
    return "".join(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=3))


def diff_stats(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def render_diff(console: Console, old: str, new: str, path: str) -> tuple[int, int]:
    diff_text = make_unified_diff(old, new, path)
    added, removed = diff_stats(old, new)
    if not diff_text:
        console.print(f"[dim](no changes to {path})[/dim]")
        return (0, 0)
    from rich.syntax import Syntax
    console.print(Syntax(diff_text, "diff", theme="ansi_dark", background_color="default"))
    console.print(f"[dim]{path}:[/dim] [green]+{added}[/green] [red]-{removed}[/red]")
    return (added, removed)


def friendly_error_text(text: str) -> str:
    raw = text or ""
    low = raw.lower()
    model = ""
    m = re.search(r"for '([^']+)'", raw)
    if m:
        model = m.group(1)
    if "unavailable for free" in low:
        return f"Model '{model}' unavailable (free tier). Use local: /model ollama_chat/<name>"
    if "notfound" in low:
        return f"Model '{model}' not found. Check id or use: /model ollama_chat/qwen2.5-coder:1.5b"
    if "ratelimit" in low or "rate-limit" in low or " 429" in f" {low}":
        return f"Rate limit{f' ({model})' if model else ''}. Try again or /model"
    if "authenticationerror" in low:
        return f"Auth failed for '{model}'. relaycli config set-key <provider> --value <key>"
    return raw


def render_local_reply(console: Console, reply) -> None:
    console.print(getattr(reply, "text", str(reply)))


class RichReporter:
    def __init__(self, console: Console) -> None:
        self.console = console
        self._buf: list[str] = []
        self.tools_used: list[str] = []
        self._status = None
        self._tool_started: dict[str, float] = {}

    def _spin(self, msg: str = "…") -> None:
        if self._status is not None or not self.console.is_terminal:
            return
        self._status = self.console.status(f"[dim]{escape(msg)}[/dim]", spinner="dots")
        self._status.start()

    def _unspin(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def close(self) -> None:
        self._unspin()

    def iteration(self, n: int) -> None:
        pass

    def model_start(self, n: int, model: str) -> None:
        self._unspin()
        self.console.print(f"[dim]→ {model.rsplit('/', 1)[-1]}[/dim]")
        self._spin()

    def model_end(self, n: int, model: str, tool_calls: int, has_text: bool, usage: "Usage") -> None:
        self._unspin()
        d = f"{tool_calls}tc" if tool_calls else ("txt" if has_text else "empty")
        self.console.print(f"[dim]← {d} · {usage.total_tokens}tok[/dim]")

    def model_error(self, n: int, model: str, error: Exception) -> None:
        self._unspin()
        self.console.print("[red]← error[/red]")

    def assistant_token(self, text: str) -> None:
        self._buf.append(text)

    def assistant_end(self) -> None:
        t = "".join(self._buf)
        self._buf.clear()
        if not t:
            return
        self._unspin()
        self.console.file.write(t)
        if not t.endswith("\n"):
            self.console.file.write("\n")
        self.console.file.flush()

    def assistant_discard(self) -> None:
        self._buf.clear()

    def tool_start(self, call: "ToolCall") -> None:
        self._unspin()
        self._tool_started[call.id] = time.perf_counter()

    def tool_end(self, call: "ToolCall", result: "ToolResult | None") -> None:
        self.tools_used.append(call.name)
        self._unspin()
        ok = result is not None and result.ok
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        secs = ""
        s = self._tool_started.pop(call.id, None)
        if s is not None:
            secs = f" {time.perf_counter() - s:.1f}s"
        self.console.print(f"  {icon} {escape(call.name)}{secs}")
        if result is not None and not result.ok and result.output:
            self.console.print(f"  [red]↳ {escape(brief_tool_error(result.output))}[/red]")


def render_task_summary(console: Console, result: "AgentResult", tools_used: list[str] | None = None) -> None:
    style = _STOP_STYLE.get(result.stopped_reason, "white")
    if result.stopped_reason == "error" and getattr(result, "final_text", ""):
        console.print(f"\n[{style}]{escape(friendly_error_text(result.final_text))}[/{style}]")
    note = ""
    if tools_used:
        from collections import Counter
        c = Counter(tools_used)
        note = " · " + ", ".join(f"{name}×{n}" if n > 1 else name for name, n in c.items())
    console.print(f"\n[{style}]■ {result.stopped_reason}[/{style}]  [dim]{result.iterations}step · {result.tool_calls}tc{note} · {result.usage.total_tokens}tok · ${result.usage.cost_usd:.6f} · {result.elapsed:.1f}s[/dim]")


_ROLE_STYLE = {"explorer": "blue", "planner": "cyan", "coder": "magenta", "tester": "green", "reviewer": "yellow"}


class RelayRichObserver:
    def __init__(self, console: Console) -> None:
        self.console = console
        self.reporters: list[tuple[str, RichReporter]] = []

    def role_start(self, role: "Role", model: str, cycle: int) -> None:
        s = _ROLE_STYLE.get(str(role), "white")
        c = f" · cycle {cycle + 1}" if cycle else ""
        self.console.print(f"\n[bold {s}]◆ {role}[/bold {s}] [dim]{escape(model)}{c}[/dim]")

    def reporter_for(self, role: "Role") -> RichReporter:
        r = RichReporter(self.console)
        self.reporters.append((str(role), r))
        return r

    def close(self) -> None:
        for _, r in self.reporters:
            r.close()


def render_setup_panel(console: Console, problem: str, detected: dict[str, bool]) -> None:
    from relaycli.core.llm import best_ollama_model, ollama_host_label
    lm = best_ollama_model()
    console.print(f"[yellow]⚠ {escape(problem)}[/yellow]")
    if lm:
        console.print(f"  relaycli init  (ollama at {escape(ollama_host_label())}, can use {escape(lm)})")
    else:
        console.print("  relaycli init  (guided setup)")
    console.print("  relaycli config set-key <provider> --env <VAR>")


def render_slash_guide(console: Console) -> None:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="cyan", no_wrap=True)
    t.add_column()
    for cmd, desc in [("/setup", "guided setup"), ("/model", "switch model"), ("/mode", "permission mode"),
                       ("/agents", "relay roles"), ("/doctor", "health check"), ("/desktop", "browser UI")]:
        t.add_row(cmd, desc)
    console.print(t)


def short_model_name(model: str) -> str:
    return model.rsplit("/", 1)[-1] or model


def render_welcome(console: Console, settings: "Settings", root: "Path", key_status: str | None) -> None:
    from relaycli import __version__
    s = short_model_name(settings.model)
    parts = [f"RelayCLI v{__version__}", f"model {s}", f"mode {settings.permission_mode}"]
    if settings.relay_enabled:
        parts.append("relay on")
    console.print(f"[dim]{' · '.join(parts)}[/dim]")
    render_model_warning(console, settings)


def render_model_warning(console: Console, settings: "Settings") -> None:
    from relaycli.core.llm import tool_capability_warning
    w = tool_capability_warning(settings.model)
    if w:
        console.print(f"[yellow]⚠ {escape(w)}[/yellow]")


def render_status_line(console: Console, settings: "Settings", root: "Path", key_status: str | None = None) -> None:
    console.print(f"[dim]model {short_model_name(settings.model)} · mode {settings.permission_mode}[/dim]")


def render_help(console: Console) -> None:
    t = Table(box=None, padding=(0, 2))
    t.add_column("cmd", style="cyan", no_wrap=True)
    t.add_column("action")
    for cmd, desc in [("/model [name]", "show/switch model"), ("/mode [m]", "suggest/auto-edit/full-auto"),
                       ("/relay [on|off]", "toggle pipeline"), ("/agents [r on|off]", "show/toggle agents"),
                       ("/doctor", "health check"), ("/desktop", "browser UI"),
                       ("!<cmd>", "run shell command"), ("exit", "quit")]:
        t.add_row(cmd, desc)
    console.print(t)


def render_routing_banner(console: Console, settings: "Settings") -> None:
    from relaycli.agent.router import routing_table
    console.print(f"[cyan]relay on[/cyan] [dim]{' · '.join(f'{r}:{m}' for r, m in routing_table(settings).items())}[/dim]")


def render_relay_summary(console: Console, result: "RelayResult") -> None:
    style = _STOP_STYLE.get(result.stopped_reason, "white")
    if result.stopped_reason in ("error", "max_iterations") and result.final_text:
        console.print(f"\n[{style}]{escape(friendly_error_text(result.final_text))}[/{style}]")
    for note in result.notes:
        console.print(f"[yellow]⚠ {escape(note)}[/yellow]")
    for run in result.role_runs:
        r = run.result
        console.print(f"[dim]{str(run.role):<9} {escape(run.model)} · {r.iterations}step · {r.usage.total_tokens}tok · ${r.usage.cost_usd:.6f}[/dim]")
    v = f" · verdict {result.verdict}" if result.verdict else ""
    console.print(f"[{style}]■ {result.stopped_reason}[/{style}]  [dim]{result.cycles + 1}cycle{v} · {result.usage.total_tokens}tok · ${result.usage.cost_usd:.6f} · {result.elapsed:.1f}s[/dim]")


def _compact(a: str, limit: int = 80) -> str:
    o = " ".join((a or "").split())
    return o if len(o) <= limit else o[:limit - 1] + "…"
