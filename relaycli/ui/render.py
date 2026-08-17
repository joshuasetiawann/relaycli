"""Rich-based rendering helpers.

Stage 3 needs colored unified diffs (shown before every file change). The
streaming-text, activity-line, and end-of-task summary rendering are added in
Stage 5; this module is the home for all of it.
"""

from __future__ import annotations

import difflib
import re
import textwrap
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from relaycli.ui import theme

if TYPE_CHECKING:  # avoid an import cycle (agent -> tools -> render -> agent)
    from pathlib import Path

    from relaycli.agent import AgentResult
    from relaycli.core.config import Settings
    from relaycli.core.llm import ToolCall
    from relaycli.relay import RelayResult
    from relaycli.agent.router import Role
    from relaycli.agent.scheduler import SchedulerResult
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


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def structured_diff(old: str, new: str, path: str) -> "FileDiff":
    """Like make_unified_diff, but returns structured per-hunk data (files,
    hunks, +/- counts) instead of printed text — so a UI can render a real
    diff review without re-parsing ToolResult.output."""
    from relaycli.tools.base import DiffHunk, FileDiff

    diff_text = make_unified_diff(old, new, path)
    added, removed = diff_stats(old, new)

    hunks: list[DiffHunk] = []
    current: list[str] = []
    header: tuple[int, int, int, int] | None = None

    def flush() -> None:
        if header is None:
            return
        h_added = sum(1 for l in current if l.startswith("+") and not l.startswith("+++"))
        h_removed = sum(1 for l in current if l.startswith("-") and not l.startswith("---"))
        hunks.append(DiffHunk(*header, added=h_added, removed=h_removed, text="".join(current)))

    for line in diff_text.splitlines(keepends=True):
        match = _HUNK_HEADER_RE.match(line)
        if match:
            flush()
            current = [line]
            header = (
                int(match.group(1)), int(match.group(2) or 1),
                int(match.group(3)), int(match.group(4) or 1),
            )
            continue
        if header is not None:
            current.append(line)
    flush()

    return FileDiff(
        path=path, added=added, removed=removed, hunks=hunks,
        is_new=old == "" and new != "",
        is_deleted=old != "" and new == "",
    )


def render_diff(console: Console, old: str, new: str, path: str) -> tuple[int, int]:
    """Print a colored unified diff and return (added, removed) line counts."""
    mode = session_color_mode()
    diff_text = make_unified_diff(old, new, path)
    added, removed = diff_stats(old, new)
    if not diff_text:
        _say(console, _tinted(mode, "muted", escape(f"(no changes to {path})")))
        return (0, 0)
    syntax = Syntax(diff_text, "diff", theme="ansi_dark", background_color="default")
    console.print(syntax)
    # §2's diff markers: `+` in success, `−` (U+2212, not a hyphen) in
    # danger, gutter only — never the whole line.
    _say(console, _tinted(mode, "text", escape(path))
         + "  " + _tinted(mode, "success", f"+{added}")
         + " " + _tinted(mode, "danger", f"−{removed}"))
    return (added, removed)


# Why a run ended -> the §2 task state it corresponds to. The glyph and the
# hue both come from that state, so an ended run reads exactly like a
# settled lane rather than inventing a third vocabulary (the old `■` was
# in neither).
_STOP_STATE = {"done": "done", "max_iterations": "cancelled", "error": "failed",
               "review_exhausted": "cancelled", "stopped": "cancelled"}
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
    """Render a local guide reply without starting the LLM.

    Unboxed, like every other listing here: §12 keeps a drawn border for
    the permission band and inline diffs, both of which are ephemeral and
    demand an answer. An answer you merely read should not look like one.
    """
    mode = session_color_mode()
    text = getattr(reply, "text", str(reply))
    _say(console, GUTTER + _tinted(mode, "accent", "guide"))
    for line in text.rstrip("\n").split("\n"):
        _say(console, BODY_INDENT + _tinted(mode, "text", escape(line)))


def _transcript_layout(console: Console):
    """`(width, ColumnWidths)` for the linear transcript.

    `resolve_columns` refuses below 80 because a *lane list* cannot lose
    any more columns; a transcript has none to lose, so a narrow terminal
    clamps to the 80-column layout instead of refusing to print the run.
    """
    from relaycli.ui.layout import MINIMUM_WIDTH, resolve_columns

    width = max(console.width, MINIMUM_WIDTH)
    return width, resolve_columns(width)


