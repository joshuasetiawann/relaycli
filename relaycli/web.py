"""relaycli web — the desktop UI in a browser.

A stdlib-only HTTP server (no new dependencies) that serves the single-file
UI (``web_ui.html``, rebuilt from the user's local design) and a tiny JSON
API the page polls:

* ``GET  /``                 → the UI
* ``GET  /api/state``        → model, mode, relay, roles, skills, cwd, version
* ``POST /api/send``         → run one request on a worker thread (409 if busy)
* ``GET  /api/events?since`` → incremental event log (user/role/text/tool/…)

SECURITY: binds 127.0.0.1 ONLY. The page can edit files and run commands
with the user's account and there is no auth story — never bind 0.0.0.0.
Loopback alone does not stop the browser being used against us, so the
handler also rejects non-loopback Host headers (DNS rebinding) and
cross-origin POSTs (a text/plain form crosses origins with no preflight).
Interactive permission prompts cannot block a web run, so a prompter that
declines is installed: in suggest mode writes/commands are refused with a
note (the UI's mode toggle exists precisely for this; auto-edit is the
web default).
"""

from __future__ import annotations

import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rich.console import Console

from relaycli.config import PermissionMode, Settings
from relaycli.context import ProjectContext
from relaycli.intent import continuation_for, local_reply_for
from relaycli.llm import preflight_settings
from relaycli.model_catalog import (
    detected_ollama_models,
    model_choices,
    provider_key,
    pull_ollama_model,
)
from relaycli.render import friendly_error_text, short_model_name
from relaycli.router import Role, resolve_model, role_enabled

UI_PATH = Path(__file__).parent / "web_ui.html"


class WebReporter:
    """Reporter protocol → session events (one per assistant block / tool)."""

    def __init__(self, session: "WebSession", agent: str) -> None:
        self._session = session
        self._agent = agent
        self._buf: list[str] = []

    def assistant_token(self, text: str) -> None:
        self._buf.append(text)

    def assistant_end(self) -> None:
        text = "".join(self._buf).strip()
        self._buf.clear()
        if text:
            self._session.add("text", agent=self._agent, text=text)

    def tool_start(self, call) -> None: ...

    def tool_end(self, call, result) -> None:
        ok = result is not None and result.ok
        summary = (result.summary if result is not None else "") or call.name
        self._session.add("tool", agent=self._agent, ok=ok, summary=summary)

    def iteration(self, n: int) -> None: ...

    def close(self) -> None:
        self.assistant_end()


class WebObserver:
    """RelayObserver protocol → session events, one WebReporter per role."""

    def __init__(self, session: "WebSession") -> None:
        self._session = session

    def role_start(self, role, model: str, cycle: int) -> None:
        self._session.add("role", agent=str(role), model=short_model_name(model),
                          cycle=cycle)

    def reporter_for(self, role) -> WebReporter:
        return WebReporter(self._session, str(role))


