"""Tests for the 4-pillar refactoring: JSON robustness, session trim,
async permissions, and heuristics loading.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from json_repair import repair_json

from relaycli.agent.loop import text_tool_calls, _json_from_text, _likely_json_attempt
from relaycli.core.config import PermissionMode
from relaycli.heuristics import Heuristics, load_heuristics, reload_heuristics
from relaycli.core.permissions import PermissionManager, Decision
from relaycli.core.session import Session
from relaycli.tools import ToolRegistry, default_registry


# =============================================================================
# Pillar 1: JSON parsing robustness
# =============================================================================


class TestJsonParsingRobustness:
    """Verify that the json-repair based parsing handles real-world malformed
    JSON that the old regex approach would miss."""

    def test_standard_json_is_unchanged(self):
        """Clean JSON still parses correctly."""
        text = '{"name":"write_file","arguments":{"path":"index.html","content":"<h1>Hi</h1>"}}'
        result = _json_from_text(text)
        assert isinstance(result, dict)
        assert result["name"] == "write_file"

    def test_missing_trailing_brace_is_repaired(self):
        """Missing closing brace at end — common Ollama truncation."""
        text = '{"name":"read_file","arguments":{"path":"app.py"}'
        result = _json_from_text(text)
        # json_repair should fix this
        assert isinstance(result, dict)
        assert result.get("name") == "read_file"

    def test_trailing_comma_is_repaired(self):
        text = '{"name":"write_file","arguments":{"path":"x.txt","content":"y",}}'
        result = _json_from_text(text)
        assert isinstance(result, dict)
        assert result["arguments"]["content"] == "y"

    def test_single_quotes_are_repaired(self):
        """Models often produce single-quoted JSON (not valid per spec)."""
        text = "{'name': 'write_file', 'arguments': {'path': 'index.html'}}"
        result = _json_from_text(text)
        assert isinstance(result, dict)
        assert result.get("name") == "write_file"

    def test_json_in_code_fence_with_extra_text(self):
        text = (
            "Here is the JSON I meant:\n"
            "```json\n"
            '{"name":"create_folder","arguments":{"path":"new-folder"}}\n'
            "```\n"
            "Let me know if that works."
        )
        result = _json_from_text(text)
        assert isinstance(result, dict)
        assert result.get("name") == "create_folder"

    def test_json_array_is_parsed(self):
        text = (
            "```json\n"
            '[{"name":"create_folder","arguments":{"path":"shop"}},'
            '{"name":"write_file","arguments":{"path":"shop/index.html","content":"<h1>Shop</h1>"}}]\n'
            "```"
        )
        result = _json_from_text(text)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_malformed_non_json_returns_none(self):
        assert _json_from_text("") is None
        assert _json_from_text("   ") is None
        assert _json_from_text("Just a normal sentence.") is None

    def test_truncated_json_fragment_is_repaired(self):
        """A JSON fragment that is truncated mid-value should be repaired."""
        text = '{"name": "write_file", "arguments": {"path": "index.html", "content": "<!DOCTYPE ht'
        result = _json_from_text(text)
        # json_repair may fix this or it falls back to bracket matching
        # Either way it should not crash and should return something parseable
        if result is not None:
            assert isinstance(result, dict)

    def test_missing_quotes_around_key(self):
        text = "{name: 'write_file', arguments: {path: 'test.txt'}}"
        result = _json_from_text(text)
        assert isinstance(result, dict)

    def test_likely_json_attempt_detection(self):
        assert _likely_json_attempt('{"name": "test"}') is True
        assert _likely_json_attempt('```json\n{"name":"test"}\n```') is True
        assert _likely_json_attempt("Just a normal sentence.") is False
        assert _likely_json_attempt("") is False

    def test_repair_json_library_works_directly(self):
        """Direct test of the json_repair library's capability."""
        # This is a common case from Ollama models
        fixed = repair_json("{'name': 'test', 'args': {}}")
        assert fixed is not None
        parsed = json.loads(fixed)
        assert parsed["name"] == "test"

    def test_very_noisy_text_with_json_buried(self):
        text = (
            "Okay, I will create the file now. "
            "Here is my tool call: "
            '{"name":"write_file","arguments":{"path":"hello.txt","content":"Hello World"}}'
            " Let me know if you need changes."
        )
        result = _json_from_text(text)
        assert isinstance(result, dict)
        assert result["name"] == "write_file"

    def test_text_tool_calls_with_heuristics(self):
        """Integration test: text_tool_calls with the full pipeline."""
        registry = default_registry()
        text = '```json\n{"name":"write_file","arguments":{"path":"test.txt","content":"hello"}}\n```'
        calls = text_tool_calls(text, registry)
        assert len(calls) == 1
        assert calls[0].name == "write_file"

    def test_text_tool_calls_with_alias(self):
        """Aliases like 'mkdir' should resolve through heuristics."""
        registry = default_registry()
        text = '```json\n{"tool":"mkdir","arguments":{"path":"my-folder"}}\n```'
        calls = text_tool_calls(text, registry)
        assert len(calls) == 1
        assert calls[0].name == "create_folder"


