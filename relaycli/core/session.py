"""Conversation session: message history + token-budget management.

The session holds the system prompt plus the running list of user / assistant /
tool messages, and trims the oldest *whole turns* when the estimated token
count approaches the budget. Trimming whole turns (a user message and the
assistant/tool messages that follow it) keeps tool-call/tool-result pairs
intact, which providers require.

Smart trim (``trim``):
- Always preserves the ``system`` prompt (excluded from messages) and the
  last **3** messages (user + assistant pair).
- For middle messages that exceed the budget, implements a **summarization
  fallback**: older user/assistant pairs are replaced with a single compact
  summary message: ``[Ringkasan Percakapan Sebelumnya: ...]``.
- This avoids naive truncation that loses conversational context.
"""

from __future__ import annotations

from typing import Any

from relaycli.core.llm import count_tokens

# Minimum messages to always keep at the tail (user+assistant pairs).
_KEEP_LAST = 3


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
        """Drop oldest history while keeping the most-recent context intact.

        Strategy (in order):
        1. Shed entire turns (user → assistant+tool-result groups) from the
           front until either the budget is met or only ``_KEEP_LAST`` user
           messages remain.
        2. If still over budget, use **summarization fallback**: merge the
           oldest remaining conversational messages into a single summary
           message so no tool-call/tool-result pairs are broken.
        3. Fall back to the original group-dropping strategy for edge cases.

        Returns the number of trim operations performed.
        """
        dropped = 0
        # Phase 1: drop oldest whole turns (preserving the last N).
        while self.estimated_tokens() > self.token_budget and self._has_droppable_turn():
            if self._user_count() <= _KEEP_LAST:
                break
            self._drop_oldest_turn()
            dropped += 1

        # Phase 2: summarization fallback for remaining over-budget messages.
        if self.estimated_tokens() > self.token_budget:
            changed = self._summarize_oldest_turn()
            if changed:
                dropped += 1

        # Phase 3: legacy intra-turn group drop (rare edge case).
        while self.estimated_tokens() > self.token_budget and self._drop_oldest_group_within_turn():
            dropped += 1

        return dropped

    def _user_count(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")

    def _user_indices(self) -> list[int]:
        return [i for i, m in enumerate(self.messages) if m.get("role") == "user"]

    def _assistant_indices(self) -> list[int]:
        return [i for i, m in enumerate(self.messages) if m.get("role") == "assistant"]

    def _has_droppable_turn(self) -> bool:
        return self._user_count() > 1

    def _drop_oldest_turn(self) -> None:
        indices = self._user_indices()
        if len(indices) < 2:
            return
        del self.messages[0: indices[1]]

    def _drop_oldest_group_within_turn(self) -> bool:
        if self._user_count() > 1:
            return False
        assistants = self._assistant_indices()
        if len(assistants) < 2:
            return False
        a0, a1 = assistants[0], assistants[1]
        del self.messages[a0:a1]
        return True

    # -- summarization fallback ------------------------------------------
    def _summarize_oldest_turn(self) -> bool:
        """Replace the oldest user+assistant pair with a summary marker.

        Merges the oldest user message and its subsequent assistant response
        (including any tool messages in between) into a single compact
        ``user`` message with a summary prefix.

        Returns True if a pair was summarized.
        """
        indices = self._user_indices()
        if len(indices) < _KEEP_LAST:
            return False

        user_idx = indices[0]
        user_content = str(self.messages[user_idx].get("content", "")).strip()

        # Find the next user message to bound the summarization block.
        next_user_idx = indices[1] if len(indices) > 1 else len(self.messages)

        # Collect the assistant reply texts for context.
        reply_parts: list[str] = []
        for i in range(user_idx + 1, next_user_idx):
            msg = self.messages[i]
            role = msg.get("role", "")
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                if role == "assistant":
                    reply_parts.append(content)
                elif role == "tool":
                    reply_parts.append(f"[tool:{msg.get('name','?')}] {content[:200]}")

        summary = _build_summary(user_content, reply_parts)

        # Replace the user message with the summary; remove everything after
        # it up to the next user message.
        self.messages[user_idx] = {"role": "user", "content": summary}
        del self.messages[user_idx + 1: next_user_idx]
        return True


_SUMMARY_PREFIX = "[Ringkasan Percakapan Sebelumnya: "
_SUMMARY_SUFFIX = "]"


def _build_summary(user_text: str, reply_texts: list[str]) -> str:
    """Build a compact summary string from a user/assistant exchange."""
    parts: list[str] = []
    if user_text:
        preview = user_text[:300]
        parts.append(f"pengguna: {preview}")
    if reply_texts:
        combined = " ".join(t.strip() for t in reply_texts if t.strip())
        if combined:
            preview = combined[:400]
            parts.append(f"asisten: {preview}")
    body = "; ".join(parts) if parts else "(percakapan diringkas)"
    return f"{_SUMMARY_PREFIX}{body}{_SUMMARY_SUFFIX}"