class RichReporter:
    """Rich presentation of an agent run, as §03's transcript.

    `HH:MM:SS read src/auth/session.ts · 118 lines` — the same rows
    `ui/frame.render_transcript_line` draws inside the parallel frame, so a
    single-agent run and a lane's transcript are the same artifact. It used
    to be Claude Code's `→ tool` / `⏺` / `⎿` vocabulary, which is why the
    two halves of the product did not look related.

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
        # Local: ui.lanes reaches agent.graph, and agent -> tools -> render
        # is a real cycle this module has always had to import around.
        from relaycli.ui.frame import STAMP_WIDTH
        from relaycli.ui.lanes import gutter_left

        self.console = console
        self._streaming = False
        self._buf: list[str] = []
        self.tools_used: list[str] = []
        self._status = None
        self._tool_started: dict[str, float] = {}
        self._mode = session_color_mode()
        # Frozen at construction: Rich reads the width per print anyway, and
        # a transcript whose stamp column jumps mid-run because the window
        # was dragged is worse than one that stays put.
        self._width, self._columns = _transcript_layout(console)
        self._stamp_width = STAMP_WIDTH
        self._gutter = gutter_left(self._columns)
        # The column wrapped text lives in — the same arithmetic
        # frame.render_transcript does, so a wrapped row lands exactly where
        # the frame's own continuation rows would.
        self._body_width = max(
            self._width - self._columns.gutter - self._gutter - STAMP_WIDTH, 20)

    # -- transcript rows (ui/frame) -----------------------------------------
    def _row(self, **fields) -> None:
        """One §03 transcript row, clipped to the terminal — §6's "streaming
        text appends, never re-lays-out": a wrapped tool line would reflow
        every row above it on the next token."""
        from relaycli.ui import frame

        entry = frame.TranscriptEntry(stamp=time.strftime("%H:%M:%S"), **fields)
        self.console.print(
            frame.render_transcript_line(entry, self._columns, self._mode,
                                         self._width, merged=False))

    def _wrapped(self, text: str, *, kind: str = "text", ok: bool = True) -> None:
        """The two row kinds that must stay readable at any width: assistant
        prose and tool errors. Clipping either loses the thing you were
        reading the transcript for.

        The model's own line breaks survive the wrap — it writes lists and
        fenced code, and re-flowing those into one paragraph loses the
        structure the breaks were carrying. Continuation rows get a blank
        stamp so the timestamps stay a column instead of repeating.
        """
        from relaycli.ui import frame

        body = self._body_width
        chunks: list[str] = []
        for line in text.rstrip("\n").split("\n"):
            # break_on_hyphens=False: the text here is full of paths and
            # identifiers, and splitting `belajar-mandarin/index.html`
            # across two rows makes it uncopyable and unsearchable.
            chunks.extend(textwrap.wrap(line, width=body, break_on_hyphens=False) or [""])

        stamp = time.strftime("%H:%M:%S")
        blank = " " * (self._stamp_width - 1)
        for index, chunk in enumerate(chunks):
            entry = frame.TranscriptEntry(
                stamp=stamp if index == 0 else blank, kind=kind, text=chunk, ok=ok)
            self.console.print(
                frame.render_transcript_line(entry, self._columns, self._mode,
                                             self._width, merged=False))

    def _meta(self, text: str) -> None:
        """A muted row under the stamp column — run bookkeeping (token
        counts, step edges) that §03's transcript has no entry kind for."""
        indent = " " * (self._gutter + self._stamp_width)
        for chunk in textwrap.wrap(text, width=self._body_width, break_on_hyphens=False):
            # Wrapped here, not by the terminal: a terminal-wrapped line
            # loses the indent on its second row and breaks the stamp column.
            self.console.print(
                _tinted(self._mode, "muted", indent + escape(chunk)), highlight=False,
            )

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
        # The full litellm id, not the short name a lane's 15-column model
        # cell has to settle for: this row has the width, and "model" alone
        # (all `short_model_name("fake/model")` leaves) names nothing.
        self._row(kind="tool", tool="model", target=model, text=f"step {n}")
        if model.startswith(("ollama_chat/", "ollama/")) and n == 1:
            # Once per run, not per step: after the first step you know it
            # is Ollama, and the reminder is nine rows of noise by step ten.
            self._meta("Ollama local is generating · `ollama ps` should show "
                       "100% GPU when acceleration is active.")
        self._spin(f"waiting for {model}… (ctrl-c to interrupt)")

    def model_end(
        self, n: int, model: str, tool_calls: int, has_text: bool, usage
    ) -> None:
        self._unspin()
        if tool_calls:
            detail = f"{tool_calls} tool call{'s' if tool_calls != 1 else ''}"
        else:
            detail = "answer" if has_text else "empty response"
        self._meta(f"{detail} · {usage.total_tokens} tok")

    def model_error(self, n: int, model: str, error: Exception) -> None:
        self._unspin()
        self._row(kind="result", ok=False, text="✗ model error")

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
        self._wrapped(text)
        self._streaming = False

    def assistant_discard(self) -> None:
        self._buf.clear()
        self._streaming = False

    def tool_start(self, call: "ToolCall") -> None:
        # No row of its own. §03 writes one line per tool *use* — the
        # in-flight state lives in the lane, and this transcript has no
        # lane, so it lives in the spinner instead. A start row plus an end
        # row doubled the length of every run for no extra fact.
        self._tool_started[call.id] = time.perf_counter()
        self._spin(f"{_TOOL_ACTIVITY.get(call.name, 'using tool')}… (ctrl-c to interrupt)")

    def tool_end(self, call: "ToolCall", result: "ToolResult | None") -> None:
        from relaycli.ui.lanes import tool_target

        self.tools_used.append(call.name)
        self._unspin()
        started = self._tool_started.pop(call.id, None)
        # Sub-0.1s reads would all read "0.0s", which says nothing except
        # that the column exists. §03 only times the calls worth timing.
        seconds = time.perf_counter() - started if started is not None else 0.0
        elapsed = f"{seconds:.1f}s" if seconds >= 0.1 else ""
        outcome = "error" if result is None else (result.summary or call.name)
        # `· 240 lines · 1.4s` — the design's own detail tail. Nothing here
        # is markup: render_transcript_line builds rich Text, so a summary
        # holding model-controlled text cannot inject a style.
        detail = " · ".join(part for part in (outcome, elapsed) if part)
        self._row(kind="tool", tool=call.name, target=tool_target(call), text=detail)
        if result is not None and not result.ok and result.output:
            self._wrapped(f"✗ {brief_tool_error(result.output)}", kind="result", ok=False)


