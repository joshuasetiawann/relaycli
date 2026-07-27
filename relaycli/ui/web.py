"""relaycli web — the desktop UI in a browser."""

from __future__ import annotations

import io
import json
import os
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rich.console import Console

from relaycli.core.config import PermissionMode, Settings
from relaycli.core.context import ProjectContext
from relaycli.core.llm import preflight_settings, ollama_models
from relaycli.ui.render import brief_tool_error, friendly_error_text, short_model_name

UI_PATH = Path(__file__).parent.parent / "web_ui.html"


def _open_browser(url: str) -> None:
    import webbrowser
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()


class WebReporter:
    def __init__(self, session: "WebSession", agent: str) -> None:
        self._session = session
        self._agent = agent
        self._buf: list[str] = []
        self._tool_started: dict[str, float] = {}

    def model_start(self, n: int, model: str) -> None:
        self._session.add("log", agent=self._agent, text=f"→ model step {n} · {model}", level="info")

    def model_end(self, n: int, model: str, tool_calls: int, has_text: bool, usage) -> None:
        detail = f"{tool_calls} tool call{'s' if tool_calls != 1 else ''}" if tool_calls else ("answer" if has_text else "empty response")
        self._session.add("log", agent=self._agent, text=f"← model {detail} · {usage.total_tokens} tok", level="ok")

    def model_error(self, n: int, model: str, error: Exception) -> None:
        self._session.add("log", agent=self._agent, text="← model error", level="bad")

    def assistant_token(self, text: str) -> None:
        self._buf.append(text)

    def assistant_end(self) -> None:
        text = "".join(self._buf).strip()
        self._buf.clear()
        if text:
            self._session.add("text", agent=self._agent, text=text)

    def assistant_discard(self) -> None:
        self._buf.clear()

    def tool_start(self, call) -> None:
        self._tool_started[call.id] = time.perf_counter()
        args = " ".join((call.arguments or "{}").split())
        if len(args) > 120:
            args = args[:119] + "…"
        detail = f"→ tool {call.name}" + (f" {args}" if args and args != "{}" else "")
        self._session.add("log", agent=self._agent, text=detail, level="info")

    def tool_end(self, call, result) -> None:
        ok = result is not None and result.ok
        summary = (result.summary if result is not None else "") or call.name
        started = self._tool_started.pop(call.id, None)
        if started is not None:
            summary = f"{summary} · {time.perf_counter() - started:.1f}s"
        self._session.add("tool", agent=self._agent, ok=ok, summary=summary)
        if result is not None and not result.ok and result.output:
            self._session.add("error", agent=self._agent, text=brief_tool_error(result.output))

    def iteration(self, n: int) -> None:
        pass

    def close(self) -> None:
        self.assistant_end()


class WebObserver:
    def __init__(self, session: "WebSession") -> None:
        self._session = session

    def role_start(self, role, model: str, cycle: int) -> None:
        self._session.add("role", agent=str(role), model=short_model_name(model), cycle=cycle)

    def reporter_for(self, role) -> WebReporter:
        return WebReporter(self._session, str(role))


