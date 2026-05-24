# RelayCLI

A provider-agnostic, installable **terminal coding agent** — the same form
factor as Claude Code / OpenAI Codex CLI, but you choose the model. Installed
once, run `relaycli` in any project directory: it reads and edits the real
files there, runs shell commands, and works through your request in an
interactive terminal session.

> Status: the single-agent MVP is complete (LLM gateway, coding tools,
> permission system, agent loop, REPL) and hardened by a security audit and a
> full pytest suite. The optional multi-agent relay pipeline is available via
> `--relay` / `/relay on`.

## Install

RelayCLI is a standard console script. Install it isolated with `pipx` or
`uv tool` (recommended), or into a virtualenv with `pip`.

```bash
# isolated, recommended
pipx install relaycli
# or from a checkout
pipx install -e .

# alternative
uv tool install relaycli

# plain pip (inside a virtualenv)
pip install -e .
```

This puts a `relaycli` command on your PATH.

## Configure

RelayCLI reads configuration from (highest precedence first):

1. CLI flags
2. environment variables / a local `.env`
3. `~/.relaycli/config.toml`
4. built-in defaults

Provider keys use their standard names (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`). Ollama needs no key.

```bash
cp .env.example .env   # then fill in the providers you use
```

Example `~/.relaycli/config.toml`:

```toml
model = "gpt-4o-mini"
permission_mode = "suggest"
```

## Run

```bash
relaycli            # prints the banner (interactive session in a later stage)
relaycli config     # show active config + which provider keys are detected
relaycli --help
```

## Permission modes

| mode        | behaviour                                            |
|-------------|------------------------------------------------------|
| `suggest`   | ask before any edit or command (default, safest)     |
| `auto-edit` | auto-apply edits, still ask before running commands  |
| `full-auto` | never prompt (a banner is shown while active)        |

## Relay pipeline (multi-agent)

The relay pipeline is what makes RelayCLI *RelayCLI*: instead of one agent,
each request flows through three specialized roles —

1. **Planner** (read-only tools) explores the project and writes a short
   numbered plan.
2. **Coder** (full tools, honors your permission mode) carries out the plan
   and reports what changed.
3. **Reviewer** (read + run tests, no writes) verifies the working tree and
   answers `VERDICT: approve` or `VERDICT: revise`. On revise, its feedback
   goes back to the Coder — bounded by `max_review_cycles` (default 2).

Each role can run on a different model ("smart routing"): a cheap model for
planning/review, a strong one for coding.

```toml
# ~/.relaycli/config.toml
relay_enabled = true
planner_model = "gpt-4o-mini"
coder_model = "claude-3-5-sonnet-latest"
reviewer_model = "gpt-4o-mini"
```

Enable per-session with `relaycli --relay` (works with `-p` one-shots too) or
`/relay on` in the REPL. `relaycli config` shows the active role → model
routing. The single-agent mode remains the default and is unchanged.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```
