# RelayCLI

A provider-agnostic, installable **terminal coding agent** — the same form
factor as Claude Code / OpenAI Codex CLI, but you choose the model. Install it
once, then run `relaycli` in any project directory: it reads and edits the
real files there, runs shell commands, and works through your request in an
interactive terminal session.

## Features

- **Provider-agnostic** (via LiteLLM): OpenAI, Anthropic, Gemini, Groq,
  Mistral, OpenRouter, or a local Ollama — no key needed for Ollama. Switch
  models live with `/model`.
- **Real coding tools**: `read_file`, `search` (ripgrep with Python fallback),
  `edit_file`, `write_file`, `run_command` — every file change is shown as a
  colored diff before it is applied.
- **Permission modes** — `suggest` / `auto-edit` / `full-auto` — gate every
  edit and every command.
- **Relay pipeline** (optional multi-agent mode): Planner → Coder → Reviewer
  with per-role model routing and a bounded review/revise loop.
- **Safety rails**: paths confined to the project root, `.gitignore`
  respected, secret files (`.env`, credentials) never auto-read, provider keys
  scrubbed from spawned command environments. Details in
  [SECURITY.md](SECURITY.md).
- **Cost awareness**: streaming output plus a per-task summary of steps,
  tokens, estimated cost, and elapsed time.

## Install

Requires Python 3.12+. Install isolated with `pipx` or `uv tool`
(recommended), or into a virtualenv with `pip`.

```bash
# from a checkout (isolated, recommended)
pipx install -e .
# or
uv tool install .

# plain pip (inside a virtualenv)
pip install -e .
```

This puts a `relaycli` command on your PATH.

## Quick start

```bash
cd your-project
export OPENAI_API_KEY=sk-...      # or any other provider key (see below)

relaycli                          # interactive session
relaycli -p "find every TODO in this project and list them"   # one-shot
relaycli config                   # check which provider keys are detected
```

## Configure

RelayCLI reads configuration from (highest precedence first):

1. CLI flags
2. environment variables / a local `.env`
3. `~/.relaycli/config.toml`
4. built-in defaults

Provider keys use their standard names: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`.
Ollama needs no key (`ollama_chat/<model>`, server URL via `OLLAMA_BASE_URL`).

```bash
cp .env.example .env   # then fill in the providers you use
```

Example `~/.relaycli/config.toml`:

```toml
model = "gpt-4o-mini"
permission_mode = "suggest"
```

> Security note: a project-local `.env` can set the model and provider keys,
> but deliberately **cannot** change the permission mode or the Ollama URL —
> an untrusted repo must not be able to switch RelayCLI into `full-auto` or
> redirect model traffic. Set those via real environment variables, CLI
> flags, or `~/.relaycli/config.toml`.

## Usage

### Interactive session

`relaycli` (no arguments) opens the REPL: type a request, watch the streamed
answer and tool activity, approve diffs/commands per your permission mode.
The welcome panel shows the model and whether its API key was found; if it
wasn't, a setup panel lists the exact fixes (it doesn't block the session).

| input | effect |
|---|---|
| plain text | send a request to the agent |
| `/model [name]` | show or switch model (e.g. `gpt-4o-mini`, `ollama_chat/llama3.1`) |
| `/mode [suggest\|auto-edit\|full-auto]` | show or switch permission mode |
| `/relay [on\|off]` | toggle the Planner → Coder → Reviewer pipeline |
| `/diff` | show working-tree changes (`git diff`) |
| `/clear` | reset the conversation |
| `/help` | show help (also `help`, `?`) |
| `/exit` | quit (also `exit`, `quit`, Ctrl-D) |
| `!<cmd>` | run a shell command in the project root (e.g. `!git status`) |

Typing `/` pops a menu of every command with its description — keep typing to
filter (`/mo` → `/model`, `/mode`), and `/mode `, `/relay `, `/model ` suggest
their arguments. Enter accepts the highlighted entry when the menu is open and
submits otherwise; Alt+Enter inserts a newline; Ctrl-C clears the line. A
status bar at the bottom shows the live `model · mode · relay` state. Typing a
`-flag` in the REPL prints a hint instead of sending it to the model — flags
belong on the `relaycli` command line.

### One-shot and flags

```bash
relaycli -p "<request>"      # run one agent loop non-interactively and exit
relaycli -m gpt-4o           # override the model at launch
relaycli --mode auto-edit    # override the permission mode at launch
relaycli -p "..." -y         # auto-approve prompts in non-interactive runs
relaycli --relay             # run through the relay pipeline (also with -p)
relaycli config              # active config, relay routing, detected providers
relaycli version             # print the version
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
planning/review, a strong one for coding. Unset roles fall back to the base
`model`.

```toml
# ~/.relaycli/config.toml
relay_enabled = true
planner_model = "gpt-4o-mini"
coder_model = "claude-3-5-sonnet-latest"
reviewer_model = "gpt-4o-mini"
max_review_cycles = 2
```

Enable per-session with `relaycli --relay` (works with `-p` one-shots too) or
`/relay on` in the REPL; `--no-relay` overrides an enabled config. The
end-of-task summary breaks down steps, tokens, and cost per role. Single-agent
mode remains the default and is unchanged.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Design docs live under `docs/superpowers/specs/` and implementation plans
under `docs/superpowers/plans/`.
