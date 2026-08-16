"""One place that hands out a line-input session.

prompt_toolkit gives history, completion and a bottom toolbar, and it is
what every interactive surface here wants. It also refuses to start at all
on a terminal it does not recognise: under Git Bash / mintty on Windows,
`TERM=xterm-256color` but Python is handed a pipe rather than a console,
so `PromptSession()` raises `NoConsoleScreenBufferError` before a single
character is read. That took `relaycli config` and `relaycli` itself down
with a traceback on a shell people actually use.

A pipe is a perfectly good place to read a line from, so fall back to
`input()`. The nice parts are lost; the command still runs.
"""

from __future__ import annotations

from typing import Any


class PlainSession:
    """`input()` wearing a PromptSession's one method.

    Accepts prompt_toolkit's formatted-text prompt (a list of
    `(style, text)` fragments) as well as a plain string, because callers
    pass whichever suits them and neither should have to know which kind of
    session it got.
    """

    def prompt(self, message: Any = "", **_kwargs: Any) -> str:
        if isinstance(message, list):
            message = "".join(str(fragment[1]) for fragment in message)
        return input(message)


def prompt_session(**kwargs: Any):
    """A `PromptSession`, or a `PlainSession` when this terminal cannot
    host one. Never raises for want of a terminal."""
    from prompt_toolkit import PromptSession

    try:
        return PromptSession(**kwargs)
    except Exception:
        # Deliberately broad: the failure modes are terminal-detection
        # errors from several layers (NoConsoleScreenBufferError, curses
        # setup, a missing controlling tty), and none of them is worth
        # ending the command over when input() would have worked.
        return PlainSession()
