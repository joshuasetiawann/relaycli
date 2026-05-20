# RelayCLI — Build Prompt Sequence (7 prompts)
Terminal coding agent ala Claude Code / Codex CLI · MVP single-agent.

## Cara pakai pack ini
- Jalankan **berurutan, satu per satu**, di **session yang sama** pada coding tool kamu (Claude Code / Cursor).
- Setelah tiap prompt, tunggu langkah **VERIFY**-nya lolos dulu, baru paste prompt berikutnya.
- Tiap prompt sudah di-anchor ulang secukupnya, jadi kalau tool agak lupa konteks, masih bisa lanjut.
- Copy teks di antara dua divider `════` (jangan ikutkan label "Kapan dipakai").

---
════════════════════════════════════════════════════
## PROMPT 1 — PENGENALAN & SCAFFOLD
*Kapan dipakai: paling awal, sebelum menulis kode apa pun.*
════════════════════════════════════════════════════

You are building RelayCLI with me across several staged steps. This first step is **context + scaffolding only** — do NOT implement features yet.

**WHAT RELAYCLI IS**
An installable command-line coding agent — same form factor as Claude Code / OpenAI Codex CLI, but provider-agnostic. Installed once via `pipx install relaycli` and run as `relaycli` in any project directory, it reads and edits the real files in that directory, runs shell commands, and completes the user's request through an interactive terminal session. Closest open-source reference: aider.

**HOW WE WORK (applies to every stage)**
- We build in stages. Do exactly the current stage's scope — don't jump ahead.
- Make the smallest correct implementation. Don't over-engineer.
- After each stage, report briefly: what changed, which files, how to verify, any risk.

**STACK (use exactly; pin versions in pyproject.toml)**
- Python 3.12+, installable via pipx / uv tool install (console-script entry point)
- LiteLLM (multi-provider LLM + tool-calling + streaming + usage/cost)
- prompt_toolkit (REPL input/history/multi-line) + Rich (output, diffs, syntax)
- Pydantic v2 + pydantic-settings; config at `~/.relaycli/config.toml` + env/`.env`
- ripgrep (shell `rg`) for search, Python fallback
- git via shell for diffs/optional commits; difflib + Rich for diff rendering
- pytest

**TARGET ARCHITECTURE (filled in over later stages)**
```
relaycli/
├── pyproject.toml          # [project.scripts] relaycli = "relaycli.cli:app"
├── README.md
├── .env.example
├── relaycli/
│   ├── __init__.py
│   ├── cli.py              # interactive REPL (default) + `relaycli -p "<req>"` one-shot
│   ├── repl.py             # prompt_toolkit loop + slash-commands
│   ├── agent.py            # core agent loop: LLM ↔ tools until done
│   ├── config.py           # pydantic-settings
│   ├── llm.py              # LiteLLM wrapper
│   ├── tools/              # read_file, edit_file, write_file, run_command, search
│   ├── context.py          # CWD awareness, .gitignore respect
│   ├── permissions.py      # suggest / auto-edit / full-auto + approval prompts
│   ├── session.py          # history + token budget
│   └── render.py           # Rich rendering
└── tests/
```

**PERMISSION MODES (safety model — implemented later, but design for it now)**
- suggest (default): ask before any edit/command
- auto-edit: auto-apply edits, ask before commands
- full-auto: no prompts (print a banner)

**GLOBAL RULES (every stage)**
- All LLM calls go through `llm.py`. Tools defined once with Pydantic schemas, exposed via LiteLLM tool-calling.
- All code/command execution honors the active permission mode.
- Respect `.gitignore`; never auto-load `.env`/secret files into context.
- Every file edit shown as a diff before/at apply. Never silently overwrite.
- All secrets from config/env. Never hardcode keys.
- Strong typing + Pydantic. Small single-purpose modules. Don't add modules outside the structure. Don't invent APIs/services not listed.

