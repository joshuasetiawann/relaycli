"""Reporter interface — presentation hooks for the agent loop."""

from __future__ import annotations

from relaycli.core.llm import LLMResponse, ToolCall, Usage
from relaycli.tools.base import ToolResult


class Reporter:
    def model_start(self, n: int, model: str) -> None: ...
    def model_end(self, n: int, model: str, tool_calls: int, has_text: bool, usage: Usage) -> None: ...
    def model_error(self, n: int, model: str, error: Exception) -> None: ...
    def assistant_token(self, text: str) -> None: ...
    def assistant_end(self) -> None: ...
    def assistant_discard(self) -> None: ...
    def tool_start(self, call: ToolCall) -> None: ...
    def tool_end(self, call: ToolCall, result: ToolResult | None) -> None: ...
    def iteration(self, n: int) -> None: ...


class PlainReporter(Reporter):
    def __init__(self, console) -> None:
        self.console = console
        self._buf: list[str] = []
        self.tools_used: list[str] = []

    def model_start(self, n: int, model: str) -> None:
        from rich.markup import escape
        self.console.print(f"[dim]→ step {n} · {escape(model)}[/dim]")

    def model_end(self, n: int, model: str, tool_calls: int, has_text: bool, usage: Usage) -> None:
        kind = f"{tool_calls} call{'s' if tool_calls != 1 else ''}" if tool_calls else "answer"
        self.console.print(f"[dim]← {kind} · {usage.total_tokens} tok[/dim]")

    def model_error(self, n: int, model: str, error: Exception) -> None:
        self.console.print("[red]← model error[/red]")

    def assistant_token(self, text: str) -> None:
        self._buf.append(text)

    def assistant_end(self) -> None:
        text = "".join(self._buf)
        self._buf.clear()
        self.console.file.write(text)
        if text and not text.endswith("\n"):
            self.console.file.write("\n")
        self.console.file.flush()

    def assistant_discard(self) -> None:
        self._buf.clear()

    def tool_start(self, call: ToolCall) -> None:
        from rich.markup import escape
        compact = " ".join((call.arguments or "{}").split())
        if len(compact) > 80:
            compact = compact[:79] + "…"
        self.console.print(f"[magenta]●[/magenta] {escape(call.name)} [dim]{escape(compact)}[/dim]")
        self.tools_used.append(call.name)

    def tool_end(self, call: ToolCall, result: ToolResult | None) -> None:
        if result is None:
            self.console.print("  [red]tool error[/red]")
            return
        style = "green" if result.ok else "red"
        label = result.summary or ("done" if result.ok else "failed")
        self.console.print(f"  [{style}]{label}[/{style}]")


class RichReporter(PlainReporter):
    pass
