# RelayCLI

A provider-agnostic, installable **terminal coding agent** — the same form
factor as Claude Code / OpenAI Codex CLI, but you choose the model. Installed
once, run `relaycli` in any project directory: it reads and edits the real
files there, runs shell commands, and works through your request in an
interactive terminal session.

> Status: built in stages. Stage 1 ships the scaffold + config + a runnable
> banner. The LLM layer, tools, agent loop, and REPL land in later stages.

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

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```