class WebSession:
    def __init__(self, settings: Settings, *, llm=None) -> None:
        self.settings = settings
        self.project = ProjectContext(Path.cwd())
        self._llm = llm
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pull_thread: threading.Thread | None = None
        self._muted_threads: set[int] = set()
        self._stop = threading.Event()
        self._manual_model_selected = False
        self._manual_slow_warning_shown_for: str | None = None

    def add(self, kind: str, **data) -> None:
        ident = threading.current_thread().ident
        with self._lock:
            if ident in self._muted_threads:
                return
            self._events.append({"n": len(self._events), "kind": kind, **data})

    def events_since(self, n: int) -> list[dict]:
        with self._lock:
            return self._events[n:]

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def pulling(self) -> bool:
        return self._pull_thread is not None and self._pull_thread.is_alive()

    def state(self) -> dict:
        from relaycli import __version__
        from relaycli.agent.router import Role, resolve_model, role_enabled
        from relaycli.model_catalog import model_choices
        from relaycli.slash import command_payload
        from relaycli.skills import discover_skills
        from relaycli.mcp.bridge import server_status
        from relaycli.config.manager import CONFIG_FILE

        s = self.settings
        roles = [{"name": str(role), "enabled": role_enabled(s, role), "model": short_model_name(resolve_model(s, role))} for role in Role]
        return {
            "version": __version__, "cwd": str(self.project.root), "project": self.project.root.name,
            "config_file": str(CONFIG_FILE), "model": s.model, "model_short": short_model_name(s.model),
            "models": model_choices(self.settings, current=self.settings.model),
            "mode": str(s.permission_mode), "relay": s.relay_enabled, "explorer": s.relay_explorer,
            "tester": s.relay_tester, "local_scaffolds": s.local_scaffolds, "tasks": s.relay_split_tasks,
            "split_tasks": s.relay_split_tasks, "roles": roles,
            "role_models": [{"role": r, "enabled": role_enabled(s, Role(r)), "assigned": getattr(s, f"{r}_model"), "resolved": short_model_name(resolve_model(s, Role(r)))} for r in ("explorer", "planner", "coder", "tester", "reviewer")],
            "providers": self._provider_status(), "onboarding": self._onboarding_status(),
            "skills": sorted(discover_skills(self.project.root)), "commands": command_payload(),
            "mcp": server_status(), "preflight": preflight_settings(s), "busy": self.busy, "ollama_pulling": self.pulling,
        }

    PROVIDERS = (
        ("openai", "OpenAI"), ("anthropic", "Anthropic"), ("gemini", "Gemini"),
        ("deepseek", "DeepSeek"), ("dashscope", "Qwen · DashScope"), ("zhipu", "GLM · Zhipu"),
        ("groq", "Groq"), ("mistral", "Mistral"), ("openrouter", "OpenRouter"),
    )

    def _provider_status(self) -> list[dict]:
        out = []
        for pid, label in self.PROVIDERS:
            from relaycli.model_catalog import provider_key
            detected = bool(provider_key(self.settings, pid))
            env = f"{pid.upper()}_API_KEY"
            out.append({"id": pid, "label": label, "env": env, "detected": detected})
        installed = ollama_models(self.settings)
        out.append({"id": "ollama", "label": "Ollama", "env": "OLLAMA_BASE_URL", "detected": bool(installed), "detail": f"{len(installed)} local model(s)" if installed else "not reachable"})
        return out

    def _onboarding_status(self) -> dict:
        from relaycli.core.llm import best_ollama_model, ollama_host_label, tool_capability_warning
        local = best_ollama_model(self.settings)
        return {"preflight": preflight_settings(self.settings), "ollama_host": ollama_host_label(self.settings),
                "ollama_model": local, "tool_warning": tool_capability_warning(self.settings.model), "ready": preflight_settings(self.settings) is None}

    def set_model(self, model: str, *, manual: bool = True) -> None:
        model = (model or "").strip()
        if model:
            self.settings.model = model
            if manual:
                self._manual_model_selected = True
                self._manual_slow_warning_shown_for = None
            try:
                from relaycli.appconfig import set_base_model
                set_base_model(model)
            except Exception:
                pass

    def set_mode(self, mode: str) -> bool:
        try:
            value = PermissionMode(mode)
        except ValueError:
            return False
        self.settings.permission_mode = value
        try:
            from relaycli.appconfig import set_runtime_option
            set_runtime_option("permission_mode", str(value))
        except Exception:
            pass
        return True

    def set_flag(self, name: str, value: bool) -> bool:
        allowed = {"relay": "relay_enabled", "explorer": "relay_explorer", "tester": "relay_tester", "tasks": "relay_split_tasks"}
        field = allowed.get(name)
        if field is None:
            return False
        setattr(self.settings, field, bool(value))
        try:
            from relaycli.appconfig import set_runtime_option
            set_runtime_option(field, bool(value))
        except Exception:
            pass
        return True

    def set_project(self, path: str) -> tuple[bool, str]:
        raw = (path or "").strip()
        if not raw:
            return False, "Project path required."
        if self.busy:
            return False, "Wait for the current run to finish before changing project."
        candidate = Path(raw).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            return False, f"Could not resolve project path: {exc}"
        if not resolved.is_dir():
            return False, f"Not a directory: {resolved}"
        self.project = ProjectContext(resolved)
        self.add("note", text=f"project directory changed: {self.project.root}")
        return True, str(self.project.root)

    def set_role_model(self, role: str, model: str) -> bool:
        roles = ("explorer", "planner", "coder", "tester", "reviewer")
        if role not in roles:
            return False
        setattr(self.settings, f"{role}_model", model.strip() or None)
        return True

    def set_key(self, provider: str, key: str) -> bool:
        key = (key or "").strip()
        env_key = f"{provider.upper()}_API_KEY"
        if key:
            os.environ[env_key] = key
        else:
            os.environ.pop(env_key, None)
        try:
            from relaycli.appconfig import ProviderConfig, load_app_config, save_app_config
            cfg = load_app_config()
            pc = cfg.providers.get(provider) or ProviderConfig()
            pc.api_key = key or None
            cfg.providers[provider] = pc
            save_app_config(cfg)
        except Exception:
            pass
        return True

    def send(self, text: str, mode: str | None = None) -> bool:
        from relaycli.intent import continuation_for, local_reply_for

        with self._lock:
            if self.busy:
                return False
            if mode:
                self.set_mode(mode)
            previous = self._last_actionable_user_text()
            run_text = continuation_for(text, previous) or text
            self._stop.clear()
            self._events.append({"n": len(self._events), "kind": "user", "text": text})
            reply = None if run_text != text else local_reply_for(text)
            if reply is not None:
                self._events.append({"n": len(self._events), "kind": "guide", "agent": "guide", "text": reply.text, "reason": reply.reason})
                self._events.append({"n": len(self._events), "kind": "summary", "stopped": "done", "verdict": None, "cycles": 0, "tasks": [], "tokens": 0, "cost": 0.0, "elapsed": 0.0, "text": ""})
                return True
            if run_text != text:
                self._events.append({"n": len(self._events), "kind": "note", "text": "continuing the previous request with your follow-up"})
            self._thread = threading.Thread(target=self._run, args=(run_text,), daemon=True)
            self._thread.start()
        return True

    def _last_actionable_user_text(self) -> str | None:
        from relaycli.intent import local_reply_for
        for event in reversed(self._events):
            if event.get("kind") != "user":
                continue
            text = (event.get("text") or "").strip()
            if text and local_reply_for(text) is None:
                return text
        return None

    def _run(self, text: str) -> None:
        from relaycli.agent.loop import Agent
        from relaycli.agent.pipeline import Relay
        from relaycli.core.permissions import PermissionManager
        from relaycli.mcp.bridge import extend_registry
        from relaycli.tools.registry import default_registry

        console = Console(file=io.StringIO(), force_terminal=False, width=100)
        permissions = PermissionManager(self.settings.permission_mode, prompter=lambda *a, **k: False, console=console)
        skills_block = ""
        if self.settings.skills_auto:
            from relaycli.skills import auto_match, discover_skills, skills_prompt_block
            skills = discover_skills(self.project.root)
            names = auto_match(skills, text)
            if names:
                self.add("note", text="auto-skill: " + ", ".join(names))
            skills_block = skills_prompt_block([skills[n] for n in names])
        ident = threading.current_thread().ident
        try:
            if self.settings.relay_enabled:
                relay = Relay(self.settings, console=console, project=self.project, permissions=permissions, should_stop=self._stop.is_set, skills_block=skills_block, **(self._llm and {"llm": self._llm} or {}))
                result = relay.run(text, observer=WebObserver(self))
                self.add("summary", stopped=result.stopped_reason, verdict=result.verdict, cycles=result.cycles, tasks=result.tasks, tokens=result.usage.total_tokens, cost=result.usage.cost_usd, elapsed=round(result.elapsed, 1), text=(friendly_error_text(result.final_text) if result.stopped_reason != "done" else ""))
            else:
                agent = Agent(self.settings, console=console, project=self.project, permissions=permissions, should_stop=self._stop.is_set, registry=extend_registry(default_registry(), console=console), skills_block=skills_block, **(self._llm and {"llm": self._llm} or {}))
                reporter = WebReporter(self, "agent")
                try:
                    result = agent.run(text, reporter=reporter)
                finally:
                    reporter.close()
                self.add("summary", stopped=result.stopped_reason, verdict=None, cycles=0, tasks=[], tokens=result.usage.total_tokens, cost=result.usage.cost_usd, elapsed=round(result.elapsed, 1), text=(friendly_error_text(result.final_text) if result.stopped_reason != "done" else ""))
        except Exception as exc:
            self.add("error", text=f"{type(exc).__name__}: {exc}")
        finally:
            if ident is not None:
                with self._lock:
                    self._muted_threads.discard(ident)

    def stop(self) -> None:
        self._stop.set()

    def reset(self, *, force: bool = False) -> bool:
        with self._lock:
            if self.busy:
                if not force:
                    return False
                self._stop.set()
                if self._thread and self._thread.ident is not None:
                    self._muted_threads.add(self._thread.ident)
            self._events.clear()
        return True


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def make_handler(session: WebSession, allowed_hosts: set[str] | None = None):
    allowed = _LOOPBACK_HOSTS | {h.lower() for h in (allowed_hosts or set())}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _host_ok(self) -> bool:
            host = urlparse(f"//{self.headers.get('Host') or ''}").hostname
            return host in allowed

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlparse(origin).hostname in allowed

        def _json(self, obj, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._host_ok():
                self._json({"error": "bad host"}, status=421)
                return
            url = urlparse(self.path)
            if url.path == "/":
                body = UI_PATH.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/state":
                self._json(session.state())
            elif url.path == "/api/events":
                since = int((parse_qs(url.query).get("since") or ["0"])[0])
                self._json({"events": session.events_since(since), "busy": session.busy})
            else:
                self._json({"error": "not found"}, status=404)

        def do_POST(self) -> None:
            if not self._host_ok():
                self._json({"error": "bad host"}, status=421)
                return
            if not self._origin_ok():
                self._json({"error": "cross-origin request rejected"}, status=403)
                return
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, status=400)
                return

            if path == "/api/stop":
                session.stop()
                self._json({"ok": True})
            elif path == "/api/reset":
                self._json({"ok": session.reset(force=bool(data.get("force")))})
            elif path == "/api/model":
                session.set_model(data.get("model") or "")
                self._json({"ok": True, "model": session.settings.model})
            elif path == "/api/mode":
                ok = session.set_mode(data.get("mode") or "")
                self._json({"ok": ok, "mode": str(session.settings.permission_mode)}, status=200 if ok else 400)
            elif path == "/api/flag":
                ok = session.set_flag(data.get("name") or "", bool(data.get("on")))
                self._json({"ok": ok}, status=200 if ok else 400)
            elif path == "/api/project":
                ok, message = session.set_project(data.get("path") or "")
                self._json({"ok": ok, "path": message} if ok else {"error": message}, status=200 if ok else 400)
            elif path == "/api/role-model":
                ok = session.set_role_model(data.get("role") or "", data.get("model") or "")
                self._json({"ok": ok}, status=200 if ok else 400)
            elif path == "/api/key":
                ok = session.set_key(data.get("provider") or "", data.get("key") or "")
                self._json({"ok": ok}, status=200 if ok else 400)
            elif path == "/api/send":
                text = (data.get("text") or "").strip()
                if not text:
                    self._json({"error": "empty message"}, status=400)
                    return
                if not session.send(text, data.get("mode")):
                    self._json({"error": "a run is already in progress"}, status=409)
                    return
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, status=404)

    return Handler


def serve(settings: Settings, port: int = 8484, *, open_browser: bool = False, host: str = "127.0.0.1", allow_hosts: set[str] | None = None) -> None:
    session = WebSession(settings)
    server = ThreadingHTTPServer((host, port), make_handler(session, allow_hosts))
    console = Console()
    bound_port = server.server_address[1]
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{bound_port}"
    scope = "loopback only" if host in _LOOPBACK_HOSTS else f"bound to {host}"
    console.print(f"[bold]RelayCLI desktop[/bold] → [cyan]{url}[/cyan]  [dim]({scope} · Ctrl-C to stop)[/dim]")
    if host not in _LOOPBACK_HOSTS:
        console.print("[bold yellow]⚠ non-loopback bind:[/bold yellow] anyone who can reach this port controls an agent with YOUR permissions — use only on trusted networks (or keep the container port mapped to 127.0.0.1).")
    if open_browser:
        _open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]bye.[/dim]")
    finally:
        server.server_close()


def serve_background(settings: Settings, port: int = 8484) -> tuple[ThreadingHTTPServer, str]:
    import threading
    session = WebSession(settings)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(session))
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, url