**THIS STAGE — SCAFFOLD ONLY**
1. `pyproject.toml` with the console-script entry (`relaycli = relaycli.cli:app`), Python 3.12, all deps above pinned.
2. The full folder structure above with **stub modules** (each file present: module docstring + TODO, no real logic yet).
3. `config.py`: a working pydantic-settings `Settings` class (model name, provider API keys from env, default permission mode = `suggest`) loading from env/`.env` and `~/.relaycli/config.toml`. This one should actually work.
4. `cli.py`: a minimal Typer app so `relaycli` runs and prints a banner (name, version, CWD, model, mode); `relaycli --help` works. No agent yet.
5. `.env.example` (provider keys + default model) and a README skeleton (install + run).

**VERIFY**
- `pipx install -e .` (or `uv tool install .`) succeeds and puts `relaycli` on PATH.
- `relaycli` prints the banner; `relaycli --help` works.
- The banner/`relaycli config` shows which provider keys are detected.

Stop after this stage. Report what you created and confirm VERIFY passes.

---
════════════════════════════════════════════════════
## PROMPT 2 — LLM & TOOL-CALLING FOUNDATION
*Kapan dipakai: setelah scaffold lolos.*
════════════════════════════════════════════════════

Continuing RelayCLI (installable terminal coding agent, Python). This stage: the LLM layer + proof that tool-calling works. Still **no full agent loop and no UI**.

**BUILD**
1. `llm.py` — a LiteLLM wrapper exposing:
   - a `complete()` / `stream()` that takes messages + a list of tool schemas, calls the configured provider/model via LiteLLM, supports streaming, and returns the model's text and any tool calls in a normalized shape.
   - usage/cost capture from LiteLLM (tokens + estimated cost per call).
   - provider/model from config; graceful fallback or a clear error if the key/model is missing.
2. A minimal tool-schema mechanism (in `tools/__init__.py`): register a tool (name, description, Pydantic args schema) and emit the JSON schema list LiteLLM tool-calling expects. Add ONE throwaway tool (e.g. `get_time`) just to exercise the round-trip.
3. A tiny manual harness (a hidden `relaycli debug-llm` command or a script): send a prompt that should trigger the throwaway tool, execute it, send the result back, and print the final model reply — proving the full round-trip.

**KEY RULES**: all model access only through `llm.py`; keys from config/env; clear errors not stack traces.

**VERIFY**
- With one provider configured (Ollama needs no key, or set an API key), the harness completes a full round-trip: model → tool call → tool result → final answer.
- Streaming prints incrementally.
- Token/cost is captured and shown.

