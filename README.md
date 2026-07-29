# RelayCLI

RelayCLI is a provider-agnostic coding agent for the terminal and desktop. Run
it inside any project, choose the model you want, and let it read files, edit
code, run commands, review changes, and keep useful project memory.

It is built for the same workflow family as Claude Code and Codex CLI, with a
few RelayCLI-specific ideas: persistent settings, optional multi-agent relay,
local Ollama support, desktop UI, MCP connectors, and explicit permission
control for every write or command.

## Why RelayCLI

- **Bring your own model**: OpenAI, Anthropic, Gemini, Groq, Mistral,
  OpenRouter, DeepSeek, Qwen, GLM, or local Ollama through LiteLLM.
- **Real project tools**: list, search, read, edit, write, run commands, manage
  background processes, and save durable memory.
- **Predictable safety**: project-root path confinement, secret-file protection,
  `.gitignore` awareness, diff previews, and permission modes.
- **Terminal first, desktop ready**: use the interactive CLI or launch a
  loopback-only browser UI with live agent status.
- **Persistent configuration**: model, mode, relay, agents, recent models,
  roles, providers, and memory load automatically on the next run.
- **Optional relay pipeline**: Planner -> Coder -> Reviewer, with per-role
  models and task-split specialists.

## Quick Start

Install:

```bash
curl -fsSL https://raw.githubusercontent.com/joshuasetiawann/relaycli/main/scripts/install.sh | sh
```

Start in a project:

```bash
cd your-project
relaycli
```

Then type a normal request:

```text
explain this repo
fix the failing tests
build a simple landing page in a new folder named demo-site
```

For guided setup at any time:

```bash
relaycli init
relaycli doctor
```

The installer clones RelayCLI, installs the `relaycli` command, smoke-checks
the install, and opens setup. If `uv` or `pipx` is unavailable, it falls back to
a private virtualenv under `~/.relaycli/venv`.

During setup you can also choose optional local services such as Ollama, n8n,
the desktop web UI, or Postgres.

## Installation Options

RelayCLI requires Python 3.12+ and Git.

```bash
git clone https://github.com/joshuasetiawann/relaycli.git
cd relaycli
```

Install with `uv`:

```bash
uv tool install .
```

Install with `pipx`:

```bash
pipx install -e .
```

Install in a virtualenv:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

Install development dependencies:

```bash
pip install -e ".[dev]"
```

## First Run

RelayCLI reads configuration from:

1. CLI flags
2. Environment variables and local `.env`
3. `~/.relaycli/config.toml`
4. Built-in defaults

