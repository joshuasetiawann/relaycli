"""Permission/approval system — three modes, one gate.

suggest:     ask before every edit or command
auto-edit:   auto-apply edits, ask before commands
full-auto:   never prompt

Async support via :meth:`confirm_async`.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Callable

from relaycli.core.config import PermissionMode

_EDIT_ACTIONS: frozenset[str] = frozenset({"edit", "write"})
_ALWAYS_PROMPT_ACTIONS: frozenset[str] = frozenset({"read_secret"})


@dataclass
class Decision:
    approved: bool
    auto: bool = False
    reason: str = ""


class PermissionManager:
    def __init__(
        self,
        mode: PermissionMode | str = PermissionMode.suggest,
        *,
        prompter: Callable[[str], bool] | None = None,
        console=None,
        assume_yes: bool | None = None,
    ) -> None:
        self.mode = self._coerce_mode(mode)
        self._prompter = prompter
        self._console = console
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
        if action in _ALWAYS_PROMPT_ACTIONS:
            return False
        if self.mode is PermissionMode.full_auto:
            return True
        if self.mode is PermissionMode.auto_edit and action in _EDIT_ACTIONS:
            return True
        return False

    def confirm(self, action: str, *, prompt_text: str) -> Decision:
        if self.is_auto(action):
            return Decision(True, auto=True, reason=f"{self.mode} auto-approved {action}")
        approved = self._ask(prompt_text)
        return Decision(approved, auto=False, reason="user prompt")

    async def confirm_async(self, action: str, *, prompt_text: str) -> Decision:
        if self.is_auto(action):
            return Decision(True, auto=True, reason=f"{self.mode} auto-approved {action}")
        loop = asyncio.get_running_loop()
        approved = await loop.run_in_executor(None, self._ask, prompt_text)
        return Decision(approved, auto=False, reason="user prompt")

    def _ask(self, prompt_text: str) -> bool:
        if self._prompter is not None:
            return bool(self._prompter(prompt_text))
        if not sys.stdin.isatty():
            if self._assume_yes:
                return True
            self._print("[yellow](non-interactive: denying by default — use full-auto or -y)[/yellow]")
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
            import re
            sys.stderr.write(re.sub(r"\[/?[^\]]+\]", "", message) + "\n")
