"""Permission / approval system.

Three modes govern whether RelayCLI may act without asking:

* ``suggest`` (default): ask before any edit or command.
* ``auto-edit``: auto-apply edits, still ask before running commands.
* ``full-auto``: never prompt (a banner is shown while active).

The :class:`PermissionManager` is the single approval gate used by
``edit_file`` / ``write_file`` / ``run_command``. Crucially, approval is driven
only by the configured mode and the human at the keyboard — never by model
output. Tools must consult this gate before any side effect.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from relaycli.config import PermissionMode

# Actions that count as "edits" (auto-approved in auto-edit mode).
_EDIT_ACTIONS: frozenset[str] = frozenset({"edit", "write"})

# Actions that must ALWAYS ask a human, regardless of mode — a permission
# level chosen by the user (even full-auto) must never silently authorize
# disclosing credential-like files to the model. See `read_secret` in
# read_file. This is the technical control behind the trust boundary: the
# mode can loosen edits/commands, but never secret disclosure.
_ALWAYS_PROMPT_ACTIONS: frozenset[str] = frozenset({"read_secret"})


@dataclass
class Decision:
    """The outcome of an approval check."""

    approved: bool
    auto: bool = False  # True if granted by mode without prompting
    reason: str = ""


class PermissionManager:
    """Decides whether a given action may proceed under the active mode."""

    def __init__(
        self,
        mode: PermissionMode | str = PermissionMode.suggest,
        *,
        prompter: Callable[[str], bool] | None = None,
        console=None,
        assume_yes: bool | None = None,
    ) -> None:
        self.mode = self._coerce_mode(mode)
        # `prompter` is injectable so tests / alternative UIs can drive approval.
        self._prompter = prompter
        self._console = console
        # When stdin is not a TTY and no prompter is supplied, default behaviour
        # is to DENY (safe). `assume_yes` can override for non-interactive runs.
        self._assume_yes = assume_yes

    @staticmethod
    def _coerce_mode(mode: PermissionMode | str) -> PermissionMode:
        if isinstance(mode, PermissionMode):
            return mode
        return PermissionMode(str(mode))

    def set_mode(self, mode: PermissionMode | str) -> PermissionMode:
        self.mode = self._coerce_mode(mode)
        return self.mode

    def is_auto(self, action: str) -> bool:
        """Whether ``action`` is auto-approved by the current mode (no prompt)."""
        if action in _ALWAYS_PROMPT_ACTIONS:
            return False  # secret disclosure is never granted by mode alone
        if self.mode is PermissionMode.full_auto:
            return True
        if self.mode is PermissionMode.auto_edit and action in _EDIT_ACTIONS:
            return True
        return False

    def confirm(self, action: str, *, prompt_text: str) -> Decision:
        """Return a :class:`Decision` for ``action`` under the active mode.

        The caller is expected to have already shown the relevant preview
        (a diff for edits, the command for runs) before calling this.
        """
        if self.is_auto(action):
            return Decision(True, auto=True, reason=f"{self.mode} auto-approved {action}")
        approved = self._ask(prompt_text)
        return Decision(approved, auto=False, reason="user prompt")

    # -- internals -------------------------------------------------------
    def _ask(self, prompt_text: str) -> bool:
        if self._prompter is not None:
            return bool(self._prompter(prompt_text))

        if not sys.stdin.isatty():
            if self._assume_yes:
                return True
            self._print(
                "[yellow](non-interactive: denying by default — "
                "use full-auto/auto-edit or -y to allow)[/yellow]"
            )
            return False

        try:
            from rich.prompt import Confirm

            return bool(Confirm.ask(prompt_text, default=False, console=self._console))
        except (EOFError, KeyboardInterrupt):
            return False

    def _print(self, message: str) -> None:
        if self._console is not None:
            self._console.print(message)
        else:
            # Strip Rich markup for a plain stderr fallback.
            import re

            sys.stderr.write(re.sub(r"\[/?[^\]]+\]", "", message) + "\n")
