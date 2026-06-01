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
- **Real coding tools**: `list_dir`, `find_files`, `read_file`, `search`
  (ripgrep with Python fallback), `edit_file`, `write_file`, `run_command`,
  plus `run_background` / `check_process` / `stop_process` for dev servers
  and watchers — every file change is shown as a
  colored diff before it is applied.
- **Permission modes** — `suggest` / `auto-edit` / `full-auto` — gate every
  edit and every command.
- **Relay pipeline** (optional multi-agent mode): Planner → Coder → Reviewer
  with per-role model routing and a bounded review/revise loop.
- **Skills that activate themselves**: built-in working styles (tdd, debug,
  verify, frontend-taste, …) switch on automatically when a request matches
  their triggers — always announced, never silent (`/skill auto off` to opt
  out).
- **Long-term memory**: the agent saves durable facts with the `remember`
  tool to `~/.relaycli/memory.md` (global) and `.relaycli/memory.md`
  (per-project) and reads them at the start of every session. `/memory` shows
  them; they're plain markdown you can edit.
- **MCP connectors**: attach external tool servers (GitHub, Postgres, fetch,
  filesystem, a real browser, or anything speaking MCP over stdio) with
  `relaycli mcp add <preset>` — their tools appear to the agent alongside the
  native ones, every call permission-gated like a command.
- **Desktop web UI**: `relaycli desktop` (or `/desktop` from the REPL) opens
  a loopback-only browser UI with the live agent pipeline, per-role models,
  and API-key management.
- **`relaycli doctor`**: one command that verifies keys (live), config file
  permissions, model routing, connector runtimes, and the classic
  `.env`-vs-config key-drift trap.
- **Safety rails**: paths confined to the project root, `.gitignore`
  respected, secret files (`.env`, credentials) never auto-read, provider keys
  scrubbed from spawned command environments. Details in
  [SECURITY.md](SECURITY.md).
- **Cost awareness**: streaming output plus a per-task summary of steps,
  tokens, estimated cost, and elapsed time.

## Install

Requires Python 3.12+ and `git`.

One-line install for new users:

```bash
curl -fsSL https://raw.githubusercontent.com/joshuasetiawann/relaycli/main/scripts/install.sh | sh
```

The installer clones RelayCLI, installs the `relaycli` command, smoke-checks
that the command starts cleanly, then opens the guided setup. If a PATH/tool
installer leaves a broken command behind, it repairs RelayCLI into a private
virtualenv under `~/.relaycli/venv`. In setup you can choose a model and
optionally start local services such as Ollama, n8n, the web UI, or Postgres
via Docker Compose.

Manual install isolated with `pipx` or `uv tool` (recommended), or into a
virtualenv with `pip`:

```bash
git clone https://github.com/joshuasetiawann/relaycli.git
cd relaycli

pipx install -e .
# or
uv tool install .

# plain pip (inside a virtualenv)
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

This puts a `relaycli` command on your PATH (with `pip`, only inside the
activated virtualenv). For running tests from a checkout, install the dev extra:
`pip install -e ".[dev]"`.

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
| `/agents [r on\|off]` | show relay agents; toggle the optional explorer/tester |
| `/skill [name]` | toggle a skill for this session (tdd, debug, ponytail, …) |
| `/skill auto [on\|off]` | toggle per-request skill auto-activation |
| `/skills` | list available skills and their sources |
| `/memory` | show long-term memory (global + project) |
| `/mcp` | show MCP connectors and their tools |
| `/desktop` | open the desktop web UI in your browser |
| `/config` | roles, per-role models & provider keys (persistent config) |
| `/settings` | general preferences: mode, theme, context limit |
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

### Skills

A skill is a small markdown file that steers how the agent works. Built-ins:
`ponytail` (least-code discipline), `tdd`, `debug`, `brainstorm`, `verify`,
`frontend-taste`. Toggle one per session with `/skill <name>` — or let them
activate themselves: built-in and user skills carry `triggers:` keywords
(English + Indonesian), and when a request matches, the best one or two
switch on for that request with a visible `✦ auto-skill:` line. Pure keyword
matching — no extra model call. `/skill auto off` disables it.

Add your own to `~/.relaycli/skills/` (or a project's `.relaycli/skills/`)
as `name.md` with a `---` header carrying `name:`, `description:` and
optional `triggers:`. Active skills also steer the relay coder.
**Project** skills are never auto-activated — a cloned repo can offer a
skill but cannot silently steer the agent.

### Memory

The agent keeps durable notes across sessions in two plain markdown files:
`~/.relaycli/memory.md` (global: your preferences, environment quirks) and
`<project>/.relaycli/memory.md` (project conventions, gotchas). It saves
facts itself with the `remember` tool — gated like an edit, so `suggest`
mode asks first — and reads both files into its context every session
(size-capped). `/memory` or `relaycli memory` shows them; edit or delete
the files freely.

### MCP connectors

RelayCLI speaks the Model Context Protocol (stdio). Connect external tool
servers and the agent can use their tools like native ones:

```bash
relaycli mcp list                 # configured servers + available presets
relaycli mcp add github           # preset (uses GITHUB_TOKEN via env:)
relaycli mcp add fetch            # web pages as markdown (needs uvx)
relaycli mcp add mydb --command "npx -y @modelcontextprotocol/server-postgres env:DATABASE_URL"
relaycli mcp test github          # start it once and list its tools
relaycli mcp remove github
```

Servers live in `~/.relaycli/config.toml` under `[mcp.<name>]`; `env:VAR`
values resolve at start time so secrets stay out of the file. Tools appear
as `mcp_<server>_<tool>` and **every call asks for approval** exactly like
`run_command` (full-auto skips prompts, as always). `/mcp` shows status
inside the REPL.

### Agents

The relay pipeline is Planner → Coder → Reviewer, plus two opt-in roles:
an **explorer** that scouts the codebase before planning (read-only) and a
**tester** that runs the plan's verification step after coding. `/agents`
shows the lineup; `/agents explorer on` / `/agents tester on` enable them
(each adds a full agent run per request). Per-role models via
`RELAYCLI_EXPLORER_MODEL` etc.

**Specialists (roster-driven).** With task-split on (`/agents tasks on`), the
Planner delegates each numbered step to a specialist from the 16-role roster
by tagging it — `1. [backend] add the token helper`, `2. [frontend] wire the
form`. Each task then runs on a fresh agent with that role's system prompt and
its own resolved model, so a real team of specialists collaborates on one
request. Enable roles and assign their models in `relaycli config` (or
`/config`); `/agents` lists the enabled specialists. Untagged steps use the
general Coder.

### Configuration & Settings

RelayCLI keeps two surfaces strictly separate:

- **`relaycli settings`** (or `/settings`) — general preferences only:
  permission mode, theme, context-token limit.
- **`relaycli config`** (or `/config`) — a persistent, roster-based config
  with two sections: **Roles & Models** (16 built-in roles, each
  enable/disable + a per-role model or tier) and **Providers & Keys** (set a
  key as an env reference or a masked literal). Scriptable subcommands:
  `config show`, `config set-model <role> <model|tier>`, `config tier <t>
  <model>`, `config enable|disable <role>`, `config set-key <provider>
  [--env VAR | --value …]`, `config path`.

Everything persists atomically to `~/.relaycli/config.toml` (`0600`); keys
are never printed — only masked status (`via env (VAR)` or `sk-…abcd`).

### One-shot and flags

```bash
relaycli -p "<request>"      # run one agent loop non-interactively and exit
relaycli -m gpt-4o           # override the model at launch
relaycli --mode auto-edit    # override the permission mode at launch
relaycli -p "..." -y         # auto-approve prompts in non-interactive runs
relaycli --relay             # run through the relay pipeline (also with -p)
relaycli config              # active config, relay routing, detected providers
relaycli doctor              # health checks (keys live-verified, perms, routing)
relaycli memory              # show long-term memory
relaycli mcp list            # MCP connectors and presets
relaycli desktop             # desktop web UI in the browser (loopback only)
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