def render_task_summary(
    console: Console, result: "AgentResult", tools_used: list[str] | None = None
) -> None:
    """The settled line: `✓ done  1 steps · 0 tool calls · 3.3k tokens …`,
    in the §2 state glyph and hue the outcome maps to."""
    mode = session_color_mode()
    state = _STOP_STATE.get(result.stopped_reason, "failed")
    glyph = theme.TASK_STATE_GLYPHS[state]
    palette = theme.palette_for(mode)
    token = theme.TASK_STATE_COLOR[state]

    # On "done" the final text was already streamed token-by-token. On error /
    # max_iterations it was only constructed, never shown — print it or the
    # user gets a silent failure.
    if result.stopped_reason != "done" and getattr(result, "final_text", ""):
        console.print()
        _say(console, GUTTER + _tinted(mode, token,
                                      escape(friendly_error_text(result.final_text))))

    tools_note = ""
    if tools_used:
        from collections import Counter

        counts = Counter(tools_used)
        tools_note = " · " + ", ".join(f"{name}×{n}" if n > 1 else name for name, n in counts.items())

    _say(console)
    _say(console, GUTTER
         + _tinted(mode, token,
                   f"{glyph.symbol if palette else glyph.ascii} {result.stopped_reason}")
         + "  "
         + _tinted(mode, "muted",
                   escape(f"{result.iterations} steps · {result.tool_calls} tool calls"
                          f"{tools_note} · {result.usage.total_tokens} tokens · "
                          f"${result.usage.cost_usd:.6f} · {result.elapsed:.1f}s")))


def role_label(role_id: str) -> str:
    """`▣ cod coder` — §2's family glyph, three-letter code, and the role's
    own name. Unknown ids fall back to the name alone rather than inventing
    a mark: the five family glyphs are a closed set."""
    mark = theme.ROLE_MARKS.get(role_id)
    return f"{mark.family_glyph} {mark.code} {role_id}" if mark else role_id


