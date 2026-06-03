# RelayCLI Production Maturity — Design

Date: 2026-07-03 · Status: approved-by-default (user AFK, pre-authorized autonomous decisions)

User request (paraphrased): the AI should know when to use which skill without
manual toggling; it should decide which tools to use; add MCP + connectors;
the desktop center graph and terminal are not good enough; add a `/desktop`
command in the CLI; add simple local memory; ship a Docker story with optional
services (n8n, Ollama, Postgres) or self-install; polish everything to
production maturity.

## Decomposition (8 sub-projects, build order)

1. **Local memory** — foundation other features reference.
2. **Auto-skills** — skills activate themselves per request.
3. **`/desktop`** — jump from REPL to the web desktop.
4. **MCP client + connectors** — external tools, the largest piece.
5. **`relaycli doctor` + production polish** — readiness checks, version 0.2.0, README, CHANGELOG, CI.
6. **Docker packaging** — image + compose profiles for n8n/ollama/postgres.
7. **Web desktop redesign** — center graph + terminal drawer.
8. **Adversarial review** — multi-agent review of the whole increment, fix confirmed findings.

Each ships as its own commit with tests; the suite stays green throughout.

## 1. Local memory

- `relaycli/memory.py`: `MemoryStore` over two markdown files — global
  `~/.relaycli/memory.md` and project `<root>/.relaycli/memory.md`.
- Injected into the agent system prompt as a `MEMORY` section, each file
  capped (default 4000 chars, newest lines win) so it can't blow the context.
- New `remember` tool (`fact`, `scope: project|global`): appends
  `- [YYYY-MM-DD] fact`. Writes are **edit-gated** (asks in suggest mode) —
  consistent with the permission posture; fixed target paths, no traversal.
- `/memory` REPL command and `relaycli memory` CLI: show both files (path +
  content); editing is just editing the file.
- Threat note: project memory rides along with a cloned repo, same as
  CLAUDE.md in other agents — accepted, size-capped, and the prompt already
  declares file contents untrusted.

## 2. Auto-skills

- Skill frontmatter gains optional `triggers:` (comma-separated keywords,
  Indonesian + English for built-ins).
- New preference `skills_auto` (default **on**, persisted in
  `[preferences]`): before each request, a **keyword matcher** (no LLM call —
  free-tier quota is precious) scores skills against the request text and
  auto-activates the top matches (cap 2) for that request only, printed as
  `✦ auto-skill: debug` so the user always sees what steered the agent.
- **Security carve-out preserved**: only `builtin` and `user` skills are
  auto-activatable. `project` skills (from a cloned repo) remain manual-only —
  the existing "a repo can offer but not silently steer" stance survives.
- Manual `/skill` toggles still win: manually-on skills stay on; auto picks
  are additive per request. `/skill auto on|off` toggles the preference.

## 3. `/desktop`

- REPL `/desktop`: starts the web server on a daemon thread (loopback,
  default port; picks a free one if busy), opens the browser
  (`webbrowser.open`), keeps the REPL alive. Idempotent — second call reuses
  the running server.
- `relaycli web` gains `--open` (default true when invoked as
  `relaycli desktop`, a thin alias command).

## 4. MCP + connectors

- `relaycli/mcp.py`: minimal MCP client, **stdio transport, JSON-RPC 2.0,
  newline-delimited** — no new dependency, no asyncio (RelayCLI is sync).
  Handshake: `initialize` (protocolVersion `2024-11-05`) →
  `notifications/initialized` → `tools/list`; calls via `tools/call` with a
  per-call timeout (60s) and process cleanup on session end.
- Config `[mcp.<name>]`: `command` (list or string), `env` (values support
  `env:VAR` references, never logged), `enabled` (default true).
- Session integration: enabled servers are started lazily; their tools are
  registered into the session ToolRegistry as `mcp_<server>_<tool>` with the
  server-provided JSON schema passed through verbatim (an `MCPTool` subclass
  overrides schema/validation — MCP schemas are arbitrary, pydantic models
  are not generated).
- **Every MCP call is command-gated** (asks in suggest/auto-edit modes):
  external side effects are opaque, so they get run_command-level caution.
- Surfaces: `/mcp` in the REPL (servers, tool counts, status);
  `relaycli mcp list|add <preset|name>|remove|test <name>`. Connector
  presets (data, not code): `filesystem`, `fetch`, `github`, `postgres`,
  `puppeteer` — each a known `npx`/`uvx` command line; `add` writes config
  and warns if the runtime (node/uv) is missing.
- Tests: a fake stdio MCP server (small Python script) exercises the full
  handshake/list/call/error path hermetically.
- "Smart tool selection" lands here + in the system prompt: the template's
  tool-guidance section is rewritten to cover choosing among native, MCP,
  and memory tools; roles keep curated registries (that design already works).

## 5. Doctor + production polish

- `relaycli doctor`: table of checks with ✓/✗ and exit code — config file
  perms (0600), provider key presence + **live OpenRouter auth ping**
  (`/api/v1/auth/key`), model resolution for every enabled role, `.env`
  vs config.toml key drift (the 401 incident), node/docker availability
  (for MCP presets / compose), memory + skills dirs writable.
- Version bump to **0.2.0**; `CHANGELOG.md`; README rewritten around the new
  surface (quickstart, skills/auto, memory, MCP, web, docker).
- CI: GitHub Actions, pytest on Python 3.12.

## 6. Docker

- `Dockerfile`: `python:3.12-slim`, non-root user, `pip install .`,
  `ENTRYPOINT ["relaycli"]`; `.dockerignore`.
- `docker-compose.yml`: service `relaycli` (mounts `./workspace`, persists
  `~/.relaycli` in a named volume, passes `OPENROUTER_API_KEY` etc. from the
  host env) + **profiles** `ollama`, `postgres`, `n8n` — opt-in via
  `docker compose --profile ollama up -d`, or install services yourself and
  point config at them. `OLLAMA_BASE_URL` env already flows into Settings.
- Web-in-docker: `relaycli web --host 0.0.0.0` allowed only with an explicit
  flag + warning; Host/Origin guard extended to the bound host. Default stays
  loopback.
- `docker/README.md` documents the recipes; no bespoke installer script —
  compose profiles ARE the "choose your services" UX.

## 7. Web desktop redesign

- Center graph rebuilt: layered pipeline layout, status-driven node states
  (idle ring / running pulse / done check / error cross), model chip + step
  count per node, edges animate only while active, clean empty state before
  the first run, reduced-motion respected. Roster specialists group visually
  under the coder stage (matches the real task-split execution).
- Terminal drawer: ANSI-color rendering (small ESC[ m parser), autoscroll
  with pause-on-scroll-up, clear button, resize handle, monospace stack.
- General polish pass over spacing/typography consistent with the existing
  dark `#0B0B0E` / `#2D5BFF` language.

## 8. Adversarial review

- Multi-agent workflow over the full increment diff: parallel finders
  (correctness, security, UX/regression) → adversarial verification → fix
  confirmed findings → suite green → final commit.

## Non-goals (this increment)

- No LLM-based skill/tool routing (quota), no HTTP/SSE MCP transports, no
  vector/semantic memory, no auth layer on the web UI (stays loopback-first),
  no unification of the relay-router vs roster role systems (tracked
  separately), no Windows docker docs.
