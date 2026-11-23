# Changelog

## Unreleased — 2026-08-21

### Security

- Confine `apply_patch` headers and fallback writes to the project root; use
  exclusive random patch files instead of a predictable temporary filename.
- Block SSRF/private targets and oversized responses in `webfetch`; bound and
  validate desktop HTTP requests and add browser security headers.
- Prevent project `.env` files from enabling or redirecting 9Router, validate
  local API base URLs, and redact provider credentials from object repr output.
- Kill complete Windows process trees on timeout/stop and invoke explicit shell
  executables instead of relying on `shell=True` lookup.
- Kill MCP descendant processes on shutdown and stop inheriting ambient
  provider API keys unless explicitly mapped in the server configuration.
- Upgrade vulnerable LiteLLM/aiohttp/click/json-repair/python-dotenv versions
  and add a reproducible `uv.lock`.

### Fixed

- Stop treating frontend design as optional style: concrete website runs now
  receive a context-derived creative direction, and an objective quality gate
  rejects placeholder copy, one-section shells, missing responsive behavior,
  browser-era typography, dead alerts, and motionless drafts before Reviewer.
- Give Ollama frontend runs a 6K context window, stop the Coder as soon as the
  three core web files are complete, and use a clean Coder session for rejected
  drafts so slow local models do not repeatedly rewrite the same weak template.
- Keep Ollama agent calls on a bounded blocking path while still forwarding
  final text to terminal/web callbacks, distinguish slow-generation timeouts
  from a stopped server, and budget one complete frontend file per model turn.
- Expand frontend rejection checks to cover inline handlers, pinned footers,
  missing reduced-motion fallbacks, and text-only centered stacks.
- Share explicit frontend destination parsing between the agent and optional
  scaffold path, including safe support for quoted folder names with spaces.
- Strengthen the built-in frontend-taste skill so campaigns, stores, and product
  interfaces no longer collapse into the same split-hero/card-grid template.
- Stop concrete file-creation requests from getting trapped in repeated
  `question` calls: actionable requests hide that tool, each agent has a
  one-question ceiling, and optional preferences resolve to sensible defaults.
- Skip the Planner round-trip for explicit static-frontend destinations and
  give the Coder a focused tool set, preventing irrelevant todo/question loops
  and reducing initial local-model prompt size.
- Add an instant, interactive mineral-water campaign scaffold for the optional
  local-scaffold mode; it produces HTML/CSS/JS without an LLM round-trip.
- Give every local scaffold domain a distinct composition—editorial fashion,
  kinetic footwear, Mandarin learning dashboard, bento store, and cinematic
  water campaign—with offline typography, metadata, motion, and real footers.
- Add `/tools` and make `doctor` verify all 22 native agent tools, including
  patching, git, background processes, web, todos, questions, and skills.
- Make `/model` and desktop model switches normalize installed bare Ollama
  tags and synchronize a uniform Relay tier profile, so Planner/Coder/Reviewer
  actually follow the selected model; automatic slow-model fallback now does
  not overwrite that explicit global selection and never downgrades file-changing
  implementation work to an undersized model.
- Execute exact one-folder requests through the native `create_folder` tool
  without model round trips, while retaining path, permission, lease, and
  verification checks.
- Restore explicit Settings field-name overrides for fields that also have
  environment aliases.
- Fall back to Python search when a resolved `rg.exe` cannot execute on Windows.
- Use the real/explicit terminal width instead of Rich's legacy Windows cap.
- Distinguish a missing Ollama model from an unreachable Ollama server.
- Read loaded Ollama model status from the bounded `/api/ps` endpoint instead
  of a Windows subprocess that could linger when the local server was down.
- Bound long-running desktop event and chat retention.

### Desktop UI

- Redesign the desktop console around an interactive four-agent flow graph
  with animated connections, synchronized node/agent selection, zoom,
  drag-to-pan, a cobalt dark theme, and a persistent collapsible terminal.