class RelayRichObserver:
    """Rich presentation of a relay run: role banners + a reporter per role.

    Implements the duck-typed RelayObserver protocol used by
    :meth:`relaycli.relay.Relay.run` (role_start / reporter_for).
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.reporters: list[tuple[str, RichReporter]] = []

    def role_start(self, role: "Role", model: str, cycle: int) -> None:
        """`▣ cod coder  gpt-4o-mini · cycle 2`.

        One hue for every role, not one per role: §12 rejects colour-coding
        agents outright — identity lives in the id and the role mark, and
        colour stays reserved for state, which is the thing you need to see
        peripherally.
        """
        mode = session_color_mode()
        cycle_note = f" · cycle {cycle + 1}" if cycle else ""
        _say(self.console)
        _say(self.console,
             GUTTER + _tinted(mode, "accent", role_label(str(role)))
             + "  " + _tinted(mode, "muted", escape(model + cycle_note)))

    def reporter_for(self, role: "Role") -> RichReporter:
        reporter = RichReporter(self.console)
        self.reporters.append((str(role), reporter))
        return reporter

    def close(self) -> None:
        """Stop any spinner a role's reporter may have left live (idempotent)."""
        for _, reporter in self.reporters:
            reporter.close()


def render_setup_panel(console: Console, problem: str, detected: dict[str, bool]) -> None:
    """§11's failure card, for the case where the configured model has no
    usable credential.

    The design gives every failure the same three-part shape — glyph and
    what happened, one line of consequence, then the exact commands — and
    draws none of them in a box. `✗ NO API KEY · anthropic` is that card
    for this failure.
    """
    from relaycli.core.config import get_settings
    from relaycli.core.llm import best_ollama_model, ollama_host_label

    settings = get_settings()
    mode = session_color_mode()
    local_model = best_ollama_model(settings)

    _say(console)
    _say(console, GUTTER + _tinted(mode, "danger", "✗ SETUP NEEDED"))
    _say(console, BODY_INDENT + _tinted(mode, "muted", escape(problem)))
    _say(console)

    fixes: list[tuple[str, str]] = []
    if local_model:
        fixes.append(("relaycli init",
                      f"guided setup — Ollama is up at {ollama_host_label(settings)}, "
                      f"can use {local_model}"))
    else:
        fixes.append(("relaycli init", "guided setup for Ollama, OpenRouter, or API keys"))
    # Anchor on our own "Set <VAR> ..." sentence and take the LAST match: the
    # problem string also embeds the model id, which is config-controlled and
    # could be crafted to smuggle a fake *_API_KEY name in front of it.
    hinted = re.findall(r"\bSet ([A-Z][A-Z0-9_]*_API_KEY)\b", problem)
    if hinted:
        fixes.append((f"export {hinted[-1]}=...", "the key for the current model"))
    fixes.append(("relaycli config set-key <provider> --env <VAR>", "store a key reference"))
    fixes.append(("relaycli -m ollama_chat/llama3.1",
                  "local via Ollama, no key; needs `ollama serve`"))
    _pairs(console, mode, fixes)

    have = [name for name, ok in detected.items() if ok and name != "ollama"]
    if have:
        _say(console, BODY_INDENT + _tinted(mode, "muted", escape(
            f"keys already detected: {', '.join(have)} — pick one of their "
            "models with /model.")))


def render_slash_guide(console: Console) -> None:
    """Compact command palette shown by `/` and in the welcome flow."""
    _pairs(console, session_color_mode(), [
        ("/setup", "guided setup: model, keys, Ollama/n8n/web/postgres"),
        ("/model", "switch the model"),
        ("/mode", "suggest | auto-edit | full-auto"),
        ("/agents", "relay roles and task-split specialists"),
        ("/services", "optional Docker services"),
        ("/doctor", "health check"),
        ("/desktop", "browser UI"),
    ], heading="commands · / to filter, /help for all")


def short_model_name(model: str) -> str:
    """Compact display name: the last path segment of a LiteLLM model id."""
    return model.rsplit("/", 1)[-1] or model


# key_status (relaycli.core.llm.key_status) -> the words and the design
# token they are tinted with. "not needed" is muted because a local model
# having no key to miss is metadata, not good news worth a hue.
_KEY_NOTE = {
    "detected": ("key detected", "success"),
    "missing": ("key missing ▲", "warning"),
    "not needed": ("no key needed", "muted"),
}


def _key_note(key_status: str | None, mode: "theme.ColorMode") -> str:
    """The key-status words, tinted — or "" when the provider is unknown,
    where the banner makes no claim about credentials either way."""
    note = _KEY_NOTE.get(key_status or "")
    return _tinted(mode, note[1], note[0]) if note else ""