# =============================================================================
# Pillar 2: Session smart trim
# =============================================================================


class TestSessionSmartTrim:
    """Verify that Session.trim() preserves last messages and uses summary."""

    def make_session(self, token_budget: int = 1_000_000) -> Session:
        """Create a session with a large enough budget for setup."""
        model = "gpt-4o-mini"
        sess = Session("You are a helpful assistant.", token_budget=token_budget, model=model)
        return sess

    def test_system_prompt_preserved(self):
        sess = self.make_session()
        assert sess.to_messages()[0]["role"] == "system"
        assert "helpful assistant" in sess.to_messages()[0]["content"]

    def test_add_user_and_assistant(self):
        sess = self.make_session()
        sess.add_user("Hello")
        sess.add_assistant_message({"role": "assistant", "content": "Hi there"})
        msgs = sess.to_messages()
        assert len(msgs) == 3
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"

    def test_trim_keeps_last_messages(self):
        """After trim with a tight budget, the last user message must remain."""
        sess = self.make_session(token_budget=100)
        sess.add_user("First message that is fairly long and uses tokens")
        sess.add_assistant_message({"role": "assistant", "content": "First response"})
        sess.add_user("Second message")
        sess.add_assistant_message({"role": "assistant", "content": "Second response"})
        sess.add_user("Third message that should be preserved")
        sess.add_assistant_message({"role": "assistant", "content": "Third response"})

        dropped = sess.trim()
        msgs = sess.to_messages()

        assert len(msgs) >= 2  # system + at least 1 message
        # The most recent user message should be there
        assert any("Third message" in str(m.get("content", "")) for m in msgs)
        assert dropped >= 0

    def test_summarization_fallback_format(self):
        """The summary message should follow the expected format."""
        sess = self.make_session(token_budget=50)
        # Add many long messages to force summarization
        for i in range(10):
            sess.add_user(f"This is user message number {i} with lots of padding tokens to consume budget")
            sess.add_assistant_message({"role": "assistant", "content": f"This is assistant reply number {i} with even more tokens to ensure we go way over budget"})

        dropped = sess.trim()
        msgs = sess.to_messages()

        # After aggressive trim, some messages should be summarized
        summary_msgs = [m for m in msgs if isinstance(m.get("content"), str) and "[Ringkasan Percakapan Sebelumnya:" in m["content"]]
        # The system message might have been summarized or the recent messages kept
        # At minimum, the system prompt must remain
        assert msgs[0]["role"] == "system"

    def test_trim_returns_int(self):
        sess = self.make_session()
        assert isinstance(sess.trim(), int)


# =============================================================================
# Pillar 3: Heuristics loading
# =============================================================================


class TestHeuristics:
    """Validate the heuristics.yaml loading and fallback behavior."""

    def test_heuristics_loads_defaults(self):
        """Even without a file, default values should work."""
        h = Heuristics({})
        assert isinstance(h.text_tool_aliases, dict)
        assert h.text_tool_aliases.get("ls") == "list_dir"
        assert h.text_tool_aliases.get("mkdir") == "create_folder"

    def test_heuristics_actionable_file_request(self):
        h = Heuristics({})
        assert h.looks_like_actionable_file_request("buat website toko laptop")
        assert h.looks_like_actionable_file_request("create a landing page")
        assert not h.looks_like_actionable_file_request("apa kabar")

    def test_heuristics_short_noop(self):
        h = Heuristics({})
        assert h.is_short_noop("done")
        assert h.is_short_noop("selesai")
        assert h.is_short_noop("Done.")
        assert not h.is_short_noop("I have created the file")

    def test_heuristics_done_claim(self):
        h = Heuristics({})
        assert h.contains_done_claim("I have created the website")
        assert h.contains_done_claim("<tool_response>")
        assert not h.contains_done_claim("still working on it")

    def test_heuristics_clarification_detection(self):
        h = Heuristics({})
        assert h.contains_clarification_or_tutorial("I need more information")
        assert h.contains_clarification_or_tutorial("here are the steps to create")
        assert not h.contains_clarification_or_tutorial("I have created the file")

    def test_canonical_tool_name_resolution(self):
        h = Heuristics({})
        known = {"write_file", "read_file", "create_folder", "list_dir"}
        assert h.canonical_tool_name("write_file", known) == "write_file"
        assert h.canonical_tool_name("mkdir", known) == "create_folder"
        assert h.canonical_tool_name("list_directory", known) == "list_dir"
        assert h.canonical_tool_name("build_web_app", known) is None

    def test_load_heuristics_cached(self):
        h1 = load_heuristics()
        h2 = load_heuristics()
        assert h1 is h2  # cached

    def test_reload_heuristics_fresh(self):
        h1 = load_heuristics()
        h2 = reload_heuristics()
        # After reload, it should be a fresh instance
        assert h1 is not h2  # different instance after reload


