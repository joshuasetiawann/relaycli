"""Conversation session: message history + token-budget management.

The session holds the system prompt plus the running list of user / assistant /
tool messages, and trims the oldest *whole turns* when the estimated token
count approaches the budget. Trimming whole turns (a user message and the
assistant/tool messages that follow it) keeps tool-call/tool-result pairs
intact, which providers require.
"""

from __future__ import annotations

from typing import Any

from relaycli.llm import count_tokens


class Session:
    """Mutable conversation state for one agent run / REPL session."""

    def __init__(self, system_prompt: str, *, token_budget: int, model: str) -> None:
        self.system_prompt = system_prompt
        self.token_budget = token_budget
        self.model = model
        self.messages: list[dict[str, Any]] = []

    # -- mutation --------------------------------------------------------
    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, message: dict[str, Any]) -> None:
        """Append a pre-built assistant message (may include tool_calls)."""
        self.messages.append(message)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}
        )

    def reset(self) -> None:
        self.messages.clear()

    # -- rendering -------------------------------------------------------
    def to_messages(self) -> list[dict[str, Any]]:
        """Full message list (system prompt first) to send to the model."""
        return [{"role": "system", "content": self.system_prompt}, *self.messages]

    def estimated_tokens(self) -> int:
        return count_tokens(self.to_messages(), self.model)

    # -- budget ----------------------------------------------------------
    def trim(self) -> int:
        """Drop oldest history until under budget. Returns fragments dropped.

        First sheds whole prior turns; then, within a single long turn (the
        dominant path for a one-shot ``-p`` run, which has just one user
        message), sheds the oldest complete assistant+tool-results group. Groups
        are dropped whole so every ``tool`` result keeps its preceding
        ``assistant`` tool_call — a pairing providers require.
        """
        dropped = 0
        while self.estimated_tokens() > self.token_budget and self._has_droppable_turn():
            self._drop_oldest_turn()
            dropped += 1
        while self.estimated_tokens() > self.token_budget and self._drop_oldest_group_within_turn():
            dropped += 1
        return dropped

    def _user_indices(self) -> list[int]:
        return [i for i, m in enumerate(self.messages) if m.get("role") == "user"]

    def _assistant_indices(self) -> list[int]:
        return [i for i, m in enumerate(self.messages) if m.get("role") == "assistant"]

    def _has_droppable_turn(self) -> bool:
        # Keep at least the most recent user turn intact.
        return len(self._user_indices()) > 1

    def _drop_oldest_turn(self) -> None:
        indices = self._user_indices()
        if len(indices) < 2:
            return
        # Remove everything before the second user message (the oldest full turn).
        del self.messages[0 : indices[1]]

    def _drop_oldest_group_within_turn(self) -> bool:
        """Drop the oldest assistant(+its tool results) group of the current turn.

        Keeps the leading user message and the most recent assistant/tool group.
        Only runs on a single-turn session (guarded), and returns False when
        there is nothing left to shed but that most-recent group.
        """
        if len(self._user_indices()) > 1:
            return False
        assistants = self._assistant_indices()
        if len(assistants) < 2:
            return False  # only the most-recent group remains — keep it
        a0, a1 = assistants[0], assistants[1]
        # a0 > 0: the leading user message precedes it and is preserved; a1
        # onward (the most recent group) is preserved.
        del self.messages[a0:a1]
        return True