# PermissionMode value -> what it means for the user, in one clause.
_MODE_MEANING = {
    "suggest": "asks before every edit & command",
    "auto-edit": "applies edits, asks before commands",
    "full-auto": "runs edits & commands without asking",
}


def session_color_mode() -> "theme.ColorMode":
    """The dark / light / NO_COLOR mode this session draws in — the same
    resolution ui/live.py does, so the single-agent screens and the
    parallel frame can never end up on different palettes."""
    from relaycli.config.manager import load_app_config

    return theme.current_color_mode(load_app_config().preference("theme"))


def render_session_bar(
    console: Console, settings: "Settings", root: "Path", *,
    agents: int = 1, tokens: int = 0, spent_usd: float = 0.0,
    limit_usd: float | None = None, rule: bool = True,
    version: str = "", idle: bool = False,
) -> None:
    """§03's status bar — `▌relaycli ~/src/app  git:main  clean  mode:ask
    … 1 agent  8.1k tok  $0.09` — for the single-agent screens.

    The parallel frame has always drawn this; the ordinary path drew a
    key/value grid instead, so the two halves of the product did not look
    like the same product. Same renderer, same tokens, one agent.

    Below 80 columns `resolve_columns` refuses outright — that rule is
    about the *lane list*, whose columns genuinely stop fitting. A status
    bar has no columns to lose and truncates cleanly, so it clamps rather
    than refusing to greet you in a narrow terminal.
    """
    from relaycli.ui import frame, gitinfo
    from relaycli.ui.layout import MINIMUM_WIDTH, resolve_columns

    width = max(console.width, MINIMUM_WIDTH)
    columns = resolve_columns(width)
    mode = session_color_mode()
    repo = gitinfo.status(root)
    data = frame.StatusBarData(
        cwd=root, branch=repo.branch, dirty=repo.dirty,
        permission_mode=getattr(settings.permission_mode, "value", str(settings.permission_mode)),
        agents=agents, tokens=tokens, spent_usd=spent_usd, limit_usd=limit_usd,
        version=version, idle=idle,
    )
    console.print(frame.render_status_bar(data, columns, mode, width))
    if rule:
        console.print(frame.render_rule(columns, mode, width))


def _tinted(mode: "theme.ColorMode", token: str, text: str) -> str:
    """Rich markup for one design token — or bare text under NO_COLOR."""
    style = theme.style_for(mode, token)
    return f"[{style}]{text}[/{style}]" if style else text


# §11's label column: a fixed field so `models`, `roles`, `lanes` and
# `skills` line their values up.
FACT_LABEL_WIDTH = 9

# §4's left gutter: the 1 column every frame row starts at, so a line
# printed here lands on the same margin as the status bar above it. Written
# out rather than read from `gutter_left(columns)`, because §4 fixes the
# gutter at 2 for every width — threading a ColumnWidths through six helpers
# to look up a constant is plumbing, not a decision.
GUTTER = " "
# …plus the 2-space indent §11 gives the block under the rule.
BODY_INDENT = GUTTER + "  "


def _say(console: Console, markup: str = "") -> None:
    """Print pre-tinted markup with Rich's repr highlighter off.

    Left on, it bolds the digits inside a model id (`qwen2.5` ->
    `qwen2.`**5**) and re-colours the slash in `/relay`, both of which
    fight the palette the line was already tinted with.
    """
    console.print(markup, highlight=False)


def _fact(console: Console, mode: "theme.ColorMode", label: str, value: str) -> None:
    """One `  skills   9 loaded · …` row of §11's launch block."""
    _say(console, BODY_INDENT + _tinted(mode, "muted", label.ljust(FACT_LABEL_WIDTH)) + value)


def _pairs(console: Console, mode: "theme.ColorMode",
           rows: list[tuple[str, str]], *, heading: str = "") -> None:
    """A two-column list — name in `text`, what it does in `muted`.

    The shape §11 uses for its command line and `ui/live.render_help_overlay`
    uses for the `?` overlay. No box: §12 reserves a drawn border for the
    permission band and inline diffs, which are ephemeral, so a border on a
    listing reads as something waiting to be answered.
    """
    if heading:
        _say(console, GUTTER + _tinted(mode, "accent", heading))
    width = max((len(name) for name, _ in rows), default=0)
    for name, description in rows:
        _say(console, BODY_INDENT + _tinted(mode, "text", escape(name.ljust(width)))
             + "  " + _tinted(mode, "muted", escape(description)))


