# RelayCLI — Terminal UX polish (design)

Date: 2026-07-02
Status: approved for implementation (user asked "perbagus lagi, masih susah
dipakai"; scope question timed out — full recommended package applied).
Process note: plan doc skipped intentionally — presentation-layer changes,
tasks tracked in this spec.

## Problems observed (from the user's real session)

1. Typed `--help` inside the REPL → sent to the LLM as a request.
2. No API key configured → nothing warns at startup; the first request fails
   with a raw error line.
3. Banner/prompt/errors look bare; no onboarding.

## Changes

### 1. Credential preflight (no network)

- `LLM.preflight(model) -> str | None` — returns the credential problem
  string, or None when the model looks runnable. Only judges providers we
  know (`_PROVIDER_KEY_ATTR`); unknown/custom providers return None (they may
  be legitimately configured through LiteLLM's own env).
- REPL banner: when preflight fails, show a setup panel (the problem, the
  exact `export ...` line, the Ollama alternative, detected-provider list,
  `/model` hint). Non-blocking.
- One-shot `-p`: same panel, then exit code 2 — fail fast before any loop.
  With relay on, every routed role model is preflighted.
- After an agent run errors with "No API key", re-show the setup panel.

### 2. REPL input smarts

Extract per-line dispatch into `Repl._handle_line(line) -> bool` (exit flag)
so it is unit-testable. Rules, in order:

| input | action |
|---|---|
| `/...` | slash command (unchanged) |
| `!cmd` | run `cmd` as a user shell command in the project root (captured, printed, exit code shown). User-typed ⇒ not permission-gated; Ctrl-C safe |
| `-...` (leading dash) | do NOT send to the model; hint that flags belong on the CLI and `/help` lists session commands |
| `help`, `?` | same as `/help` |
| `exit`, `quit` | quit like `/exit` |
| anything else | agent request (unchanged) |

### 3. Visual polish

- Welcome banner → Rich `Panel` (`render.render_welcome`): name + version,
  cwd, model with key status (`key detected` / `key missing ⚠` / `no key
  needed`), mode with its meaning, relay status + routing, quick-start hints.
- Prompt shows the model's short name and mode: `gpt-4o-mini · suggest › `
  (+ `· relay` when on).
- `/help` → aligned Rich table incl. `!cmd`, aliases, key bindings.
- One-shot header reuses the same status line style.

All strings that can carry config-controlled text (model ids, problems) are
`escape()`d, consistent with the existing markup-injection policy.

## Files

`relaycli/llm.py` (preflight), `relaycli/render.py` (welcome, setup panel,
help table), `relaycli/repl.py` (dispatch, prompt, banner), `relaycli/cli.py`
(one-shot preflight + header), tests in `tests/test_ux.py` (+ relay CLI tests
untouched), README (usage table).

## Tests

- preflight: missing key → message; ollama → None; unknown provider → None;
  key present → None.
- dispatch: `-h`/`--help` → hint, no agent call; `help`/`?` → help; `exit` →
  True; `!echo hi` → output + not permission-gated; plain text → agent.
- one-shot: missing key → exit 2 + actionable output; mocked-provider tests
  keep passing (unknown provider ⇒ preflight None).
- banner: version, cwd, model, mode, key-missing warning present.

## Out of scope

Autocomplete, themes, non-interactive TTY detection changes, i18n.
