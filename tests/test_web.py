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
    for var in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY", "ZHIPUAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(Settings.model_config, "toml_file", str(tmp_path / "no.toml"))
    # WebSession reads/writes the roster via appconfig — keep it hermetic so
    # tests never touch the real ~/.relaycli/config.toml.
    from relaycli import appconfig
    from relaycli import model_catalog
    monkeypatch.setattr(appconfig, "CONFIG_FILE", tmp_path / "roster.toml")
    model_catalog._LIVE_CACHE.clear()
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


class RecordingLLM(FakeLLM):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls = []

    def complete(self, messages, *, tools=None, model=None, temperature=None,
                 stream=False, on_token=None):
        self.calls.append(list(messages))
        return super().complete(
            messages, tools=tools, model=model, temperature=temperature,
            stream=stream, on_token=on_token,
        )


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
    assert state["local_scaffolds"] is False
    assert "onboarding" in state
    assert "ready" in state["onboarding"]
    assert any(c["name"] == "model" and c["usage"].startswith("/model") for c in state["commands"])
    assert any(c["name"] == "mode" and c["group"] == "Safety" for c in state["commands"])


def test_send_runs_agent_and_records_events():
    session = WebSession(_settings(), llm=FakeLLM([_resp("Hello from the agent")]))
    assert session.send("explain this repo") is True
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


def test_send_records_model_progress_logs():
    session = WebSession(_settings(), llm=FakeLLM([_resp("Hello from the agent")]))

    assert session.send("explain this repo") is True
    session._thread.join(timeout=30)

    logs = [e["text"] for e in session.events_since(0) if e["kind"] == "log"]
    assert any("→ model step 1" in text for text in logs)
    assert any("← model answer" in text for text in logs)


def test_send_greeting_returns_local_guide_without_thread():
    session = WebSession(_settings(), llm=FakeLLM([_resp("should not run")]))
    assert session.send("halo") is True
    assert session._thread is None
    events = session.events_since(0)
    assert [e["kind"] for e in events] == ["user", "guide", "summary"]
    assert events[1]["agent"] == "guide"
    assert "siap bantu" in events[1]["text"]
    assert events[2]["tokens"] == 0
    assert events[2]["stopped"] == "done"


def test_send_short_followup_continues_previous_request():
    llm = RecordingLLM([_resp("first done"), _resp("continued")])
    session = WebSession(_settings(), llm=llm)

    assert session.send('buat website toko di folder "tokoku"') is True
    session._thread.join(timeout=30)
    assert session.send("lanjut") is True
    session._thread.join(timeout=30)

    second_user_messages = [
        m["content"] for m in llm.calls[1]
        if m.get("role") == "user"
    ]
    assert any("Original request:" in text for text in second_user_messages)
    assert any('buat website toko di folder "tokoku"' in text for text in second_user_messages)
    assert any("User follow-up:\nlanjut" in text for text in second_user_messages)
    assert any(
        e["kind"] == "note" and "continuing the previous request" in e["text"]
        for e in session.events_since(0)
    )


def test_send_frontend_prompt_uses_agent_by_default(tmp_path):
    llm = RecordingLLM([_resp("agent handled")])
    session = WebSession(_settings(permission_mode=PermissionMode.full_auto), llm=llm)

    assert session.send("buatin saya web platform belajar mandarin") is True
    session._thread.join(timeout=30)

    assert llm.calls
    assert not (tmp_path / "belajar-mandarin").exists()
    events = session.events_since(0)
    assert any(e["kind"] == "text" and e["text"] == "agent handled" for e in events)
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"


def test_send_frontend_shop_scaffold_runs_locally_without_llm(tmp_path):
    llm = RecordingLLM([_resp("should not run")])
    session = WebSession(
        _settings(permission_mode=PermissionMode.full_auto, local_scaffolds=True),
        llm=llm,
    )

    assert session.send(
        "buatin saya web toko spatu di front endnya aja di folder baru namanya sepatuu yaa"
    ) is True
    session._thread.join(timeout=30)

    assert llm.calls == []
    assert (tmp_path / "sepatuu" / "index.html").is_file()
    assert (tmp_path / "sepatuu" / "styles.css").is_file()
    assert (tmp_path / "sepatuu" / "app.js").is_file()
    events = session.events_since(0)
    assert any(e["kind"] == "tool" and "sepatuu/index.html" in e["summary"] for e in events)
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"
    assert summary["tokens"] == 0