def render_welcome(
    console: Console, settings: "Settings", root: "Path", key_status: str | None
) -> None:
    """The REPL greeting: §03's status bar, the rule under it, then the
    few facts the bar has no column for.

    This used to be a bordered key/value panel in the old Claude-clay
    identity — a different product from the one the parallel frame draws.
    The bar carries cwd, branch, dirty count and mode now, so only what it
    cannot show is spelled out below: which model, whether its key is
    there, and the relay routing.

    ``key_status`` comes from :func:`relaycli.core.llm.key_status`; None means
    "unknown provider" and the banner makes no claim about credentials.
    """
    from pathlib import Path as _Path

    from relaycli import __version__

    mode = session_color_mode()

    def muted(text: str) -> str:
        return _tinted(mode, "muted", text)

    # §11's launch bar: it is the one screen that names the version, and it
    # says `idle` rather than `1 agent  0 tok` — nothing has run yet, and a
    # count of zero tokens is a number pretending to be a measurement.
    render_session_bar(console, settings, root, version=__version__, idle=True)

    model_line = _tinted(mode, "text", escape(settings.model))
    note = _key_note(key_status, mode)
    if note:
        model_line += f"  {note}"
    _fact(console, mode, "model", model_line)

    meaning = _MODE_MEANING.get(str(settings.permission_mode))
    if meaning:
        _fact(console, mode, "mode",
              _tinted(mode, "text", str(settings.permission_mode))
              + muted(f" · {meaning}"))

    if settings.relay_enabled:
        from relaycli.agent.router import routing_table

        routes = " · ".join(
            f"{role}:{escape(short_model_name(m))}"
            for role, m in routing_table(settings).items()
        )
        _fact(console, mode, "relay",
              _tinted(mode, "running", "on") + muted(f"  {routes}"))
    else:
        _fact(console, mode, "relay",
              muted("off — /relay on for planner → coder → reviewer"))

    _render_skills_fact(console, mode, root)

    _say(console)
    _say(console, muted(BODY_INDENT + 'Describe a change and RelayCLI works it through — try '
                        '"explain this repo", "fix failing tests".'))
    # Four, like §11's own line, and they fit one row at 80 columns. The
    # rest is what /help is for; the keys that quit and run a shell live in
    # the prompt's bottom toolbar, which is pinned and always in view.
    _say(console, BODY_INDENT + "   ".join(
        _tinted(mode, "text", name) + muted(f" {what}") for name, what in (
            ("/model", "switch model"), ("/mode", "permissions"),
            ("/doctor", "health check"), ("/help", "all commands"))))

    if root in (_Path.home(), _Path(_Path.home().anchor)):
        _say(console)
        # ▲ is §2's warning marker; ⚠ is in neither the glyph set nor the
        # ASCII fallback table, so it had no NO_COLOR spelling at all.
        _say(console, _tinted(mode, "warning",
                              BODY_INDENT + "▲ This is your whole home directory — the agent can "
                              "read and change anything under it."))
        _say(console, muted(BODY_INDENT + "  Better: cd into a project folder (e.g. mkdir "
                            "~/proyek/app && cd ~/proyek/app) and run relaycli there."))
    render_model_warning(console, settings)
    # Closes the launch block the way §11 does. The caret row under it is
    # the REPL's own prompt_toolkit line, and its bottom toolbar carries the
    # key strip — drawing a second, dead `❯` here would be a caret you
    # cannot type into sitting directly above one you can.
    _render_rule(console)


def _render_skills_fact(console: Console, mode: "theme.ColorMode", root: "Path") -> None:
    """`skills  9 loaded · ▲ 2 from this repo — they run with your
    permissions`. §10 marks project-sourced skills because that is a trust
    boundary: anyone with commit access can change them."""
    from relaycli.skills import discover_skills

    try:
        skills = discover_skills(root)
    except OSError:  # pragma: no cover - discover_skills already guards per-file
        return
    if not skills:
        return
    line = _tinted(mode, "text", f"{len(skills)} loaded")
    project = sum(1 for skill in skills.values() if skill.source == "project")
    if project:
        line += _tinted(mode, "warning", f" · ▲ {project} from this repo")
        line += _tinted(mode, "muted",
                        " — they run with your permissions · /skills inspect")
    else:
        line += _tinted(mode, "muted", " · /skills to list them")
    _fact(console, mode, "skills", line)


