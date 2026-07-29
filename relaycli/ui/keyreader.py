"""Raw-mode key reader for the live parallel view.

The impure half of the key map (ui/keymap.py holds the pure half): puts
the terminal in cbreak mode, reads one key at a time on a daemon thread,
and hands each key to a callback. Nothing here interprets a key.

Two details this has to get right:

* **ESC is ambiguous.** A bare `esc` and the start of `shift-tab`
  (`\\x1b[Z`) are the same first byte, so after an ESC we wait
  ESC_SEQUENCE_TIMEOUT for a continuation before deciding. Without that
  wait, either esc lags a keystroke behind or shift-tab reports as esc —
  and esc stops the whole run, so guessing wrong is expensive.
* **It must be inert without a TTY.** Piped output, CI, and the pytest
  suite all have a non-tty stdin; `start()` returns a no-op stopper
  rather than raising or, worse, leaving the user's terminal in cbreak
  mode after the process exits.
"""

from __future__ import annotations

import contextlib
import os
import select
import sys
import threading
from typing import Callable, Iterator

ESC_SEQUENCE_TIMEOUT = 0.05
_POLL_INTERVAL = 0.1


def stdin_is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):  # closed or replaced by a test double
        return False


@contextlib.contextmanager
def cbreak_mode(fd: int) -> Iterator[None]:
    """cbreak, not raw: raw would also swallow ^C, and a user hammering
    ^C on a runaway agent must always keep working."""
    import termios
    import tty

    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _read_key(fd: int) -> str | None:
    """One logical key, or None when nothing arrived this poll."""
    if not select.select([fd], [], [], _POLL_INTERVAL)[0]:
        return None
    first = os.read(fd, 1).decode("utf-8", "replace")
    if first != "\x1b":
        return first
    # Possible escape sequence — give the rest of it a moment to land.
    if not select.select([fd], [], [], ESC_SEQUENCE_TIMEOUT)[0]:
        return "\x1b"
    rest = os.read(fd, 8).decode("utf-8", "replace")
    return "\x1b" + rest


def start(on_key: Callable[[str], None]) -> Callable[[], None]:
    """Begin reading keys; returns a stopper. A no-op stopper is returned
    when stdin isn't a terminal, so callers never need to special-case it."""
    if not stdin_is_interactive():
        return lambda: None

    fd = sys.stdin.fileno()
    stop = threading.Event()

    def loop() -> None:
        try:
            with cbreak_mode(fd):
                while not stop.is_set():
                    key = _read_key(fd)
                    if key:
                        try:
                            on_key(key)
                        except Exception:
                            # A broken handler must not restore-less-ly kill
                            # the thread: the cbreak context manager only
                            # restores on the way out of `with`.
                            pass
        except Exception:
            pass  # no terminal control available; navigation is simply off

    thread = threading.Thread(target=loop, name="relaycli-keys", daemon=True)
    thread.start()

    def stopper() -> None:
        stop.set()
        thread.join(timeout=1.0)

    return stopper