Common provider keys:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
export GROQ_API_KEY=...
export MISTRAL_API_KEY=...
export OPENROUTER_API_KEY=...
```

Ollama needs no API key:

```bash
ollama serve
ollama pull qwen2.5-coder:1.5b
relaycli init --model ollama_chat/qwen2.5-coder:1.5b --yes
```

Example `~/.relaycli/config.toml`:

```toml
model = "gpt-4o-mini"
permission_mode = "suggest"
relay_enabled = false
```

Project `.env` files may set models and provider keys, but cannot escalate the
permission mode or redirect Ollama traffic. Those safety-sensitive settings
must come from CLI flags, real environment variables, or
`~/.relaycli/config.toml`.

## Daily Usage

Interactive mode:

```bash
relaycli
```

One-shot mode:

```bash
relaycli -p "find every TODO and summarize them"
relaycli -p "fix the import error" --mode auto-edit
relaycli -p "review the current diff" --relay
relaycli -p "add a health endpoint" --no-experimental-parallel  # single agent, no task graph
```

Desktop UI:

```bash
relaycli desktop
```

Health check:

```bash
relaycli doctor
```

## Interactive Commands

Type `/` in the REPL to open the command palette. Keep typing to filter.

| Command | Purpose |
| --- | --- |
| `/model [name]` | Show or switch the active model |
| `/mode [suggest\|auto-edit\|full-auto]` | Show or switch permission mode |
| `/relay [on\|off]` | Toggle Planner -> Coder -> Reviewer |
| `/agents` | Show relay roles and task-split specialists |
| `/agents explorer on` | Add a read-only explorer before planning |
| `/agents tester on` | Add verification after coding |
| `/agents tasks on` | Let the planner delegate steps to specialists |
| `/skill [name]` | Toggle a skill for the current session |
| `/skill auto [on\|off]` | Enable or disable automatic skill activation |
| `/skills` | List available skills |
| `/memory` | Show global and project memory |
| `/mcp` | Show MCP connector status |
| `/desktop` | Open the desktop UI |
| `/config` | Configure roles, provider keys, and model tiers |
| `/settings` | Configure general preferences |
| `/diff` | Show uncommitted changes |
| `/clear` | Reset conversation history |
| `/help` | Show help |
| `/exit` | Quit |
| `!<cmd>` | Run a shell command in the project root |

## Permission Modes

| Mode | Behavior |
| --- | --- |
| `suggest` | Ask before edits and commands. Safest default. |
| `auto-edit` | Apply file edits automatically, ask before commands. |
| `full-auto` | Run edits and commands without asking. Use deliberately. |

Mode changes persist automatically. If you switch to `full-auto`, the next
RelayCLI session will remember it until you change it again.

## Desktop UI

`relaycli desktop` starts a loopback-only web app. It includes:

- chat with the agent or relay team
- model picker with recent models, provider groups, search, and Ollama pull
- mode controls that persist across sessions
- project directory switcher
- live workflow visualization
- activity feed for model calls, reads, writes, commands, and summaries
- resizable terminal output drawer
- provider key and role configuration panels

The desktop UI is local by default. It binds to loopback unless explicitly
configured otherwise.

## Relay Pipeline

Relay mode turns one request into a small workflow:

1. **Planner** inspects the project and produces a short plan.
2. **Coder** edits files and runs commands according to the plan.
3. **Reviewer** checks the result and returns `VERDICT: approve` or
   `VERDICT: revise`.

Enable it:

```bash
relaycli --relay
# or inside the REPL
/relay on
```

Configure per-role models:

```toml
# ~/.relaycli/config.toml
relay_enabled = true
planner_model = "gpt-4o-mini"
coder_model = "claude-3-5-sonnet-latest"
reviewer_model = "gpt-4o-mini"
max_review_cycles = 2
```

Task-split mode lets the planner route numbered steps to specialist roles:

```text
1. [backend] add the API route
2. [frontend] wire the UI
3. [security] review input validation
```

Turn it on with:

```text
/agents tasks on
```

Use `/config` to enable specialists and assign their models.

## Parallel Orchestration

The default pipeline as of this release: an Orchestrator role reads the
request once and decomposes it into a task graph (independent steps run
concurrently; dependent steps wait), instead of running Planner → Coder →
Reviewer in sequence.

```bash
relaycli -p "add a health endpoint and its test"
# same thing, explicit:
relaycli -p "..." --experimental-parallel
```

Fall back to the sequential relay pipeline for one run, or turn parallel
off entirely:

```bash
relaycli -p "..." --no-experimental-parallel   # single agent, one run
relaycli -p "..." --relay                      # relay pipeline, one run
```

```toml
# ~/.relaycli/config.toml
experimental_parallel = false   # disable permanently
max_concurrent_agents = 3       # concurrency cap
```

A live pinned status bar + lane list shows progress while it runs — only
when running interactively in **full-auto** permission mode, since a
permission prompt can't safely share the screen with a display that's
redrawing itself. Any other permission mode prints one line per task as
it changes state instead.

While that live view is up, the lane list is navigable:

| Key | Action |
| --- | --- |
| `tab` / `shift-tab` | next / previous lane |
| `1`–`9` | jump to a lane |
| `enter` | focus the selected lane |
| `esc` | back out one level — at the top level, stop every agent |
| `^k` | collapse / expand the lane list |
| `x` | drop the selected task, freeing its file lease |
| `R` | retry the selected task once it has failed |
| `?` | toggle the key overlay |

`esc` peels one layer at a time (overlay, then focus, then stop), so it
can never end a run while something you were reading is still on screen.
Keys are read only from a real terminal; piped or redirected output is
unaffected.

`x` detaches a task and releases whatever files it had claimed, which is
how you unstick a graph waiting on one wedged agent. The work itself runs
in a thread and cannot be killed outright, so that agent finishes its
current model call before it goes away — its result is discarded either
way. `R` puts a failed task back in the queue and un-blocks everything
that was waiting on it; it applies while the run is still going, so retry
a lane while its siblings are still working.

The desktop UI has the same two controls as buttons on each task lane,
each shown only when it applies: **drop** while a task is still live,
**retry** once it has failed or been dropped.

### Reviewing what changed

The desktop console's canvas has two views: **lanes** (the task graph as
it runs) and **changes** (every file the agents touched). Each entry
shows the path, which task made the edit, the `+`/`−` tally, and the
hunks themselves with a real sign gutter — so selecting the code column
gives you text you can paste straight back into a file.

Changes are read-only. Accepting or rejecting individual hunks would
mean holding edits back until you approve them, which is a change to the
permission model rather than to the UI — `suggest` and `auto-edit` are
where that decision lives today.

This path is newer than relay/task-split and has had less real-world
mileage; if something looks wrong, `--relay` is the well-worn fallback.

### Benchmarking

`scripts/benchmark_parallel.py` runs one real request through both pipelines
back to back — same prompt, same model, a fresh temp project for each side —
and prints a wall-clock/tokens/cost table:

```bash
python scripts/benchmark_parallel.py openrouter/your/model:here
```

No numbers are published here. Three attempts to run it during Stage 7's own
development each failed for environment reasons, not a code defect: two
free-tier OpenRouter models (`qwen/qwen3-coder:free`, `openai/gpt-oss-120b:free`)
came back "unavailable on the free tier," a third (`cohere/north-mini-code:free`)
hung with no response or error, and neither Ollama (unreachable in that
sandbox) nor that machine's configured `claude-3-5-sonnet-latest` fallback (no
Anthropic key present) offered a way around it. Run the script yourself
against a model you know answers in your environment — the table it prints
is the real deliverable, not a number copied from here.

## Skills

Skills are markdown instructions that shape how the agent works. Built-in
skills include:

- `frontend-taste`
- `debug`
- `tdd`
- `verify`
- `brainstorm`
- `ponytail`

Skills can auto-activate when a request matches their triggers. RelayCLI
announces this with `auto-skill` output. Disable automatic activation with:

```text
/skill auto off
```

Add personal skills in:

```text
~/.relaycli/skills/
```

Project skills can live in:

```text
<project>/.relaycli/skills/
```

Project skills are listed, but not silently auto-activated.

## Memory

RelayCLI keeps durable notes in plain markdown:

```text
~/.relaycli/memory.md
<project>/.relaycli/memory.md
```

The agent can save facts with the `remember` tool, and reads memory at the
start of future sessions. Use `/memory` or `relaycli memory` to inspect it.

Good memory examples:

- project conventions
- preferred commands
- recurring environment issues
- user preferences
- known local model limitations

## MCP Connectors

RelayCLI supports MCP over stdio. Add external tool servers and the agent can
use them alongside native tools.

```bash
relaycli mcp list
relaycli mcp add github
relaycli mcp add fetch
relaycli mcp test github
relaycli mcp remove github
```

Custom command example:

```bash
relaycli mcp add mydb --command "npx -y @modelcontextprotocol/server-postgres env:DATABASE_URL"
```

Connector config is stored in `~/.relaycli/config.toml`. `env:VAR` references
resolve at runtime so secrets do not need to be written into the file.

## Configuration Surfaces

RelayCLI separates general preferences from model/provider routing.

Use settings for general preferences:

```bash
relaycli settings
```

Use config for roles, providers, keys, and model tiers:

```bash
relaycli config
relaycli config show
relaycli config set-model coder gpt-4o
relaycli config tier strong claude-3-5-sonnet-latest
relaycli config set-key openrouter --env OPENROUTER_API_KEY
relaycli config path
```

Configuration is written atomically to `~/.relaycli/config.toml` with `0600`
permissions. Provider keys are masked in output.

## Safety Model

RelayCLI is designed to be useful without being casual about trust:

- tool paths stay inside the project root
- `.gitignore` is respected
- secret-looking files are not auto-read
- provider keys are scrubbed from spawned command environments
- every edit is represented as a diff
- every command and write is permission-gated unless you choose `full-auto`
- project memory and project skills are treated as untrusted project data

Read [SECURITY.md](SECURITY.md) for details.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Useful commands:

```bash
python -m relaycli.cli version
python -m pytest -q
python -m compileall relaycli tests
```

Design notes live in `docs/superpowers/specs/`. Implementation plans live in
`docs/superpowers/plans/`.

## License

MIT. See [LICENSE](LICENSE).