def render_screen_heading(console: Console, title: str, subtitle: str = "") -> None:
    """`▌relaycli  ROLES & SKILLS  11 of 16 enabled` and the rule under it.

    §07, §09 and §10 all open a full-screen view this way: the same focus
    rail and product name the status bar carries, then the screen's name in
    caps, then what is on it. A bordered panel is what this replaced, and
    §12 keeps borders for the two things that are ephemeral and demand an
    answer — the permission band and an inline diff.
    """
    mode = session_color_mode()
    rail = theme.MARKERS["focus_rail"]
    palette = theme.palette_for(mode)
    line = GUTTER + _tinted(mode, "accent",
                            f"[bold]{rail.symbol if palette else rail.ascii}relaycli[/bold]")
    line += "  " + _tinted(mode, "heading", escape(title))
    if subtitle:
        line += "  " + _tinted(mode, "muted", escape(subtitle))
    _say(console, line)
    _render_rule(console)


def _render_rule(console: Console) -> None:
    """The full-width `────` divider, from the same renderer the parallel
    frame uses so a rule is a rule everywhere in the product."""
    from relaycli.ui import frame
    from relaycli.ui.layout import MINIMUM_WIDTH, resolve_columns

    width = max(console.width, MINIMUM_WIDTH)
    console.print(frame.render_rule(resolve_columns(width), session_color_mode(), width))


def render_model_warning(console: Console, settings: "Settings") -> None:
    """Warn when a chosen local model may not drive tools reliably."""
    from relaycli.core.llm import tool_capability_warning

    warning = tool_capability_warning(settings.model)
    if warning:
        mode = session_color_mode()
        _say(console, _tinted(mode, "warning", BODY_INDENT + f"▲ {escape(warning)}"))


def render_status_line(
    console: Console, settings: "Settings", root: "Path", key_status: str | None = None
) -> None:
    """The one-shot (`-p`) header: the same §03 status bar the REPL and the
    parallel frame draw, plus the model line the bar has no column for."""
    mode = session_color_mode()
    render_session_bar(console, settings, root)
    line = _tinted(mode, "muted", "model ") + _tinted(mode, "text", escape(settings.model))
    note = _key_note(key_status, mode)
    if note:
        line += f"  {note}"
    console.print(line, highlight=False)


def render_help(console: Console) -> None:
    """The REPL /help screen: every accepted input form, aligned.

    Same two-column, box-free shape as the `?` key overlay the parallel
    frame draws (`ui/live.render_help_overlay`) — this is the linear
    session's version of that overlay, so it should not be a different
    artifact.
    """
    mode = session_color_mode()
    _pairs(console, mode, [
        ("<plain text>", "send a request to the agent"),
        ("/", "show the command palette"),
        ("/setup", "guided first-run setup (alias: /init)"),
        ("/init", "alias of /setup"),
        ("/model [name]", "show or switch the model (e.g. gpt-4o-mini, ollama_chat/llama3.1)"),
        ("/mode [m]", "permission mode: suggest | auto-edit | full-auto"),
        ("/relay [on|off]", "toggle the Planner → Coder → Reviewer pipeline"),
        ("/agents [r on|off]", "show relay agents; toggle explorer/tester"),
        ("/services [start names]", "show/start optional services: ollama, web, postgres, n8n"),
        ("/doctor", "run a local health check"),
        ("/skill [name]", "toggle a skill for this session (tdd, debug, ponytail, …)"),
        ("/skill auto [on|off]", "toggle per-request skill auto-activation"),
        ("/skills", "list available skills and where they come from"),
        ("/memory", "show long-term memory (global + project)"),
        ("/mcp", "show MCP connectors and their tools"),
        ("/desktop", "open the desktop web UI in your browser"),
        ("/config", "roles, per-role models & provider keys (persistent config)"),
        ("/settings", "general preferences: mode, theme, context limit"),
        ("/diff", "show uncommitted changes (git diff)"),
        ("/clear", "reset the conversation"),
        ("/help", "show this help  (aliases: help, ?)"),
        ("/exit", "quit  (aliases: exit, quit, Ctrl-D)"),
        ("!<cmd>", "run a shell command in the project root (e.g. !git status)"),
    ], heading="keys & commands")
    _say(console, BODY_INDENT + _tinted(mode, "muted",
                                 "Enter submits · Alt+Enter newline · Ctrl-R history · "
                                 "Ctrl-C clears the line · Ctrl-D quits"))