class WebSession:
    """One browser session's state: the event log and the single worker."""

    def __init__(self, settings: Settings, *, llm=None) -> None:
        self.settings = settings
        self.project = ProjectContext(Path.cwd())
        self._llm = llm  # injection seam for tests
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pull_thread: threading.Thread | None = None
        self._muted_threads: set[int] = set()
        self._stop = threading.Event()

    # -- events ------------------------------------------------------------
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

    # -- state -------------------------------------------------------------
    def state(self) -> dict:
        from relaycli import __version__
        from relaycli.config import CONFIG_FILE
        from relaycli.skills import discover_skills

        s = self.settings
        roles = [
            {
                "name": str(role),
                "enabled": role_enabled(s, role),
                "model": short_model_name(resolve_model(s, role)),
            }
            for role in Role
        ]
        return {
            "version": __version__,
            "cwd": str(self.project.root),
            "project": self.project.root.name,
            "config_file": str(CONFIG_FILE),
            "model": s.model,
            "model_short": short_model_name(s.model),
            "models": self._model_choices(),
            "mode": str(s.permission_mode),
            "relay": s.relay_enabled,
            "explorer": s.relay_explorer,
            "tester": s.relay_tester,
            # "tasks" mirrors the /api/flag name the settings toggle uses;
            # "split_tasks" is kept for any other consumer.
            "tasks": s.relay_split_tasks,
            "split_tasks": s.relay_split_tasks,
            "roles": roles,
            "role_models": [
                {
                    "role": r,
                    "enabled": role_enabled(s, Role(r)),
                    "assigned": getattr(s, f"{r}_model"),
                    "resolved": short_model_name(resolve_model(s, Role(r))),
                }
                for r in self.ROLES
            ],
            "providers": self._provider_status(),
            "onboarding": self._onboarding_status(),
            # Roster specialists a task can be delegated to in task-split mode.
            "specialists": self._enabled_specialists(),
            # The full 16-role roster (from config.toml) for the Configuration
            # panel: each role's enabled state, assignment, and resolved model.
            "roster": self._roster(),
            "skills": sorted(discover_skills(self.project.root)),
            "mcp": self._mcp_status(),
            "preflight": preflight_settings(s),
            "busy": self.busy,
            "ollama_pulling": self.pulling,
        }

    def _mcp_status(self) -> list[dict]:
        from relaycli.mcp import server_status

        return server_status()

    def _enabled_specialists(self) -> list[str]:
        from relaycli.roster import enabled_specialists

        return enabled_specialists()

    def _roster(self) -> list[dict]:
        from relaycli.appconfig import effective_roles, load_app_config

        return [
            {"id": r.id, "name": r.display_name, "enabled": r.enabled,
             "assigned": r.assigned, "model": r.model}
            for r in effective_roles(load_app_config())
        ]

    def set_roster(self, role: str, enabled=None, model=None) -> bool:
        """Enable/disable a roster role and/or assign its model (persisted)."""
        from relaycli.appconfig import RoleConfig, load_app_config, save_app_config
        from relaycli.roles import builtin_role

        if builtin_role(role) is None:
            return False
        cfg = load_app_config()
        rc = cfg.roles.get(role) or RoleConfig()
        if enabled is not None:
            rc.enabled = bool(enabled)
        if model is not None:
            rc.model = (model.strip() or None)
        cfg.roles[role] = rc
        save_app_config(cfg)
        return True

    def _model_choices(self) -> list[dict]:
        return model_choices(self.settings, current=self.settings.model)

    def set_model(self, model: str) -> None:
        model = (model or "").strip()
        if model:
            self.settings.model = model
            from relaycli.appconfig import set_base_model

            set_base_model(model)

    def set_flag(self, name: str, value: bool) -> bool:
        """Toggle a boolean session setting from the UI. Returns True if known."""
        allowed = {
            "relay": "relay_enabled", "explorer": "relay_explorer",
            "tester": "relay_tester", "tasks": "relay_split_tasks",
        }
        field = allowed.get(name)
        if field is None:
            return False
        setattr(self.settings, field, bool(value))
        return True

    # Each relay role can run a different specialist model. "" clears the
    # override so the role falls back to the base model.
    ROLES = ("explorer", "planner", "coder", "tester", "reviewer")

    def set_role_model(self, role: str, model: str) -> bool:
        if role not in self.ROLES:
            return False
        setattr(self.settings, f"{role}_model", model.strip() or None)
        return True

    # Provider credentials the UI can set at runtime. attr = the Settings
    # field LiteLLM reads directly (6 managed providers); env = the variable
    # LiteLLM reads itself for the rest (DeepSeek / Qwen / GLM).
    PROVIDERS = (
        ("openai", "OpenAI", "openai_api_key", "OPENAI_API_KEY"),
        ("anthropic", "Anthropic", "anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("gemini", "Gemini", "gemini_api_key", "GEMINI_API_KEY"),
        ("deepseek", "DeepSeek", None, "DEEPSEEK_API_KEY"),
        ("dashscope", "Qwen · DashScope", None, "DASHSCOPE_API_KEY"),
        ("zhipu", "GLM · Zhipu", None, "ZHIPUAI_API_KEY"),
        ("groq", "Groq", "groq_api_key", "GROQ_API_KEY"),
        ("mistral", "Mistral", "mistral_api_key", "MISTRAL_API_KEY"),
        ("openrouter", "OpenRouter", "openrouter_api_key", "OPENROUTER_API_KEY"),
    )

    def set_key(self, provider: str, key: str) -> bool:
        key = (key or "").strip()
        for pid, _label, attr, env in self.PROVIDERS:
            if pid != provider:
                continue
            if attr is not None:
                setattr(self.settings, attr, key or None)
            if key:
                os.environ[env] = key
            else:
                os.environ.pop(env, None)
            from relaycli.appconfig import ProviderConfig, load_app_config, save_app_config

            cfg = load_app_config()
            pc = cfg.providers.get(pid) or ProviderConfig()
            pc.api_key = key or None
            cfg.providers[pid] = pc
            save_app_config(cfg)
            return True
        return False

    def _provider_status(self) -> list[dict]:
        out = []
        for pid, label, attr, env in self.PROVIDERS:
            detected = bool(provider_key(self.settings, pid))
            out.append({"id": pid, "label": label, "env": env, "detected": detected})
        ollama = detected_ollama_models(self.settings)
        out.append({
            "id": "ollama", "label": "Ollama", "env": "OLLAMA_BASE_URL",
            "detected": bool(ollama), "detail": f"{len(ollama)} local model(s)" if ollama else "not reachable",
        })
        return out

    def pull_ollama(self, model: str) -> tuple[bool, str]:
        model = (model or "").strip()
        if not model:
            return False, "Ollama model name required."
        with self._lock:
            if self.pulling:
                return False, "an Ollama pull is already running"
            self._events.append({
                "n": len(self._events),
                "kind": "note",
                "text": f"Ollama pull started: {model}",
            })
            self._pull_thread = threading.Thread(
                target=self._pull_ollama_worker, args=(model,), daemon=True
            )
            self._pull_thread.start()
        return True, model

    def _pull_ollama_worker(self, model: str) -> None:
        try:
            name = pull_ollama_model(self.settings, model)
            self.add("note", text=f"Ollama model installed: {name}")
        except Exception as exc:
            self.add("error", text=f"Ollama pull failed: {exc}")

    def _onboarding_status(self) -> dict:
        from relaycli.llm import best_ollama_model, ollama_host_label, tool_capability_warning

        local = best_ollama_model(self.settings)
        preflight = preflight_settings(self.settings)
        return {
            "preflight": preflight,
            "ollama_host": ollama_host_label(self.settings),
            "ollama_model": local,
            "tool_warning": tool_capability_warning(self.settings.model),
            "ready": preflight is None,
        }

    def stop(self) -> None:
        """Ask the in-flight run to halt after its current step (idempotent)."""
        self._stop.set()

    def reset(self, *, force: bool = False) -> bool:
        """Clear the event log for a new chat.

        A force reset asks the current run to stop and mutes that worker's
        late events so the old run cannot spill back into the fresh chat view.
        """
        with self._lock:
            if self.busy:
                if not force:
                    return False
                self._stop.set()
                if self._thread and self._thread.ident is not None:
                    self._muted_threads.add(self._thread.ident)
            self._events.clear()
        return True

    # -- running -----------------------------------------------------------
    def send(self, text: str, mode: str | None = None) -> bool:
        """Start one run; False when a run is already in flight.

        The busy-check and thread creation happen under ``self._lock`` so two
        near-simultaneous requests (double-click, two tabs) can't both pass
        the check and start two concurrent runs.
        """
        with self._lock:
            if self.busy:
                return False
            if mode:
                try:
                    self.settings.permission_mode = PermissionMode(mode)
                except ValueError:
                    pass
            previous = self._last_actionable_user_text()
            run_text = continuation_for(text, previous) or text
            self._stop.clear()
            self._events.append({"n": len(self._events), "kind": "user", "text": text})
            reply = None if run_text != text else local_reply_for(text)
            if reply is not None:
                self._events.append({
                    "n": len(self._events),
                    "kind": "guide",
                    "agent": "guide",
                    "text": reply.text,
                    "reason": reply.reason,
                })
                self._events.append({
                    "n": len(self._events),
                    "kind": "summary",
                    "stopped": "done",
                    "verdict": None,
                    "cycles": 0,
                    "tasks": [],
                    "tokens": 0,
                    "cost": 0.0,
                    "elapsed": 0.0,
                    "text": "",
                })
                return True
            if run_text != text:
                self._events.append({
                    "n": len(self._events),
                    "kind": "note",
                    "text": "continuing the previous request with your follow-up",
                })
            self._thread = threading.Thread(target=self._run, args=(run_text,), daemon=True)
            self._thread.start()
        return True

    def _last_actionable_user_text(self) -> str | None:
        """Return the latest user request that was substantial enough to run."""

        for event in reversed(self._events):
            if event.get("kind") != "user":
                continue
            text = (event.get("text") or "").strip()
            if text and local_reply_for(text) is None:
                return text
        return None

    def _run(self, text: str) -> None:
        from relaycli.agent import Agent
        from relaycli.mcp import extend_registry
        from relaycli.permissions import PermissionManager
        from relaycli.relay import Relay
        from relaycli.tools import default_registry

        console = Console(file=io.StringIO(), force_terminal=False, width=100)
        # A web run cannot answer an interactive prompt: everything that
        # would ask, declines. The UI surfaces this as a note.
        permissions = PermissionManager(
            self.settings.permission_mode, prompter=lambda *a, **k: False,
            console=console,
        )
        if self.settings.permission_mode is PermissionMode.suggest:
            self.add("note", text=(
                "suggest mode declines every edit/command on the web — "
                "switch the mode toggle to auto-edit or full-auto"
            ))
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
                relay = Relay(self.settings, console=console, project=self.project,
                              permissions=permissions, should_stop=self._stop.is_set,
                              skills_block=skills_block,
                              **({"llm": self._llm} if self._llm else {}))
                result = relay.run(text, observer=WebObserver(self))
                self.add("summary", stopped=result.stopped_reason,
                         verdict=result.verdict, cycles=result.cycles,
                         tasks=result.tasks, tokens=result.usage.total_tokens,
                         cost=result.usage.cost_usd, elapsed=round(result.elapsed, 1),
                         text=(friendly_error_text(result.final_text)
                               if result.stopped_reason != "done" else ""))
            else:
                agent = Agent(self.settings, console=console, project=self.project,
                              permissions=permissions, should_stop=self._stop.is_set,
                              registry=extend_registry(default_registry(), console=console),
                              skills_block=skills_block,
                              **({"llm": self._llm} if self._llm else {}))
                reporter = WebReporter(self, "agent")
                try:
                    result = agent.run(text, reporter=reporter)
                finally:
                    reporter.close()
                self.add("summary", stopped=result.stopped_reason,
                         verdict=None, cycles=0, tasks=[],
                         tokens=result.usage.total_tokens,
                         cost=result.usage.cost_usd, elapsed=round(result.elapsed, 1),
                         text=(friendly_error_text(result.final_text)
                               if result.stopped_reason != "done" else ""))
        except Exception as exc:  # never kill the server thread silently
            self.add("error", text=f"{type(exc).__name__}: {exc}")
        finally:
            if ident is not None:
                with self._lock:
                    self._muted_threads.discard(ident)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def make_handler(session: WebSession, allowed_hosts: set[str] | None = None):
    # Docker/LAN deployments may allow extra hostnames explicitly
    # (`relaycli web --allow-host`); loopback is always allowed.
    allowed = _LOOPBACK_HOSTS | {h.lower() for h in (allowed_hosts or set())}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet server log
            pass

        # Loopback binding alone does not stop the BROWSER from being used
        # against us: a malicious site can DNS-rebind its domain to
        # 127.0.0.1 (Host: evil.com reaches us), and a text/plain form POST
        # crosses origins without a CORS preflight. Reject any Host that is
        # not a loopback literal (or explicitly allowed), and any
        # state-changing request whose Origin (when present) is not allowed.
        def _host_ok(self) -> bool:
            host = urlparse(f"//{self.headers.get('Host') or ''}").hostname
            return host in allowed

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True  # non-browser client (curl); Host is checked
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
                self._json({"events": session.events_since(since),
                            "busy": session.busy})
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
            elif path == "/api/flag":
                ok = session.set_flag(data.get("name") or "", bool(data.get("on")))
                self._json({"ok": ok}, status=200 if ok else 400)
            elif path == "/api/role-model":
                ok = session.set_role_model(data.get("role") or "", data.get("model") or "")
                self._json({"ok": ok}, status=200 if ok else 400)
            elif path == "/api/key":
                ok = session.set_key(data.get("provider") or "", data.get("key") or "")
                self._json({"ok": ok}, status=200 if ok else 400)
            elif path == "/api/roster":
                ok = session.set_roster(
                    data.get("role") or "", data.get("enabled"), data.get("model"))
                self._json({"ok": ok}, status=200 if ok else 400)
            elif path == "/api/ollama/pull":
                ok, message = session.pull_ollama(data.get("model") or "")
                self._json(
                    {"ok": ok, "model": message} if ok else {"error": message},
                    status=200 if ok else 409,
                )
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


def serve(
    settings: Settings,
    port: int = 8484,
    *,
    open_browser: bool = False,
    host: str = "127.0.0.1",
    allow_hosts: set[str] | None = None,
) -> None:
    """Serve the desktop UI until Ctrl-C (loopback by default)."""
    session = WebSession(settings)
    server = ThreadingHTTPServer((host, port), make_handler(session, allow_hosts))
    console = Console()
    # Read back the actual bound port, not the requested one — `--port 0`
    # (a standard "pick any free port" convention) would otherwise print a
    # useless "http://127.0.0.1:0".
    bound_port = server.server_address[1]
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{bound_port}"
    scope = "loopback only" if host in _LOOPBACK_HOSTS else f"bound to {host}"
    console.print(
        f"[bold]RelayCLI desktop[/bold] → [cyan]{url}[/cyan]  "
        f"[dim]({scope} · Ctrl-C to stop)[/dim]"
    )
    if host not in _LOOPBACK_HOSTS:
        console.print(
            "[bold yellow]⚠ non-loopback bind:[/bold yellow] anyone who can reach "
            "this port controls an agent with YOUR permissions — use only on "
            "trusted networks (or keep the container port mapped to 127.0.0.1)."
        )
    if open_browser:
        _open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]bye.[/dim]")
    finally:
        server.server_close()


def _open_browser(url: str) -> None:
    """Open ``url`` in the default browser without ever crashing the caller."""
    import threading
    import webbrowser

    # webbrowser.open can block on some launchers; a daemon thread keeps the
    # server (or REPL) responsive either way.
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()


def serve_background(settings: Settings, port: int = 8484) -> tuple[ThreadingHTTPServer, str]:
    """Start the desktop UI on a daemon thread; returns (server, url).

    Used by the REPL's /desktop so the terminal session stays usable while
    the browser UI runs. Binding failures (port busy) fall back to an
    ephemeral port rather than raising.
    """
    import threading

    session = WebSession(settings)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(session))
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, url
