# RelayCLI — Task decomposition + web desktop (design)

Date: 2026-07-03
Status: approved for implementation (user: "ide masing-masing agent ada
tugasnya diterapkan; berikan relaycli desktop yang jalan di web, file
design ada di lokal"). Design source: `~/Downloads/RelayCLI desktop app
design.zip` → RelayCLI.dc.html (1440×900 dark desktop frame, accent
#2D5BFF, Geist + JetBrains Mono; left chat, center agent graph, right
run/agents panel, bottom terminal drawer). One commit per task.

## 1. Task decomposition — each agent owns a task

- `relay_split_tasks: bool = False` (opt-in via /agents tasks on|off).
- When on: the Planner's numbered plan is parsed into tasks
  (`^\s*\d+[.)]` lines, capped at 6; the final verification step stays
  with the Tester/Reviewer). A FRESH coder agent runs per task —
  "Task i/N: <step>" + the full plan + previous task reports — so each
  task gets a clean context window (the design's orchestrator chat:
  "Decomposed the goal into N tasks and routed them to the team").
- Tester/Reviewer run once at the end over the combined work; revise
  cycles rerun a single fixer coder with the feedback, not the whole
  task fan-out. Unparseable plan (no numbered lines) → note + classic
  single-coder path.
- RelayResult gains `tasks: list[str]`; observers see role_start per
  task (cycle = task index) — the CLI summary shows task count.

## 2. `relaycli web` — the desktop in a browser

- New command `relaycli web [--port 8484]`: stdlib ThreadingHTTPServer,
  no new dependencies.
  - `GET /` → single-file UI (relaycli/web_ui.html) rebuilt faithfully
    from the local design: same tokens (#0B0B0E surfaces, #24252B
    borders, #2D5BFF accent, JetBrains Mono), top bar (mode toggle,
    model, live dot), left chat, right run panel with per-agent cards,
    bottom terminal log. The center graph is simplified to the agent
    cards + log (out of scope: animated SVG edges).
  - `GET /api/state` → model, mode, relay, roles, skills, cwd, version.
  - `POST /api/send` {text, mode} → one run per request on a worker
    thread (409 while busy); a Web reporter/observer records events.
  - `GET /api/events?since=N` → incremental JSON event log (UI polls);
    events: user, role_start, text, tool, note, summary, error.
- Loopback only (bind 127.0.0.1) — the page can edit files and run
  commands with the user's account; no auth story yet, so never bind
  0.0.0.0. Permission prompts cannot block a web run: suggest-mode
  confirms auto-decline (the UI mode toggle exists precisely for this;
  auto-edit is the web default).

## Out of scope (follow-ups)

Animated SVG agent graph, websockets/SSE, parallel task execution,
multi-session web tabs, auth for non-loopback binds, /init project
memory (next parity item).

## Tests

Task split: plan parsing (caps, unparseable), per-task coder runs with
fresh sessions + task text in requests, revise path uses single fixer,
result.tasks populated. Web: state endpoint JSON, send→events flow with
a scripted LLM (thread joined), busy 409, loopback bind. pty/curl at
the end.