def render_routing_banner(console: Console, settings: "Settings") -> None:
    """Print the role → model routing line (model ids are untrusted: escape)."""
    from relaycli.agent.router import routing_table

    mode = session_color_mode()
    routes = " · ".join(f"{role}:{m}" for role, m in routing_table(settings).items())
    _say(console, _tinted(mode, "muted", "relay ") + _tinted(mode, "running", "on")
         + "  " + _tinted(mode, "muted", escape(routes)))


def render_relay_summary(console: Console, result: "RelayResult") -> None:
    """Print the end-of-relay summary: notes, per-role lines, and totals."""
    mode = session_color_mode()
    state = _STOP_STATE.get(result.stopped_reason, "failed")
    glyph = theme.TASK_STATE_GLYPHS[state]
    palette = theme.palette_for(mode)
    token = theme.TASK_STATE_COLOR[state]

    # error/max_iterations texts are constructed, never streamed. Anything
    # else (done, review_exhausted) was already streamed live by its role —
    # re-printing would duplicate the coder's report.
    if result.stopped_reason in ("error", "max_iterations") and result.final_text:
        console.print()
        _say(console, GUTTER + _tinted(mode, token,
                                      escape(friendly_error_text(result.final_text))))

    for note in result.notes:
        _say(console, GUTTER + _tinted(mode, "warning", f"▲ {escape(note)}"))

    _say(console)
    for run in result.role_runs:
        r = run.result
        _say(console, BODY_INDENT
             + _tinted(mode, "text", escape(role_label(str(run.role))))
             + _tinted(mode, "muted", escape(
                 f"  {run.model} · {r.iterations} steps · "
                 f"{r.usage.total_tokens} tokens · ${r.usage.cost_usd:.6f}")))
    verdict_note = f" · verdict {result.verdict}" if result.verdict else ""
    _say(console, GUTTER
         + _tinted(mode, token,
                   f"{glyph.symbol if palette else glyph.ascii} {result.stopped_reason}")
         + "  "
         + _tinted(mode, "muted", escape(
             f"{result.cycles + 1} cycle(s){verdict_note} · "
             f"{result.usage.total_tokens} tokens · ${result.usage.cost_usd:.6f} · "
             f"{result.elapsed:.1f}s")))


def render_parallel_summary(console: Console, result: "SchedulerResult") -> None:
    """The end-of-run summary for --experimental-parallel: one settled lane
    per task in graph order, then session totals.

    Each row is §2's state glyph, hue and role mark — the same vocabulary
    the live lane list uses — so the summary reads as those lanes coming to
    rest rather than as a separate report. The old `■` was in neither the
    glyph set nor the ASCII fallback table.
    """
    mode = session_color_mode()
    palette = theme.palette_for(mode)

    def settled(state: str, text: str) -> str:
        glyph = theme.TASK_STATE_GLYPHS[state]  # type: ignore[index]
        return _tinted(mode, theme.TASK_STATE_COLOR[state],  # type: ignore[index]
                       f"{glyph.symbol if palette else glyph.ascii} {text}")

    for task_id, task in result.graph.tasks.items():
        outcome = result.outcomes.get(task_id)
        detail = (f"{outcome.usage.total_tokens} tokens · ${outcome.usage.cost_usd:.6f}"
                  if outcome else "did not run")
        state = task.status if task.status in theme.TASK_STATE_GLYPHS else "failed"
        _say(console, BODY_INDENT + settled(state, task.status.ljust(9)) + " "
             + _tinted(mode, "text", escape(f"{task_id} {role_label(task.role_id)}"))
             + _tinted(mode, "muted", escape(f" · {detail}")))
        if outcome is not None and not outcome.ok and outcome.error:
            _say(console, BODY_INDENT + "  " + _tinted(mode, "danger", escape(outcome.error[:200])))

    state = ("cancelled" if result.stopped_early
             else ("done" if result.graph.all_ok() else "failed"))
    status = ("stopped early" if result.stopped_early
              else ("done" if result.graph.all_ok() else "done with failures"))
    _say(console)
    _say(console, BODY_INDENT + settled(state, status) + "  " + _tinted(mode, "muted", escape(
        f"{len(result.outcomes)}/{len(result.graph.tasks)} task(s) ran · "
        f"{result.budget.spent_tokens_total} tokens · "
        f"${result.budget.spent_usd_total:.6f} · {result.elapsed:.1f}s")))
