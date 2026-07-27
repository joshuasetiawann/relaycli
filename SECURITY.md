# RelayCLI — Security & Correctness Audit (Prompt 7)

Adversarial audit of the whole codebase, reviewed as a tool that edits real
files and runs shell commands on a user's machine. Findings were produced
across 8 dimensions and each was independently verified by three skeptical
reviewers (exploitability / code-accuracy / severity) before being accepted.

**Result:** 18 findings confirmed, 3 dependency-hygiene items reviewed and
accepted, 0 false positives. All fixed items ship with regression tests.
**`pytest`: 107 passed** (was 79; +28 audit regression tests).

Fixes are listed highest-severity first.

---

## HIGH

### H1 — `read_file` secret guard was bypassable by the model itself
*secret-leakage · `relaycli/tools/read_file.py`*

**Risk.** The secret/git-ignored filter was gated on a model-supplied
`force=true` argument, and `read_file` had no permission gate at all. A prompt
injection in any file/command output could make the agent read `.env`,
`*.pem`, `id_rsa`, `.git-credentials`, etc. and ship the contents to the LLM
provider — silently, even in the default `suggest` mode.

**Fix.** Removed the model-controlled `force` field entirely. Secret-like reads
now require explicit **human** approval via a new always-prompt `read_secret`
permission action that is **never** auto-approved by any mode (not even
full-auto); merely git-ignored files use the normal mode-gated `read` action.
The trust boundary is now enforced by `PermissionManager`, not by model output.

### H2 — Rich-markup injection hid command payloads from the human approver
*prompt-injection · `relaycli/tools/run_command.py`, `render.py`, `agent.py`*

**Risk.** Model-controlled strings (commands, tool arguments, summaries) were
printed with Rich markup enabled, so a crafted command could render its
dangerous portion invisibly (e.g. black-on-black) — the human approves what
looks benign while `shell=True` runs the full string. Bracketed commands could
also raise `MarkupError`.

**Fix.** `rich.markup.escape()` applied to every model-derived string at every
console sink: the `run_command` command preview and summaries, `RichReporter`,
and `PlainReporter` tool lines, plus the edit/write/read approval prompts. The
human now always sees the exact command.

### H3 — A project-local `.env` could silently escalate to full-auto
*config-keys · `relaycli/config.py`*

**Risk.** Settings were read from a CWD `.env` ranked above the user's own
config. A cloned/untrusted repo could ship `.env` with
`RELAYCLI_PERMISSION_MODE=full-auto` (→ unattended arbitrary command
execution) and/or `OLLAMA_BASE_URL=http://attacker/` (→ file contents
exfiltrated to an attacker endpoint on the very next model call).

**Fix.** A `_FilteredSource` wrapper strips the security-relevant fields
(`permission_mode`, `ollama_base_url`, matched case-insensitively across all
alias spellings) from the CWD `.env` source only. These may still be set via
the real environment, the user-owned `~/.relaycli/config.toml`, or `--mode`.
Provider keys and `RELAYCLI_MODEL` still load from `.env` as documented.
`.env.example` updated to reflect this.

---

## MEDIUM

### M1 — `read_file` read the whole file before applying `max_bytes`
*fs-safety · `read_file.py`* — **Fixed.** The read is now bounded
(`fh.read(max_bytes + 1)`), so a multi-GB file can't exhaust process memory;
the true size is still reported via `stat()`.

### M2 — `edit_file` silently corrupted non-UTF-8 files
*fs-safety · `edit_file.py`* — **Fixed.** The edit path now reads raw bytes,
refuses binary (NUL sniff) and non-UTF-8 (strict decode) files instead of a
lossy `errors="replace"` round-trip that rewrote bytes as U+FFFD invisibly.

### M3 — `run_command` leaked provider keys held in the environment
*secret-leakage · `run_command.py`* — **Fixed.** Spawned commands run with a
scrubbed environment: RelayCLI's known provider-key variables are removed (kept
narrow so unrelated tokens like `GITHUB_TOKEN` survive). `llm.py`'s docstring
corrected to match reality.

