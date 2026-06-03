# RelayCLI — Claude Code-style look + OpenRouter open-source defaults (design)

Date: 2026-07-03
Status: approved for implementation (user: "untuk open router saya mau model
open source aja … tampilannya masih kurang banget, bisa ikutin kaya claude
cli gak? … saya juga mau kamu set api keynya"). Presentation/config changes
only; one commit per task.

## Problems observed

1. The curated `/model` suggestions include closed models behind the
   OpenRouter prefix (`openrouter/anthropic/claude-3.5-sonnet`); the user
   wants open-source-only there. `.env.example` and the config field docs
   use the same closed-model examples.
2. The visual language (cyan panel, `●` one-liners, reversed toolbar,
   `model · mode ›` prompt) doesn't read like Claude Code, which the user
   wants to mirror.
3. Fresh machine setup is manual: the user has an OpenRouter key but nothing
   is configured; the default model (`gpt-4o-mini`) needs a key they don't
   have.

## Facts established against the live OpenRouter API (2026-07-03)

- The user's key is valid (`GET /api/v1/key` OK) and **free-tier**
  (`is_free_tier: true`, no credits): paid models 402 until credits are
  added; `:free` variants work with daily caps and can be transiently
  rate-limited upstream (qwen3-coder:free 429'd; nemotron-3-super:free
  answered).
- Open-weights check = `hugging_face_id` present in `/api/v1/models`;
  tool-calling check = `"tools" ∈ supported_parameters`. Verified ids:
  `qwen/qwen3-coder:free` (1M ctx), `qwen/qwen3-coder-next`,
  `deepseek/deepseek-v4-flash`, `z-ai/glm-4.7`, `moonshotai/kimi-k2.6`,
  `minimax/minimax-m2.7`, `openai/gpt-oss-120b:free`,
  `nvidia/nemotron-3-super-120b-a12b:free`,
  `meta-llama/llama-3.3-70b-instruct:free`. (`qwen3.6-flash`/`qwen3.7-plus`
  etc. have NO public weights — excluded.)
- `~/.relaycli/config.toml` is **alias-strict** like init kwargs:
  `OPENROUTER_API_KEY = "..."` loads, `openrouter_api_key = "..."` is
  silently ignored (verified empirically).

## Changes

### 1. OpenRouter: open-source suggestions + configured key

- `repl.py::_ARG_COMPLETIONS["model"]`: replace the single
  `openrouter/anthropic/...` entry with the verified open-source set above
  (keep the other providers' entries; suggestions, not a whitelist).
- `.env.example` + `config.py` field description: open-source example ids.
- Local machine (NOT committed): write `~/.relaycli/config.toml` (0600) with
  `model = "openrouter/qwen/qwen3-coder:free"` and the key under its alias
  spelling; key stays out of the repo. `relaycli config` / preflight must
  show `key detected`.

### 2. Claude-style activity rendering (`render.py::RichReporter`)

- Tool outcome becomes the two-line Claude shape:
  `⏺ tool_name` (bold; green dot ok, red fail) then `  ⎿  <summary>` (dim).
  Summaries stay escape()d; `tools_used` tracking unchanged.
- Assistant text blocks start with a `⏺ ` bullet before the first streamed
  token of each block (raw-write streaming unchanged after that).
- A dim spinner (`✶ Working…`) shows while waiting on the model — started
  at run start and after each tool result, stopped before any output.
  Terminal-only (`console.is_terminal`); tests on StringIO consoles see
  plain output, no spinner frames.

### 3. Claude-style chrome (welcome, prompt, menu, toolbar)

- Welcome: rounded box, Claude-orange (#D97757) border and `✻` title:
  `✻ Welcome to RelayCLI v<ver>!`, dim body lines (model + key note, cwd,
  mode + meaning, relay, hints). Same information as today — the banner
  tests keep asserting version/cwd/model/mode/key-note substrings; long cwd
  still folds.
- Prompt shrinks to a Claude-like `❯ ` (accent-colored): the session status
  already lives in the bottom toolbar, so the old `model · mode ›` prompt
  is redundant. `_prompt_text` tests updated accordingly.
- Toolbar restyled: no reverse video — dim gray text on default background
  (prompt_toolkit Style), same content.
- Slash-menu styled dark-gray with a subtle selection highlight, meta text
  dim (Style classes `completion-menu.*`).
- Setup panel: rounded + dim border, content unchanged (anti-spoof anchor
  and tests untouched).

## Out of scope

A real bordered input box (prompt_toolkit full-screen layout), themes,
relay role-banner redesign, background pre-warming, paid-tier handling.

## Tests

- Completer: `/model ` suggestions contain only open-source ids under the
  `openrouter/` prefix (and at least one `:free`).
- Reporter: tool_end emits the `⏺ name` + `⎿ summary` shape for ok/fail;
  assistant bullet appears once per block; StringIO consoles get no
  spinner frames. Existing substring assertions keep passing.
- Welcome/prompt: banner still carries version/cwd/model/key/mode; prompt
  is `❯ `; toolbar content unchanged.
- pty drive at the end: banner, menu, one !cmd, /exit — eyeball the ANSI.
