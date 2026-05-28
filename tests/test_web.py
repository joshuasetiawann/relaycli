"""relaycli web tests: state/send/events API with a scripted LLM, no network."""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from relaycli.config import PermissionMode, Settings
from relaycli.llm import LLMResponse, Usage
from relaycli.web import UI_PATH, WebSession, make_handler


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch, tmp_path):
    for var in list(os.environ):
        if var.startswith("RELAYCLI_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(Settings.model_config, "toml_file", str(tmp_path / "no.toml"))
    # WebSession reads/writes the roster via appconfig — keep it hermetic so
    # tests never touch the real ~/.relaycli/config.toml.
    from relaycli import appconfig
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "roster.toml")
    monkeypatch.chdir(tmp_path)


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    def complete(self, messages, *, tools=None, model=None, temperature=None,
                 stream=False, on_token=None):
        resp = self._responses.pop(0)
        if on_token and resp.text:
            on_token(resp.text)
        return resp


def _settings(**kw) -> Settings:
    kw.setdefault("model", "fake/model")
    kw.setdefault("permission_mode", PermissionMode.full_auto)
    return Settings(_env_file=None, **kw)


def _resp(text) -> LLMResponse:
    return LLMResponse(text=text, usage=Usage(total_tokens=8))


def test_state_reports_session():
    session = WebSession(_settings())
    state = session.state()
    assert state["model"] == "fake/model"
    assert state["mode"] == "full-auto"
    assert {r["name"] for r in state["roles"]} == {
        "explorer", "planner", "coder", "tester", "reviewer",
    }
    assert "ponytail" in state["skills"]
    assert state["busy"] is False


def test_send_runs_agent_and_records_events():
    session = WebSession(_settings(), llm=FakeLLM([_resp("Hello from the agent")]))
    assert session.send("halo") is True
    session._thread.join(timeout=30)
    kinds = [e["kind"] for e in session.events_since(0)]
    assert kinds[0] == "user"
    assert "text" in kinds and "summary" in kinds
    texts = [e for e in session.events_since(0) if e["kind"] == "text"]
    assert texts[0]["text"] == "Hello from the agent"
    summary = [e for e in session.events_since(0) if e["kind"] == "summary"][0]
    assert summary["stopped"] == "done"
    # events are monotonically numbered for the polling API
    assert [e["n"] for e in session.events_since(0)] == list(range(len(kinds)))


def test_send_mode_override_and_suggest_note():
    session = WebSession(_settings(), llm=FakeLLM([_resp("ok")]))
    session.send("halo", mode="suggest")
    session._thread.join(timeout=30)
    assert session.settings.permission_mode is PermissionMode.suggest
    assert any(e["kind"] == "note" for e in session.events_since(0))


