# RelayCLI — Slash-command menu + instant startup (design)

Date: 2026-07-03
Status: approved for implementation (user: "bikin kaya claude code cli — ketik
/ muncul commandnya; ini ribet banget"). Presentation/infra changes only;
tasks tracked in this spec, one commit per task.

## Problems observed

1. Typing `/` gives zero discoverability — you must memorize commands or run
   /help. Claude Code pops a menu with descriptions as soon as `/` is typed.
2. Cold start takes 15–40 s on spinning storage before anything is printed
   (importing the litellm/openai module tree — measured with -X importtime;
   not network). It reads as a hang or a bug.

## Changes

### 1. Instant startup (lazy LiteLLM)

- `relaycli/llm.py` stops importing litellm at module load. A `_lazy_litellm()`
  helper imports + configures it on first real use (completion, credential
  kwargs, token counting); `is_warm()` reports whether that happened.
- The no-network paths (`preflight`, `key_status`) stop using
  `litellm.get_llm_provider` and use a tiny static resolver instead:
  explicit `provider/` prefixes for the providers we manage, plus bare-name
  families (gpt-*, claude-*, gemini-*, mistral-*). Anything unrecognized
  returns None = "make no claim" — same permissive philosophy as today; the
  real call path keeps LiteLLM's full resolver and stays authoritative.
- Result: banner, setup panel, `relaycli config`, and the slash menu appear
  instantly; the heavy import cost moves to the first model call. The REPL
  prints a one-time "[dim]loading provider libraries…[/dim]" note right
  before that first call so the pause is explained.

### 2. Slash-command menu (Claude Code-style)

- `SlashCompleter` (prompt_toolkit `Completer`) wired into the PromptSession
  with `complete_while_typing=True`:
  - `/` at the start of the line → popup listing every command with its
    argument hint and a one-line description (`display_meta`).
  - Prefix filtering: `/mo` → /model, /mode.
  - First-argument completion: `/mode ` → suggest|auto-edit|full-auto,
    `/relay ` → on|off, `/model ` → curated common model ids.
  - No menu for plain text or multiline buffers (pastes).
- Enter applies the highlighted completion when the menu is open; otherwise
  submits (matches Claude Code muscle memory). Alt+Enter unchanged.
- Bottom toolbar shows live session status: `model · mode · relay · /help`.

## Files

`relaycli/llm.py` (lazy import + fast resolver), `relaycli/repl.py`
(SlashCompleter, key binding, toolbar, warm note), README, tests in
`tests/test_ux.py`.

## Tests

- Import-lightness: a fresh interpreter importing `relaycli.repl` +
  `relaycli.cli` must NOT have litellm in `sys.modules`.
- Fast resolver: bare gpt-*/claude-* map to their providers (key missing →
  named env var); `openrouter/...` needs OPENROUTER_API_KEY; unknown ids
  stay permissive (None). Existing preflight tests keep passing unchanged.
- Completer: `/` lists all commands; `/mo` filters; `/mode ` and `/relay `
  and `/model ` complete arguments; plain text and multiline yield nothing.

## Out of scope

Fuzzy history search, themes, autocompleting free-text requests, background
pre-warming threads.
