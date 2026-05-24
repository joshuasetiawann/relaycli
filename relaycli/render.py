"""Rich-based rendering helpers.

Stage 3 needs colored unified diffs (shown before every file change). The
streaming-text, activity-line, and end-of-task summary rendering are added in
Stage 5; this module is the home for all of it.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

if TYPE_CHECKING:  # avoid an import cycle (agent -> tools -> render -> agent)
    from relaycli.agent import AgentResult
    from relaycli.llm import ToolCall
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


_STOP_STYLE = {"done": "green", "max_iterations": "yellow", "error": "red"}


class RichReporter:
    """Rich presentation of an agent run.

    Implements the duck-typed Reporter protocol used by :meth:`Agent.run`
    (assistant_token / assistant_end / tool_start / tool_end / iteration). The
    agent and tool logic are untouched; this only renders.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._streaming = False
        self.tools_used: list[str] = []

    def assistant_token(self, text: str) -> None:
        # Write raw so model text containing brackets isn't parsed as markup.
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
        # line is printed at tool_end once the result is known.
        pass

    def tool_end(self, call: "ToolCall", result: "ToolResult | None") -> None:
        self.tools_used.append(call.name)
        if result is None:
            self.console.print(f"[red]●[/red] {escape(call.name)} [red](error)[/red]")
            return
        icon = "[green]●[/green]" if result.ok else "[red]●[/red]"
        # Escape: summaries can embed model-controlled text (commands, paths).
        self.console.print(f"{icon} {escape(result.summary or call.name)}")

    def iteration(self, n: int) -> None:
        pass


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

