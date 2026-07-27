"""Conversation session — message history with token-budget management.

Smart trim preserves the system prompt, keeps the last 3 messages intact,
and uses summarization fallback for middle messages that exceed budget.
"""

from __future__ import annotations

from typing import Any

from relaycli.core.llm import count_tokens

_KEEP_LAST = 3


class Session:
    def __init__(self, system_prompt: str, *, token_budget: int, model: str) -> None:
        self.system_prompt = system_prompt
        self.token_budget = token_budget
        self.model = model
        self.messages: list[dict[str, Any]] = []

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content})

    def reset(self) -> None:
        self.messages.clear()

    def to_messages(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.system_prompt}, *self.messages]

    def estimated_tokens(self) -> int:
        return count_tokens(self.to_messages(), self.model)

    def trim(self) -> int:
        dropped = 0
        while self.estimated_tokens() > self.token_budget and self._has_droppable_turn():
            if self._user_count() <= _KEEP_LAST:
                break
            self._drop_oldest_turn()
            dropped += 1

        if self.estimated_tokens() > self.token_budget:
            if self._summarize_oldest_turn():
                dropped += 1

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

    def _summarize_oldest_turn(self) -> bool:
        indices = self._user_indices()
        if len(indices) < _KEEP_LAST:
            return False
        user_idx = indices[0]
        user_content = str(self.messages[user_idx].get("content", "")).strip()
        next_user_idx = indices[1] if len(indices) > 1 else len(self.messages)

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
        self.messages[user_idx] = {"role": "user", "content": summary}
        del self.messages[user_idx + 1: next_user_idx]
        return True


_SUMMARY_PREFIX = "[Ringkasan Percakapan Sebelumnya: "
_SUMMARY_SUFFIX = "]"


def _build_summary(user_text: str, reply_texts: list[str]) -> str:
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