Stop. Report + confirm VERIFY. (We'll remove the throwaway tool next stage.)

---
════════════════════════════════════════════════════
## PROMPT 3 — TOOLS & PERMISSION SYSTEM
*Kapan dipakai: setelah round-trip LLM lolos.*
════════════════════════════════════════════════════

Continuing RelayCLI. This stage: the real coding tools + the permission/approval system. **No agent loop yet** — tools must be independently testable. Remove the throwaway `get_time` tool from the previous stage.

**BUILD the tools** (each its own module under `tools/`, registered with a Pydantic args schema):
- `read_file(path)` → file contents (reasonable size limit; refuse/flag binary).
- `search(query, path?)` → ripgrep (shell `rg`) with Python fallback; returns `file:line` matches.
- `write_file(path, content)` → create/overwrite (goes through the diff + permission flow).
- `edit_file(path, …)` → apply a targeted change; render a unified colored diff (difflib + Rich) before→after; apply per permission mode.
- `run_command(cmd)` → run a shell command in the CWD; capture stdout/stderr/exit code; apply per permission mode.

**BUILD `permissions.py`**
- Modes: suggest (default), auto-edit, full-auto.
- An approval gate used by edit/write/run: in suggest, prompt y/n (show the diff or the command first); in auto-edit, auto-apply edits but prompt for commands; in full-auto, no prompts.

**BUILD `context.py`**
- Respect `.gitignore`; refuse to read `.env`/secret-like files unless explicitly targeted; resolve paths safely **within the project root** (block traversal outside CWD via `..`, absolute paths, symlinks).

**KEY RULES**: every edit shows a diff before apply; `run_command` never runs without honoring the mode; no path escapes the project root; nothing hardcoded.

**VERIFY** (quick script or unit tests)
- read_file / search work on a sample repo.
- write_file / edit_file show a diff and respect suggest vs full-auto.
- run_command captures output and is blocked without approval in suggest mode.
- Reading outside the project root or a `.gitignore`d path is refused/flagged.

Stop. Report + confirm VERIFY.

---
════════════════════════════════════════════════════
## PROMPT 4 — AGENT LOOP (THE CORE)
*Kapan dipakai: setelah tools + permission lolos.*
════════════════════════════════════════════════════

Continuing RelayCLI. This stage: the heart — the agent loop tying the LLM (Prompt 2) and the tools (Prompt 3) together. After this, RelayCLI can actually do work even with a basic UI.

**BUILD**
1. `session.py` — conversation history (system + user/assistant/tool messages) + token-budget management (trim oldest near a configurable limit).
2. `agent.py` — the core loop:
   - Add the user request to the session.
   - Call `llm.py` with the full tool schemas.
   - If the model returns tool calls → execute each via the tool registry (honoring permissions), append results to the session, loop again.
   - If the model returns a final answer with no tool calls → task done, return.
   - Cap total iterations (configurable, e.g. 50) to prevent runaway loops.
   - Build a focused system prompt: RelayCLI's role, the available tools, the CWD, the active permission mode.
3. A temporary `relaycli run "<request>"` command running one full agent loop end-to-end (plain tool-activity printing for now), so the loop is testable before the nice REPL exists.

**KEY RULES**: all LLM via `llm.py`; all tool exec via the registry + permissions; enforce the iteration cap; no module sprawl.

**VERIFY**
- `relaycli run "create a file notes.txt containing today's date"` → agent uses write_file (asks in suggest), creates it, reports done.
- `relaycli run "find every TODO in this project and list them"` → agent uses search and summarizes.
- A multi-step request (read → edit → run) completes across several loop iterations.
- The iteration cap stops a pathological loop cleanly.

Stop. Report + confirm VERIFY.

---
════════════════════════════════════════════════════
## PROMPT 5 — TERMINAL UX (REPL, CLI, RENDERING)
*Kapan dipakai: setelah agent loop lolos.*
════════════════════════════════════════════════════

Continuing RelayCLI. This stage: make it feel like Claude Code/Codex — the interactive terminal experience. **Keep the agent/tool/permission logic untouched**; this stage is presentation + entry only.

**BUILD**
1. `render.py` (Rich): streaming model text; a compact activity line per tool call (`● read api/users.py`, `● edit api/users.py (+12 −3)`, `● run pytest → passed`); colored diffs; a clean end-of-task summary (steps, tools used, elapsed time, estimated cost).
2. `repl.py` (prompt_toolkit): interactive session loop with input history + multi-line. On launch print CWD / model / mode. Each user line runs the agent loop and streams output. Slash-commands:
   - `/model <name>` — switch model
   - `/mode <suggest|auto-edit|full-auto>` — switch permission mode (banner on full-auto)
   - `/diff` — show pending/last changes
   - `/clear` — reset session
   - `/exit` — quit
3. `cli.py`: `relaycli` (no args) → interactive REPL (default). `relaycli -p "<request>"` → run the agent loop once non-interactively and exit. `--model` / `--mode` flags at launch. Replace the temporary `run` command from Prompt 4.

**KEY RULES**: don't touch agent/tool/permission internals; honor permission modes in the UI prompts.

**VERIFY**
- `relaycli` opens an interactive session; a request streams text + tool activity and applies changes per mode.
- `/model` switches between two providers (e.g. an OpenAI model and a local Ollama model); `/mode` switches live with a full-auto banner.
- `relaycli -p "create hello.py that prints hello"` creates the file and exits.

Stop. Report + confirm VERIFY.

---
════════════════════════════════════════════════════
## PROMPT 6 — TESTS, ACCEPTANCE VERIFY & DEBUG
*Kapan dipakai: setelah fitur MVP lengkap. (Tahap debugging.)*
════════════════════════════════════════════════════

Continuing RelayCLI. The MVP feature set is built. This stage: lock it down — real tests, a full acceptance run, and fix every bug at the **root cause** (don't patch symptoms; don't claim "fixed" unless the cause is understood).

**DO**
1. Write the pytest suite:
   - `tests/test_tools.py` — read/search/write/edit/run, incl. permission behavior + path-safety (no escaping project root, `.gitignore` respected).
   - `tests/test_permissions.py` — suggest vs auto-edit vs full-auto gating.
   - `tests/test_agent_loop.py` — **MOCK the LLM** (no real API): verify a full read→edit→run→done cycle, a forced tool-error → recovery, and the iteration cap.
2. Run the full acceptance scenario end-to-end on a sample repo:
   - install via pipx/uv → `relaycli` on PATH
   - "add a docstring to every function in utils.py, then run the tests" → reads, proposes diffs (asks in suggest), on approval runs tests, feeds results back
   - `/mode full-auto` then a request completes with no prompts + banner
   - `relaycli -p "..."` one-shot works
   - switching models between two providers works
3. For EVERY failure or flaky behavior: find the root cause, fix it, re-run until green. Harden these edge cases: missing/invalid API key, provider/network error, a command that fails, a very large file, an empty repo, an interrupted (Ctrl-C) session, and a model returning malformed tool args.

**OUTPUT**: a passing `pytest`, a clean acceptance run, and a short report listing each bug → root cause → fix.

Stop. Report results.

---
════════════════════════════════════════════════════
## PROMPT 7 — SECURITY & BUG AUDIT
*Kapan dipakai: paling akhir. (Pengecekan keamanan & bug.)*
════════════════════════════════════════════════════

Continuing RelayCLI. Final stage: a dedicated **adversarial security + correctness audit** of the whole codebase, then fix what you find. Review it as a senior security engineer auditing a tool that edits real files and runs shell commands on a user's machine.

**AUDIT for (and fix each finding)**
1. **Command execution** — can `run_command` be abused (injection, chained/destructive ops) regardless of mode? Is full-auto clearly gated + signposted? Any path where a command runs without honoring the mode?
2. **File-system safety** — path traversal: can any tool read/write outside the project root via `..`, absolute paths, or symlinks? Are writes always diff-shown and mode-gated?
3. **Secret leakage** — are `.env` / credential files excluded from context and from anything sent to the model? Is `.gitignore` actually honored? Are API keys ever logged, printed, or written to history/disk?
4. **Prompt injection** — file contents and command output are untrusted. Can malicious text coerce the agent into running commands or exfiltrating data? Add a clear trust boundary; never auto-escalate permissions based on model output.
5. **Config & keys** — keys only from env/secure config, never hardcoded/committed; `.env.example` has no real values.
6. **Dependencies** — versions pinned; flag anything unmaintained or risky.
7. **Failure modes** — no secrets in stack traces; safe handling of malformed model/tool output; no silent overwrites.

**OUTPUT**: a security report — each finding with severity (low/med/high/critical), the risk, and the fix applied. Apply fixes highest-severity first. Re-run `pytest` after fixes to confirm nothing broke.

This is the last stage. Report findings, fixes, and final test status.

---

### Setelah 7 prompt ini
MVP single-agent kamu jalan. Layer berikutnya (yang bikin dia **RelayCLI**, bukan sekadar aider): **multi-agent relay** (Planner → Coder → Reviewer + reflection/retry) dan **smart model router** (model murah buat planning, model kuat buat coding) di atas fondasi ini. Itu prompt pack terpisah kalau core-nya sudah stabil.