def test_send_frontend_shop_scaffold_respects_quoted_folder_without_llm(tmp_path):
    llm = RecordingLLM([_resp("should not run")])
    session = WebSession(
        _settings(permission_mode=PermissionMode.full_auto, local_scaffolds=True),
        llm=llm,
    )

    assert session.send(
        'tolong build ulang website toko baju online di folder baru bernama "toko baju" pake html css js aja nuansa hitam gekao'
    ) is True
    session._thread.join(timeout=30)

    assert llm.calls == []
    html = (tmp_path / "toko baju" / "index.html").read_text()
    app_js = (tmp_path / "toko baju" / "app.js").read_text()
    assert 'class="theme-dark"' in html
    assert "Baju harian" in html
    assert "Everyday Cotton Tee" in app_js
    assert not (tmp_path / "bernama").exists()
    summary = [e for e in session.events_since(0) if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"


def test_send_mandarin_learning_platform_scaffold_without_llm(tmp_path):
    llm = RecordingLLM([_resp("should not run")])
    session = WebSession(
        _settings(permission_mode=PermissionMode.full_auto, local_scaffolds=True),
        llm=llm,
    )

    assert session.send("buatin saya web platform belajar mandarin") is True
    session._thread.join(timeout=30)

    assert llm.calls == []
    html = (tmp_path / "belajar-mandarin" / "index.html").read_text()
    app_js = (tmp_path / "belajar-mandarin" / "app.js").read_text()
    assert "MandarinLab" in html
    assert "Belajar Mandarin" in html
    assert "Nada dan Pinyin" in app_js
    assert any(
        e["kind"] == "text" and "MandarinLab" in e["text"] and "toko sepatu" not in e["text"]
        for e in session.events_since(0)
    )
    summary = [e for e in session.events_since(0) if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"


def test_send_frontend_shop_scaffold_defaults_folder_without_llm(tmp_path):
    llm = RecordingLLM([_resp("should not run")])
    session = WebSession(
        _settings(permission_mode=PermissionMode.full_auto, local_scaffolds=True),
        llm=llm,
    )

    assert session.send("buatin saya web toko sepatu fokus frontend aja") is True
    session._thread.join(timeout=30)

    assert llm.calls == []
    assert (tmp_path / "toko-sepatu" / "index.html").is_file()
    assert (tmp_path / "toko-sepatu" / "styles.css").is_file()
    summary = [e for e in session.events_since(0) if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"
    assert summary["tokens"] == 0


def test_send_frontend_shop_scaffold_respects_suggest_mode(tmp_path):
    session = WebSession(
        _settings(permission_mode=PermissionMode.suggest, local_scaffolds=True)
    )

    assert session.send("buat web toko sepatu di folder namanya sepatuu") is True

    assert session._thread is None
    assert not (tmp_path / "sepatuu").exists()
    assert any("suggest mode" in e.get("text", "") for e in session.events_since(0))


def test_unknown_text_tool_json_is_retried_not_rendered_as_action(tmp_path):
    fake = '```json\n{"name":"build_web_app","arguments":{"output_folder":"web-app"}}\n```'
    real = (
        '```json\n'
        '{"name":"write_file","arguments":{"path":"web-app/index.html","content":"<h1>OK</h1>"}}\n'
        '```'
    )
    session = WebSession(_settings(), llm=FakeLLM([_resp(fake), _resp(real), _resp("done")]))

    assert session.send("buat web toko") is True
    session._thread.join(timeout=30)

    events = session.events_since(0)
    assert (tmp_path / "web-app" / "index.html").read_text() == "<h1>OK</h1>"
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"
    assert not any(e["kind"] == "text" and "build_web_app" in e["text"] for e in events)


def test_repeated_unknown_text_tool_json_is_marked_as_error():
    fake = '```json\n{"name":"build_web_app","arguments":{"output_folder":"web-app"}}\n```'
    session = WebSession(_settings(), llm=FakeLLM([_resp(fake), _resp(fake), _resp(fake)]))

    assert session.send("buat web toko") is True
    session._thread.join(timeout=30)

    events = session.events_since(0)
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "error"
    assert "fake tool call" in summary["text"]
    assert not any(e["kind"] == "text" and "build_web_app" in e["text"] for e in events)


def test_text_tool_json_executes_in_web_session(tmp_path):
    fake = (
        '```json\n'
        '{"name":"write_file","arguments":{"path":"mandarin/index.html","content":"<h1>你好</h1>"}}\n'
        '```'
    )
    session = WebSession(_settings(), llm=FakeLLM([_resp(fake), _resp("done")]))

    assert session.send("ubah file mandarin/index.html jadi halaman sederhana") is True
    session._thread.join(timeout=30)

    events = session.events_since(0)
    assert (tmp_path / "mandarin" / "index.html").read_text() == "<h1>你好</h1>"
    assert any(e["kind"] == "tool" and "mandarin/index.html" in e["summary"] for e in events)
    assert not any(e["kind"] == "text" and "write_file" in e["text"] for e in events)
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"


def test_web_retries_frontend_task_when_model_only_gives_tutorial(tmp_path):
    from relaycli.llm import ToolCall

    def tc(name, args, call_id):
        return ToolCall(id=call_id, name=name, arguments=json.dumps(args))

    session = WebSession(
        _settings(),
        llm=FakeLLM([
            LLMResponse(text="", tool_calls=[tc("create_folder", {"path": "toko laptop"}, "c1")], usage=Usage(total_tokens=8)),
            _resp("Here are the steps to create a website with HTML, CSS, and JavaScript."),
            LLMResponse(text="", tool_calls=[tc("write_file", {
                "path": "toko laptop/index.html",
                "content": "<h1>Toko Laptop</h1>",
            }, "c2")], usage=Usage(total_tokens=8)),
            _resp("done"),
        ]),
    )

    assert session.send('buatin website toko laptop di folder "toko laptop"') is True
    session._thread.join(timeout=30)

    assert (tmp_path / "toko laptop" / "index.html").read_text() == "<h1>Toko Laptop</h1>"
    summary = [e for e in session.events_since(0) if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"


def test_web_discards_fake_done_claim_before_recovery(tmp_path):
    from relaycli.llm import ToolCall

    def tc(name, args, call_id):
        return ToolCall(id=call_id, name=name, arguments=json.dumps(args))

    claim = "I created the marketplace website. Open index.html to preview it."
    session = WebSession(
        _settings(),
        llm=FakeLLM([
            _resp(claim),
            LLMResponse(text="", tool_calls=[tc("write_file", {
                "path": "marketplace-shopee/index.html",
                "content": "<h1>Marketplace</h1>",
            }, "c1")], usage=Usage(total_tokens=8)),
            _resp("done"),
        ]),
    )

    assert session.send('buat website marketplace di folder "marketplace-shopee"') is True
    session._thread.join(timeout=30)

    events = session.events_since(0)
    assert (tmp_path / "marketplace-shopee" / "index.html").read_text() == (
        "<h1>Marketplace</h1>"
    )
    assert not any(e["kind"] == "text" and claim in e["text"] for e in events)
    assert any(e["kind"] == "tool" and "marketplace-shopee/index.html" in e["summary"] for e in events)
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"


def test_send_slow_local_model_returns_fast_error(monkeypatch):
    import relaycli.web as web_mod

    monkeypatch.setattr(web_mod, "slow_local_model_warning", lambda model: "slow local model")
    monkeypatch.setattr(web_mod, "recommended_fast_local_model", lambda settings: None)
    session = WebSession(
        _settings(model="ollama_chat/qwen3:4b"),
        llm=FakeLLM([_resp("should not run")]),
    )

    assert session.send("jelasin repo ini") is True

    assert session._thread is None
    events = session.events_since(0)
    assert any(e["kind"] == "error" and "slow local model" in e["text"] for e in events)
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "error"


def test_send_slow_local_model_auto_switches_to_fast_model(monkeypatch):
    import relaycli.web as web_mod

    monkeypatch.setattr(web_mod, "slow_local_model_warning", lambda model: "slow local model")
    monkeypatch.setattr(
        web_mod,
        "recommended_fast_local_model",
        lambda settings: "ollama_chat/qwen2.5-coder:0.5b",
    )
    session = WebSession(
        _settings(model="ollama_chat/qwen3:4b"),
        llm=FakeLLM([_resp("ok")]),
    )

    assert session.send("jelasin repo ini") is True
    session._thread.join(timeout=30)

    assert session.settings.model == "ollama_chat/qwen2.5-coder:0.5b"
    events = session.events_since(0)
    assert any(
        e["kind"] == "note" and "Model auto-switched:" in e["text"]
        for e in events
    )
    assert any(e["kind"] == "text" and e["text"] == "ok" for e in events)
    summary = [e for e in events if e["kind"] == "summary"][-1]
    assert summary["stopped"] == "done"


def test_send_respects_manually_selected_slow_model(monkeypatch):
    import relaycli.web as web_mod

    monkeypatch.setattr(web_mod, "slow_local_model_warning", lambda model: "slow local model")
    monkeypatch.setattr(
        web_mod,
        "recommended_fast_local_model",
        lambda settings: "ollama_chat/qwen2.5-coder:0.5b",
    )
    session = WebSession(
        _settings(model="ollama_chat/qwen2.5-coder:1.5b"),
        llm=FakeLLM([_resp("ok")]),
    )
    session.set_model("ollama_chat/qwen3:4b")

    assert session.send("jelasin repo ini") is True
    session._thread.join(timeout=30)

    assert session.settings.model == "ollama_chat/qwen3:4b"
    events = session.events_since(0)
    assert any(
        e["kind"] == "log" and "manual slow local model kept" in e["text"]
        for e in events
    )
    assert not any(
        e["kind"] == "note" and "Model auto-switched:" in e["text"]
        for e in events
    )
    assert any(e["kind"] == "text" and e["text"] == "ok" for e in events)


def test_send_warns_once_for_manually_selected_slow_model(monkeypatch):
    import relaycli.web as web_mod

    monkeypatch.setattr(web_mod, "slow_local_model_warning", lambda model: "slow local model")
    monkeypatch.setattr(
        web_mod,
        "recommended_fast_local_model",
        lambda settings: "ollama_chat/qwen2.5-coder:0.5b",
    )
    session = WebSession(
        _settings(model="ollama_chat/qwen2.5-coder:1.5b"),
        llm=FakeLLM([_resp("first"), _resp("second")]),
    )
    session.set_model("ollama_chat/qwen3:4b")

    assert session.send("jelasin repo ini") is True
    session._thread.join(timeout=30)
    assert session.send("jelasin lagi") is True
    session._thread.join(timeout=30)

    events = session.events_since(0)
    warnings = [
        e for e in events
        if e["kind"] == "log" and "manual slow local model kept" in e["text"]
    ]
    assert len(warnings) == 1


def test_send_permissive_followup_carries_previous_request():
    llm = RecordingLLM([
        _resp("I need clarification."),
        _resp("Created the shop."),
    ])
    session = WebSession(_settings(), llm=llm)

    first = "buatkan struktur catatan produk, di folder baru namanya shooooi"
    assert session.send(first) is True
    session._thread.join(timeout=30)

    assert session.send("apa aja, buat di folder baru ya") is True
    session._thread.join(timeout=30)

    second_user = llm.calls[-1][-1]["content"]
    assert "Original request:" in second_user
    assert first in second_user
    assert "reasonable defaults" in second_user
    assert "shooooi" in second_user
    assert any(
        e["kind"] == "note" and "continuing the previous request" in e["text"]
        for e in session.events_since(0)
    )


def test_pull_ollama_records_start_and_done(monkeypatch):
    monkeypatch.setattr("relaycli.web.pull_ollama_model", lambda settings, model: model)
    session = WebSession(_settings())

    ok, model = session.pull_ollama("qwen2.5-coder:0.5b")

    assert ok is True
    assert model == "qwen2.5-coder:0.5b"
    session._pull_thread.join(timeout=5)
    texts = [e["text"] for e in session.events_since(0) if e["kind"] == "note"]
    assert texts == [
        "Ollama pull started: qwen2.5-coder:0.5b",
        "Ollama model installed: qwen2.5-coder:0.5b",
    ]


def test_send_mode_override_and_suggest_note():
    session = WebSession(_settings(), llm=FakeLLM([_resp("ok")]))
    session.send("explain this repo", mode="suggest")
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
        assert "RelayCLI" in html and "Agents" in html and "Activity" in html
        assert 'id="activityList"' in html

        state = json.loads(urllib.request.urlopen(base + "/api/state", timeout=5).read())
        assert state["model"] == "fake/model"

        req = urllib.request.Request(
            base + "/api/send", method="POST",
            data=json.dumps({"text": "explain this repo", "mode": "full-auto"}).encode(),
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
    ui = UI_PATH.read_text(encoding="utf-8")
    for marker in ("projectBtn", "chatCollapse", "sideCollapse", "termLarger"):
        assert marker in ui
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
    from relaycli import appconfig

    session = WebSession(_settings())
    session.set_model("ollama_chat/llama3.1")
    assert session.settings.model == "ollama_chat/llama3.1"
    assert session.set_mode("auto-edit") is True
    assert session.settings.permission_mode is PermissionMode.auto_edit
    assert session.set_flag("tasks", True) is True
    assert session.settings.relay_split_tasks is True
    assert session.set_flag("explorer", True) is True
    assert session.settings.relay_explorer is True
    assert session.set_flag("bogus", True) is False

    cfg = appconfig.load_app_config()
    assert cfg._raw["model"] == "ollama_chat/llama3.1"
    assert cfg._raw["permission_mode"] == "auto-edit"
    assert cfg._raw["relay_split_tasks"] is True
    assert cfg._raw["relay_explorer"] is True


def test_set_project_changes_desktop_working_directory(tmp_path):
    session = WebSession(_settings())
    target = tmp_path / "project-two"
    target.mkdir()

    ok, path = session.set_project(str(target))

    assert ok is True
    assert path == str(target.resolve())
    assert session.state()["cwd"] == str(target.resolve())
    assert any("project directory changed" in e.get("text", "") for e in session.events_since(0))


def test_reset_clears_events_when_idle():
    session = WebSession(_settings())
    session.add("user", text="hi")
    assert session.reset() is True
    assert session.events_since(0) == []


def test_force_reset_clears_busy_run_and_mutes_late_events():
    import time as _time

    class SlowLLM(FakeLLM):
        def complete(self, *a, **kw):
            _time.sleep(0.2)
            return super().complete(*a, **kw)

    session = WebSession(_settings(), llm=SlowLLM([_resp("late output")]))
    assert session.send("explain this repo") is True
    assert session.reset(force=True) is True
    assert session.events_since(0) == []
    session._thread.join(timeout=5)
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
    session.send("run relay stop test")
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
        assert post("/api/mode", {"mode": "full-auto"})["mode"] == "full-auto"
        assert session.settings.permission_mode is PermissionMode.full_auto
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


# --- /desktop: background server -------------------------------------------
def test_serve_background_starts_and_serves(monkeypatch, tmp_path):
    import json
    import urllib.request

    monkeypatch.chdir(tmp_path)
    from relaycli.config import Settings
    from relaycli.web import serve_background

    server, url = serve_background(Settings(), port=0)
    try:
        with urllib.request.urlopen(f"{url}/api/state", timeout=10) as resp:
            state = json.loads(resp.read().decode())
        assert "model" in state
        assert url.startswith("http://127.0.0.1:")
    finally:
        server.shutdown()
        server.server_close()


def test_serve_background_port_busy_falls_back(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from relaycli.config import Settings
    from relaycli.web import serve_background

    s1, url1 = serve_background(Settings(), port=0)
    busy_port = int(url1.rsplit(":", 1)[1])
    s2, url2 = serve_background(Settings(), port=busy_port)
    try:
        assert url1 != url2  # second server picked an ephemeral port
    finally:
        for s in (s1, s2):
            s.shutdown()
            s.server_close()


def test_allow_hosts_extends_guard(monkeypatch, tmp_path):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    monkeypatch.chdir(tmp_path)
    from relaycli.config import Settings
    from relaycli.web import WebSession, make_handler

    session = WebSession(Settings())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(session, {"myhost.lan"})
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        # allowed extra Host passes
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/state", headers={"Host": "myhost.lan"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert json.loads(resp.read().decode())["model"]
        # anything else is still rejected (DNS rebinding)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/state", headers={"Host": "evil.com"}
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("evil host was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 421
    finally:
        server.shutdown()
        server.server_close()


# ── fixes: TOCTOU busy-check, MCP + auto-skills wiring on the web surface ──
def test_send_rejects_while_busy():
    session = WebSession(_settings(), llm=FakeLLM([_resp("ok")]))
    assert session.send("explain first task") is True
    assert session.send("explain second task") is False  # rejected while the first run is live
    session._thread.join(timeout=30)


def test_send_concurrent_calls_start_at_most_one_run():
    """Two near-simultaneous POST /api/send (double-click, two tabs) must not
    both start a run — the busy-check-and-start must be atomic."""
    import time as _time

    class SlowLLM(FakeLLM):
        def complete(self, *a, **kw):
            _time.sleep(0.2)
            return super().complete(*a, **kw)

    session = WebSession(_settings(), llm=SlowLLM([_resp("ok"), _resp("ok")]))
    results = []
    barrier = threading.Barrier(2)

    def call():
        barrier.wait(timeout=5)
        results.append(session.send("explain this repo"))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sorted(results) == [False, True]
    session._thread.join(timeout=30)
    user_events = [e for e in session.events_since(0) if e["kind"] == "user"]
    assert len(user_events) == 1  # only the winning call appended a request


def test_web_run_wires_mcp_tools(monkeypatch, tmp_path):
    import sys as _sys

    import relaycli.agent as agent_mod
    import relaycli.mcp as mcp_mod

    fake_server = str(
        __import__("pathlib").Path(__file__).parent / "fake_mcp_server.py"
    )
    monkeypatch.setattr(
        mcp_mod, "enabled_servers",
        lambda: {"fake": mcp_mod.MCPServerConfig(
            name="fake", command=[_sys.executable, fake_server]
        )},
    )
    captured = {}
    orig_init = agent_mod.Agent.__init__

    def capture_init(self, *a, **kw):
        captured["registry"] = kw.get("registry")
        return orig_init(self, *a, **kw)

    monkeypatch.setattr(agent_mod.Agent, "__init__", capture_init)
    try:
        session = WebSession(_settings(), llm=FakeLLM([_resp("ok")]))
        session.send("explain tools")
        session._thread.join(timeout=30)
        assert captured["registry"] is not None
        assert "mcp_fake_echo" in captured["registry"].names()
    finally:
        mcp_mod.shutdown_all()


def test_web_run_applies_auto_skills(monkeypatch):
    import relaycli.skills as skills_mod

    monkeypatch.setattr(skills_mod, "auto_match", lambda skills, text, **kw: ["debug"])
    session = WebSession(_settings(), llm=FakeLLM([_resp("ok")]))
    session.send("fix this crash")
    session._thread.join(timeout=30)
    notes = [e["text"] for e in session.events_since(0) if e["kind"] == "note"]
    assert any("auto-skill: debug" in n for n in notes)


def test_web_run_skills_auto_off_skips_matching(monkeypatch):
    import relaycli.skills as skills_mod

    called = []
    monkeypatch.setattr(
        skills_mod, "auto_match",
        lambda *a, **kw: called.append(1) or ["debug"],
    )
    session = WebSession(_settings(skills_auto=False), llm=FakeLLM([_resp("ok")]))
    session.send("fix this crash")
    session._thread.join(timeout=30)
    assert not called


def test_serve_prints_actual_bound_port_not_requested(monkeypatch, tmp_path, capsys):
    """`relaycli web --port 0` (pick any free port) must report the real
    bound port, not a literal 'http://127.0.0.1:0'."""
    import threading as _threading

    from rich.console import Console as _Console
    import relaycli.web as web_mod

    monkeypatch.chdir(tmp_path)
    printed = {}
    orig_console = web_mod.Console

    class CapturingConsole(_Console):
        def print(self, *args, **kwargs):
            printed["text"] = printed.get("text", "") + " ".join(str(a) for a in args)
            return super().print(*args, **kwargs)

    monkeypatch.setattr(web_mod, "Console", CapturingConsole)
    from relaycli.config import Settings

    t = _threading.Thread(
        target=web_mod.serve, args=(Settings(),), kwargs={"port": 0}, daemon=True
    )
    t.start()
    import time as _time
    deadline = _time.time() + 5
    while "text" not in printed and _time.time() < deadline:
        _time.sleep(0.05)
    assert "text" in printed, "serve() never printed its startup line"
    assert ":0" not in printed["text"].split("→")[-1].split()[0]
    assert "http://127.0.0.1:" in printed["text"]