- Keep Orchestrator visible in both the graph and agent inspector, and reclaim
  idle activity space so every agent remains visible at laptop heights.
- Repair the narrow-screen panel hierarchy, lane clipping, and header overflow.
- Make the interface self-contained/offline by removing remote font requests;
  add a local favicon and responsive run-inspector sheet.

## 0.4.1 — 2026-07-06

The ready-to-install CLI patch.

### Added
- Native `create_folder` tool so small/local models can execute folder-creation
  requests instead of returning fake JSON tool names.
- `relaycli doctor` installation checks for bundled skills, core tools, and
  the desktop UI asset.
- Wheel packaging regression test to ensure runtime assets ship with installs.

### Changed
- The one-line installer now smoke-checks the installed command and repairs to
  a private virtualenv if the command starts with missing dependencies.
- Agent file-edit recovery now includes project path hints and read-before-edit
  recovery for existing files.
- Version 0.4.1.

## 0.4.0 — 2026-07-05

The onboarding and terminal-UX release.

### Added
- `relaycli init`: guided first-run setup that detects local Ollama, chooses a
  usable model, writes `~/.relaycli/config.toml`, and can start optional Docker
  Compose services (`ollama`, `web`, `postgres`, `n8n`).
- One-line installer script at `scripts/install.sh`; the README now documents
  `curl -fsSL .../scripts/install.sh | sh` for new users.
- Terminal command palette: typing `/` shows helpful commands, with new
  `/setup`, `/services`, and `/doctor` helpers.
- Ollama readiness checks in `relaycli doctor`, plus warnings for local models
  that may not support tool calling reliably.

### Changed
- Web desktop now surfaces onboarding/setup state and local Ollama readiness.
- Terminal setup panels and status warnings are more actionable.
- Version 0.4.0.

## 0.2.0 — 2026-07-03

The production-maturity release.

### Added
- **MCP connectors**: stdio Model Context Protocol client, `[mcp.<name>]`
  config with `env:` secret references, `relaycli mcp
  list|add|remove|test`, `/mcp` in the REPL, presets (filesystem, fetch,
  github, postgres, puppeteer). Server tools appear as
  `mcp_<server>_<tool>`, command-gated.
- **Long-term memory**: global (`~/.relaycli/memory.md`) + per-project
  (`.relaycli/memory.md`) notes injected into every session; `remember`
  tool (edit-gated); `/memory` and `relaycli memory`.
- **Auto-skills**: built-in/user skills carry `triggers:` keywords
  (English + Indonesian) and activate per matching request, announced with
  `✦ auto-skill:`; `/skill auto on|off` (persisted). Project skills stay
  manual-only.
- **`relaycli doctor`**: config/dir permissions, live OpenRouter key
  verification, `.env` vs `config.toml` key-drift detection, model routing
  preflight, memory-dir writability, MCP + runtime availability
  (node/npx/uvx/docker/git). `--offline` skips network checks.
- **Desktop entry points**: `/desktop` in the REPL (background server +
  browser), `relaycli desktop`, `relaycli web --open`.
- **Docker packaging**: Dockerfile + docker-compose profiles for optional
  Ollama / Postgres / n8n services.
- Actionable hint when a provider rejects an API key (401) instead of a raw
  LiteLLM error dump.

### Changed
- Agent system prompt: tool-selection guidance (native vs MCP vs memory).
- Web desktop: configured roster team visible in the flow view before a run.
- Version 0.2.0.

## 0.1.0 — 2026-07-02

Initial release: provider-agnostic terminal coding agent with real tools,
permission modes, relay pipeline (Planner → Coder → Reviewer) with per-role
model routing, 16-role roster with task-split mode, skills, web desktop UI,
persistent configuration (`~/.relaycli/config.toml`, 0600), and a security
audit (path confinement, secret-file gating, key scrubbing).