def test_http_endpoints_roundtrip():
    session = WebSession(_settings(), llm=FakeLLM([_resp("Done.")]))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "RelayCLI" in html and "Agents" in html

        state = json.loads(urllib.request.urlopen(base + "/api/state", timeout=5).read())
        assert state["model"] == "fake/model"

        req = urllib.request.Request(
            base + "/api/send", method="POST",
            data=json.dumps({"text": "hi", "mode": "full-auto"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req, timeout=5).read()) == {"ok": True}
        session._thread.join(timeout=30)

        events = json.loads(
            urllib.request.urlopen(base + "/api/events?since=0", timeout=5).read()
        )
        kinds = [e["kind"] for e in events["events"]]
        assert "user" in kinds and "summary" in kinds
        assert events["busy"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_send_empty_rejected_and_ui_file_ships():
    session = WebSession(_settings())
    assert UI_PATH.is_file()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/send", method="POST",
            data=b'{"text": "  "}', headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_dns_rebinding_and_cross_origin_posts_rejected():
    session = WebSession(_settings())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # DNS rebinding: loopback socket but a foreign Host header.
        req = urllib.request.Request(base + "/api/state", headers={"Host": "evil.example"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 421")
        except urllib.error.HTTPError as exc:
            assert exc.code == 421

        # Cross-origin POST (e.g. a text/plain form from a malicious page).
        req = urllib.request.Request(
            base + "/api/send", method="POST",
            data=b'{"text": "rm -rf"}',
            headers={"Origin": "http://evil.example"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert session.events_since(0) == []  # nothing ran

        # Same-origin POST still works (loopback Origin, as our UI sends).
        req = urllib.request.Request(
            base + "/api/send", method="POST",
            data=b'{"text": ""}',  # empty → 400, but PAST the origin guard
            headers={"Origin": f"http://127.0.0.1:{port}",
                     "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_state_lists_models_and_config():
    session = WebSession(_settings(model="openrouter/qwen/qwen3-coder:free"))
    state = session.state()
    models = state["models"]
    ids = [m["id"] for m in models]
    assert "openrouter/qwen/qwen3-coder:free" in ids
    assert any(m["current"] for m in models)
    assert state["config_file"].endswith("config.toml")
    assert "tasks" in state and state["tasks"] == state["split_tasks"]

    # 6 direct providers x 2 + OpenRouter x 8 + Ollama (local), grouped.
    groups = {}
    for m in models:
        groups.setdefault(m["group"], []).append(m)
    for provider in ("GPT", "Gemini", "Claude", "DeepSeek", "Qwen", "GLM"):
        assert len(groups[provider]) == 2, provider
    assert len(groups["OpenRouter"]) == 8
    assert "Ollama" in groups and len(groups["Ollama"]) >= 1
    assert all(m["id"].startswith("ollama_chat/") for m in groups["Ollama"])


def test_current_model_surfaces_under_current_group():
    session = WebSession(_settings(model="some/exotic-model"))
    models = session.state()["models"]
    cur = [m for m in models if m["current"]]
    assert len(cur) == 1 and cur[0]["group"] == "Current"
    assert cur[0]["id"] == "some/exotic-model"


def test_set_model_and_flags():
    session = WebSession(_settings())
    session.set_model("ollama_chat/llama3.1")
    assert session.settings.model == "ollama_chat/llama3.1"
    assert session.set_flag("tasks", True) is True
    assert session.settings.relay_split_tasks is True
    assert session.set_flag("explorer", True) is True
    assert session.settings.relay_explorer is True
    assert session.set_flag("bogus", True) is False


def test_reset_clears_events_when_idle():
    session = WebSession(_settings())
    session.add("user", text="hi")
    assert session.reset() is True
    assert session.events_since(0) == []


def test_stop_halts_a_relay_run():
    # A coder that would loop forever (always returns a tool call) must stop
    # once the Stop flag is set — the should_stop hook is checked per step.
    # send() clears the flag (a new run starts fresh), so Stop must be tripped
    # while the run is in flight: the FakeLLM sets it right after the planner.
    from relaycli.llm import ToolCall

    class StoppingLLM:
        def __init__(self, session):
            self._session = session
            self._calls = 0

        def complete(self, messages, *, tools=None, model=None, temperature=None,
                     stream=False, on_token=None):
            self._calls += 1
            if self._calls == 1:  # planner
                if on_token:
                    on_token("Goal\n1. a\n2. b")
                return _resp("Goal\n1. a\n2. b")
            self._session.stop()  # a coder is now running: ask it to halt
            return LLMResponse(
                text="", usage=Usage(total_tokens=8),
                tool_calls=[ToolCall(id="c1", name="list_dir", arguments="{}")],
            )

    session = WebSession(_settings(relay_enabled=True))
    session._llm = StoppingLLM(session)
    session.send("do it")
    session._thread.join(timeout=30)
    assert not session.busy
    summary = [e for e in session.events_since(0) if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "stopped"


def test_http_stop_model_reset_endpoints():
    session = WebSession(_settings())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"

        def post(path, body):
            req = urllib.request.Request(
                base + path, method="POST", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Origin": f"http://127.0.0.1:{port}"},
            )
            return json.loads(urllib.request.urlopen(req, timeout=5).read())

        assert post("/api/model", {"model": "ollama_chat/llama3.1"})["model"] == "ollama_chat/llama3.1"
        assert post("/api/flag", {"name": "tasks", "on": True})["ok"] is True
        assert session.settings.relay_split_tasks is True
        assert post("/api/stop", {})["ok"] is True
        assert post("/api/reset", {})["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_state_includes_full_roster_and_set_roster():
    session = WebSession(_settings(relay_enabled=True))
    roster = session.state()["roster"]
    ids = {r["id"] for r in roster}
    assert {"orchestrator", "coder", "backend", "frontend", "security"} <= ids
    assert len(roster) == 16
    # enabling + assigning a roster role persists and reflects in state
    assert session.set_roster("backend", enabled=True, model="strong") is True
    r2 = {r["id"]: r for r in session.state()["roster"]}["backend"]
    assert r2["enabled"] is True and r2["assigned"] == "strong"
    assert session.set_roster("nope", enabled=True) is False


def test_state_lists_enabled_specialists():
    session = WebSession(_settings(relay_enabled=True))
    specs = session.state()["specialists"]
    assert "coder" in specs                       # enabled implementer
    assert "planner" not in specs                 # pipeline role, not a task owner


def test_role_models_in_state_and_set():
    session = WebSession(_settings(model="base/model", relay_enabled=True))
    rm = {r["role"]: r for r in session.state()["role_models"]}
    assert set(rm) == {"explorer", "planner", "coder", "tester", "reviewer"}
    # default: no override, resolves to the base model
    assert rm["coder"]["assigned"] is None
    assert rm["coder"]["resolved"] == "model"  # short name of base/model
    # assign a specialist to the coder
    assert session.set_role_model("coder", "claude-3-5-sonnet-latest") is True
    assert session.settings.coder_model == "claude-3-5-sonnet-latest"
    rm2 = {r["role"]: r for r in session.state()["role_models"]}
    assert rm2["coder"]["resolved"] == "claude-3-5-sonnet-latest"
    # clearing falls back to the base model
    assert session.set_role_model("coder", "") is True
    assert session.settings.coder_model is None
    assert session.set_role_model("bogus", "x") is False


def test_set_key_settings_field_vs_env(monkeypatch):
    session = WebSession(_settings())
    # managed provider → Settings field
    assert session.set_key("openai", "sk-managed") is True
    assert session.settings.openai_api_key == "sk-managed"
    # unmanaged provider → process env var LiteLLM reads
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert session.set_key("deepseek", "sk-deep") is True
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-deep"
    # provider status reflects both
    st = {p["id"]: p for p in session.state()["providers"]}
    assert st["openai"]["detected"] is True
    assert st["deepseek"]["detected"] is True
    assert st["anthropic"]["detected"] is False
    # clearing removes it
    assert session.set_key("deepseek", "") is True
    assert "DEEPSEEK_API_KEY" not in os.environ
    assert session.set_key("nope", "x") is False


def test_role_model_and_key_endpoints():
    session = WebSession(_settings(relay_enabled=True))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"

        def post(path, body):
            req = urllib.request.Request(
                base + path, method="POST", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Origin": f"http://127.0.0.1:{port}"})
            return json.loads(urllib.request.urlopen(req, timeout=5).read())

        assert post("/api/role-model", {"role": "planner", "model": "deepseek/deepseek-reasoner"})["ok"]
        assert session.settings.planner_model == "deepseek/deepseek-reasoner"
        assert post("/api/key", {"provider": "anthropic", "key": "sk-ant-web"})["ok"]
        assert session.settings.anthropic_api_key == "sk-ant-web"
    finally:
        server.shutdown(); server.server_close()