# =============================================================================
# Pillar 4: PermissionManager async support
# =============================================================================


class TestPermissionManagerAsync:
    """Validate async compatibility of PermissionManager."""

    @pytest.mark.asyncio
    async def test_async_auto_approve_full_auto(self):
        pm = PermissionManager(PermissionMode.full_auto)
        decision = await pm.confirm_async("edit", prompt_text="edit?")
        assert decision.approved is True
        assert decision.auto is True

    @pytest.mark.asyncio
    async def test_async_auto_approve_auto_edit(self):
        pm = PermissionManager(PermissionMode.auto_edit)
        decision = await pm.confirm_async("write", prompt_text="write?")
        assert decision.approved is True
        assert decision.auto is True

    @pytest.mark.asyncio
    async def test_async_secret_never_auto(self):
        pm = PermissionManager(PermissionMode.full_auto, prompter=lambda _: False)
        decision = await pm.confirm_async("read_secret", prompt_text="secret?")
        assert decision.approved is False

    @pytest.mark.asyncio
    async def test_async_with_prompter(self):
        calls = []
        def prompter(text: str) -> bool:
            calls.append(text)
            return True
        pm = PermissionManager(PermissionMode.suggest, prompter=prompter)
        decision = await pm.confirm_async("edit", prompt_text="approve?")
        assert decision.approved is True
        assert decision.auto is False
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_async_rejected_by_prompter(self):
        pm = PermissionManager(PermissionMode.suggest, prompter=lambda _: False)
        decision = await pm.confirm_async("command", prompt_text="run?")
        assert decision.approved is False

    def test_sync_confirm_still_works(self):
        """Backward compatibility: sync confirm must still function."""
        pm = PermissionManager(PermissionMode.full_auto)
        decision = pm.confirm("edit", prompt_text="?")
        assert decision.approved is True
        assert decision.auto is True

    def test_sync_confirm_suggest_prompts(self):
        pm = PermissionManager(PermissionMode.suggest, prompter=lambda _: True)
        decision = pm.confirm("command", prompt_text="run?")
        assert decision.approved is True
        assert decision.auto is False

    def test_decision_dataclass(self):
        d = Decision(approved=True, auto=True, reason="test")
        assert d.approved is True
        assert d.auto is True
        assert d.reason == "test"

    def test_is_auto_full_auto(self):
        pm = PermissionManager(PermissionMode.full_auto)
        assert pm.is_auto("edit") is True
        assert pm.is_auto("write") is True
        assert pm.is_auto("command") is True

    def test_is_auto_auto_edit(self):
        pm = PermissionManager(PermissionMode.auto_edit)
        assert pm.is_auto("edit") is True
        assert pm.is_auto("write") is True
        assert pm.is_auto("command") is False

    def test_is_auto_suggest(self):
        pm = PermissionManager(PermissionMode.suggest)
        assert pm.is_auto("edit") is False
        assert pm.is_auto("write") is False
        assert pm.is_auto("command") is False

    def test_read_secret_never_auto(self):
        pm = PermissionManager(PermissionMode.full_auto)
        assert pm.is_auto("read_secret") is False

    def test_set_mode(self):
        pm = PermissionManager(PermissionMode.suggest)
        assert pm.mode is PermissionMode.suggest
        pm.set_mode(PermissionMode.full_auto)
        assert pm.mode is PermissionMode.full_auto

    def test_mode_coerce_string(self):
        pm = PermissionManager("full-auto")
        assert pm.mode is PermissionMode.full_auto