### M4 — Ctrl-C mid-tool broke every subsequent REPL turn
*failure-modes · `agent.py`* — **Fixed.** A `KeyboardInterrupt` during tool
execution left an assistant `tool_calls` message with no matching results,
which providers reject. The loop now stubs a tool result for the interrupted
call **and** all not-yet-started calls in that turn before re-raising.

### M5 — Non-atomic writes could destroy a file on a mid-write failure
*failure-modes · `write_file.py`, `edit_file.py`, `tools/base.py`* — **Fixed.**
New `atomic_write()` helper writes to a same-directory temp file, fsyncs, then
`os.replace()`s it in — preserving the original's mode and never truncating on
ENOSPC/interrupt. Temp files are cleaned up on any failure.

### M6 — Token-budget `trim()` was a no-op on the primary (one-shot) path
*correctness · `session.py`* — **Fixed.** Added a within-turn fallback that
sheds the oldest complete assistant+tool-results group (keeping the leading
user message and the most recent group, preserving tool-call/result pairing) so
the budget is enforced even when there is a single user turn.

---

## LOW (all fixed unless noted)

- **L1 — timeout killed only the shell, not the process group.** `run_command`
  now launches with `start_new_session=True` and `killpg`s the whole group on
  timeout/overflow, so piped/backgrounded children don't orphan.
- **L2 — output was buffered unbounded (OOM risk).** Output is now drained
  concurrently with a hard 20 KB/stream cap; on overflow the process group is
  killed immediately rather than waiting out the timeout.
- **L3 — `is_secret` missed `credentials.json`, `.pypirc`, `kubeconfig`, …**
  Broadened `SECRET_NAMES`/`SECRET_PATTERNS` (`*credentials*`, etc.).
- **L4 — `~/.relaycli` + REPL history were world-readable.** `ensure_config_dir()`
  creates the dir `0700` and the history file `0600` (prompts can contain
  secrets).
- **L5 — `_normalize` crashed on an empty/blocked provider response.** Guarded
  empty `choices` and moved normalization inside the error-wrapping `try`, so it
  surfaces as `LLMError`, not a raw traceback.
- **L6 — streaming under-reported tokens/cost.** Added
  `stream_options={"include_usage": True}` (real provider usage) and included
  tool-call payloads in the fallback estimate.
- **L7 — TOCTOU on validated paths (re-open by name).** *Accepted (documented).*
  The verified, honest conclusion: this grants no capability beyond
  `run_command` (which is arbitrary unconfined shell by design), requires a
  concurrent local attacker who already has write access, and RelayCLI runs
  unprivileged. A correct fix needs `openat2`/`RESOLVE_BENEATH` (Linux-only,
  semantics-changing). Tracked as future hardening, not an exploitable defect.

---

## Accepted by design

- **`run_command` uses `shell=True` and, in full-auto, runs without prompts.**
  This is the tool's advertised function (running `pytest`, builds, etc.), and
  full-auto is an explicit, banner-signposted user opt-in. The residual risk is
  materially reduced by the fixes above: full-auto can no longer be entered
  silently (H3), provider keys are scrubbed from commands (M3), and every
  command preview is now truthful (H2). We intentionally did **not** change
  full-auto's documented "never prompt" contract.

- **Dependency hygiene (3 plausible items).** No lockfile/hashes for the
  transitive closure, a pre-1.0 transitive dep (`annotated-doc`), and an
  unbounded `requires-python`. Verification concluded none is a concretely
  exploitable defect: a repo-committed lockfile does not affect `pip install`
  from PyPI, and an upper `requires-python` bound is against PyPA guidance. The
  7 direct deps are already `==`-pinned. Left as-is by deliberate decision.

---

## Config & keys (clean)

No hardcoded/committed secrets; `.env.example` contains only placeholders;
keys come solely from env / user config and are passed to LiteLLM as kwargs
(never exported to the process env). `.gitignore` correctly excludes `.env`.
