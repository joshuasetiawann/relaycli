# Changelog

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
