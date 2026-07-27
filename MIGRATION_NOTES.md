# MIGRATION_NOTES.md
<!-- Phase 2 closing summary appended near the top for visibility; detailed
     per-pair notes are further down in this file, in the order they were
     written during the work. -->


Living decision log for the RelayCLI repair/migration/re-verify task.
Branch: `repair/merge-and-migration`.

---

## Phase 2 — architecture migration completion

**2a — dead shadowed modules.** `relaycli/agent.py` (deleted in Phase 1, dead
per its 28-conflict-block status), `relaycli/config.py`, `relaycli/mcp.py`
(both unconflicted, deleted here) — all three confirmed dead via
`importlib.util.find_spec`, which resolved `relaycli.agent`/`.config`/`.mcp`
to their same-named packages' `__init__.py` even before deletion (Python's
package-over-module shadowing), and confirmed live after deletion by
re-running the same check plus `import relaycli.<name>` for each.

**2b — the 12 duplicate pairs.** Diffed every pair before touching anything.
Nine were functionally identical or already-reconciled shims — trivial to
finish:

- `context.py`, `roles.py`, `roster.py`, `project_hints.py`, `render.py` —
  already thin re-export shims from Phase 1 conflict resolution, parity
  already verified then. Deleted the flat file, rewrote every importer to
  the layered path.
- `permissions.py`, `memory.py`, `router.py`, `session.py` — diffed
  byte-for-byte functionally identical to their layered counterpart; only
  docstrings/comments differed (the flat side consistently had the richer
  prose — ported that into the layered file, kept the layered file's already-
  correct internal imports). Deleted the flat file, rewrote every importer.

Import rewriting for these 9 done with a small regex script
(`/tmp/.../scratchpad/rewrite_imports.py`) rather than hand-editing ~23 call
sites — anchored to statement-start (`^\s*from relaycli\.<mod>\b`,
`^\s*import relaycli\.<mod>\b`, and the bare `from relaycli import <mod>[ as
x]` form) so it can't touch a string or comment that merely mentions one of
these names. Verified after running: `grep -rnE 'from relaycli\.(context|
roles|roster|project_hints|permissions|memory|router|session|render)\b'`
returns nothing, `compileall` clean, and a live `import` of all 9 layered
modules plus `relaycli.cli` and `relaycli.tools.default_registry()` (21
tools) all succeed.

The remaining three pairs — `llm.py` ↔ `core/llm.py` (28 KB vs 19 KB),
`repl.py` ↔ `ui/repl.py` (41 KB vs 31 KB), `web.py` ↔ `ui/web.py` (37 KB vs
21.6 KB) — are large enough that diffing them by hand is real, independent
work per pair (disjoint files, no shared state), so this part was delegated
to three parallel research/reconciliation agents rather than done serially.
Findings appended below once each reports back.

### `llm.py` ↔ `core/llm.py` (agent-reconciled, self-reviewed)

`relaycli/core/llm.py` rewritten as the union of both sides. Notable finds:

- **Real bug fixed**: `ollama_host_label` existed in flat `llm.py` but was
  missing from `core/llm.py` entirely — yet `relaycli/ui/render.py` and
  `relaycli/ui/web.py` already did `from relaycli.core.llm import ...
  ollama_host_label`. That was a live, latent `ImportError` waiting to
  happen the moment either module's lazy import actually executed. Fixed by
  including it.
- **Real bug fixed**: `LLM._wrap_error`'s core version dropped flat's
  fallback branch for an unresolvable-provider `AuthenticationError`, so it
  produced no "rejected" hint text at all.
  `test_llm_unit.py::test_wrap_error_auth_failure_unknown_provider_still_hints`
  requires it; verified live after the fix.
- `tool_capability_warning` message wording: kept flat's ("plain text"),
  required by `test_tool_capability_warning_for_risky_local_model`.
- A few judgment calls where no test disambiguates exact wording (error
  message text in `_missing_key_message`, `_credential_kwargs`) — kept
  flat's fuller text, flagged, low risk (only substring assertions exist).

### `repl.py` ↔ `ui/repl.py` (agent-reconciled, self-reviewed)

Took flat `repl.py` (the tested anchor — `tests/test_ux.py` imports it
directly and is 1276 lines of coverage) as the base, with import paths
corrected to their layered equivalents. Net diff against flat is now three
hunks: one docstring cross-reference, and two `relaycli.config` →
`relaycli.core.config` import fixes. Everything else is byte-identical.

- **Real bug found**: `ui/repl.py`'s old `/relay`, `/agents`, `/skill auto`
  usage strings passed unescaped `[on|off]` to `Console.print`, which Rich
  interprets as a markup tag and silently drops — verified live, the
  printed output was `"Usage: /relay "` with the options missing entirely.
  Flat's `\[on|off]` (escaped) is correct and is what survives.
- Confirmed `_ensure_runnable_local_model`'s flat behavior (blocks
  `agent.run()` when no fallback model exists) is required by
  `test_repl_skips_agent_for_slow_local_model`; `ui/repl.py`'s old version
  always returned `True` and would have run the agent anyway.
  `recommended_fast_local_model`/`slow_local_model_warning` also must stay
  bound at module scope (not a local import) — tests monkeypatch
  `repl_mod.recommended_fast_local_model` directly.
- `_try_frontend_scaffold` and the `local_scaffolds` gate in `_handle_line`
  were missing from `ui/repl.py` entirely — six dedicated tests
  (`test_repl_frontend_*`) require them.
- The agent also added a new `except Exception` guard around
  `agent.run()` in `_run_agent`, reasoning that an unexpected internal bug
  would otherwise crash the whole interactive session. It's a reasonable
  idea but it's a **behavior change** with no test requiring it, and rule 5
  ("behaviour is frozen during Phases 1–3") applies — removed it here so
  Phase 2 stays a pure migration/no-op-behavior step. Revisit in Phase 5c
  (actionable error messages) as a deliberate, reviewed addition instead of
  a side effect of file reconciliation.

**Two more uncatalogued duplicate pairs surfaced, not in the master
prompt's list of 12 or its list of 15 "unique" flat modules — flagging per
rule 6 rather than silently fixing or ignoring:**

- `relaycli/relay.py` (594 lines) vs `relaycli/agent/pipeline.py`
  (256 lines). The master prompt explicitly lists `relay.py` as one of the
  fifteen "unique flat modules... do not migrate." That's the right call in
  practice — `tests/test_relay.py` imports `Relay`/`RelayResult` etc. from
  `relaycli.relay` directly, and `cli.py` does too — but `agent/pipeline.py`
  is a real, independently-importable, much-less-complete second copy of
  the same classes (`Relay`, `RelayResult`, `RelayObserver`, `RoleRun`,
  `parse_tasks`, `parse_verdict`, ...), re-exported (but never actually
  used) via `relaycli/agent/__init__.py`. Left both alone: `relay.py` per
  explicit instruction, `agent/pipeline.py` because nothing live reaches it
  (confirmed via grep — its only consumer is its own unused re-export).
  `relaycli/ui/render.py`'s TYPE_CHECKING import was pointed at
  `relaycli.relay` (the live one), not `relaycli.agent.pipeline` (which the
  broken minimal render.py had mistakenly used — see below).
- `relaycli/config_menu.py` (flat, also explicitly listed as "unique, leave
  alone") vs `relaycli/config/menu.py` (layered). Same shape: identical
  class/function names (`ConfigMenu`, `SettingsMenu`, `run_configuration`,
  `run_settings`), `tests/test_config_menu.py` and every real call site
  (`repl.py`, `cli.py`, `config_cli.py`) use the flat one; `config/menu.py`
  is only reached from `config/manager.py`'s own (currently unmounted —
  see the `cli.py` decision above) `config_app`. Left both alone for the
  same reason.

Neither of these is currently causing a test failure or runtime error, so
no fix was needed — but both are exactly the kind of "two homes for one
module" state Phase 2 is supposed to eliminate, and the master prompt's own
enumeration missed them. Recommending a follow-up pass to delete
`agent/pipeline.py` and `config/menu.py` outright (they're unused) once
this repair lands, so the migration is actually finished rather than
finished-except-for-two-more.

### `relaycli/ui/render.py` — a real gap my own Phase 1 verification missed

While reviewing the llm.py agent's report, its call to
`best_ollama_model()`/`ollama_host_label()` with zero arguments in
`ui/render.py` (both functions require `settings: Settings`) turned out to
be a symptom of something much bigger: `relaycli/ui/render.py` was not an
incomplete migration of the flat `render.py` I read during Phase 1 — it was
a **different, deliberately minimal reimplementation** (its own docstring:
`"Minimal clean UI — inspired by opencode / claude code."`), missing or
altering most of the behavior the test suite actually depends on:
`render_setup_panel` didn't even reference its own `detected` parameter, had
no `Panel`, no "Manual fixes" section, and none of the security-conscious
regex extraction that stops a crafted model id from spoofing a fake
`_API_KEY` export hint (`test_setup_panel_export_hint_ignores_spoofed_model_id`
directly tests for this); `render_welcome` had no Claude-Code panel styling,
no "whole home directory" warning
(`test_banner_warns_when_workspace_is_home` — a named regression guard for
a real 2026-07-03 incident); `render_task_summary`'s condition for whether
to reprint the final text checked `== "error"` instead of `!= "done"`,
which silently drops the message on `max_iterations`
(`test_render_task_summary_shows_max_iterations_text`); `render_help`'s
command table didn't match `SLASH_COMMANDS`
(`test_slash_help_matches_slash_commands_registry`); several `console.print`
calls were missing `escape()` on model-id/route text that the flat version
explicitly escaped with a comment calling out untrusted input.

**Root cause of why my Phase 1 check missed this**: I verified "symbol
parity" by grepping `^(class |def )` on both sides and confirming every
name in the flat version also appeared in the layered target — that check
is necessary but not sufficient, since it says nothing about whether the
*bodies* match. This is now corrected by fully rewriting
`relaycli/ui/render.py` to match the tested flat content (verified during
Phase 1), with only its internal imports adjusted to layered paths. Given
how consequential this was, the other four Phase-1 shim files (`context.py`,
`roles.py`, `roster.py`, `project_hints.py` → their `core.*` targets) were
re-verified by diffing full function bodies against the original `acb74e0`
git blobs (not just re-checking symbol names) — confirmed genuinely
cosmetic-only (import-path + docstring/formatting differences, zero logic
changes) in all four cases. This render.py case really was the outlier.

### `web.py` ↔ `ui/web.py` (agent-reconciled, self-reviewed)

Took flat `web.py` as the base (parity-checked live: constructed a
`WebSession`, drove `state()`/`send()`/the HTTP handler, including
DNS-rebind/cross-origin guards). Diff against flat is 12 hunks, all either
import-path corrections or the one genuinely necessary content change
(`UI_PATH`'s relative path, adjusted one directory deeper for the file's new
location — still resolves to the same `relaycli/web_ui.html`).

- **Real bug found and reproduced**: old `ui/web.py`'s `_onboarding_status()`
  imported `ollama_host_label` from `relaycli.core.llm`, which (at the time)
  didn't define it — every `state()` call, i.e. every `/api/state` poll from
  the desktop UI, raised `ImportError`. The main endpoint of the web server
  was dead on arrival. (This class of bug is also what the `llm.py`
  reconciliation fixed independently — `core/llm.py` now has
  `ollama_host_label`; the two agents converged on the same root cause from
  different directions.)
- Missing from old `ui/web.py`, all confirmed real and tested: the roster
  API (`_roster()`, `set_roster()`, `POST /api/roster` —
  `test_state_includes_full_roster_and_set_roster`), Ollama pull
  (`pull_ollama()`, `POST /api/ollama/pull` —
  `test_pull_ollama_records_start_and_done`), the frontend-scaffold branch in
  `send()` (5 tests, e.g. `test_send_frontend_shop_scaffold_runs_locally_without_llm`
  asserts no LLM call happens), and the slow-local-model auto-switch (same
  `recommended_fast_local_model` pattern as `cli.py`/`repl.py` — 4 tests
  patch it at module scope).
- **Reconfirms the `agent/pipeline.py` finding from a second, independent
  angle, and makes it more serious**: comparing `relay.py` against
  `agent/pipeline.py`'s `Relay` directly (not just symbol names), the
  layered copy's `_run_tasks` silently ignores a task's `[role]`
  specialist tag and always builds a plain Coder regardless of task-split
  assignment — the delegation feature exists in name only there. Its
  `_SECURITY_BLOCK` is also truncated to one line, dropping the
  anti-prompt-injection / anti-secret-exfiltration instructions every real
  role prompt carries elsewhere. Nothing live reaches this code path today
  (confirmed above), but if anyone ever wires `relaycli.agent.pipeline.Relay`
  in instead of `relaycli.relay.Relay` — an easy mistake, since
  `relaycli.agent` re-exports it under the same names — they'd get a
  silently degraded, less secure pipeline with no error to signal it.
- Two pre-existing test-fixture issues reproduced identically regardless of
  which `web.py` is used (not introduced by this repair, not fixed): (1)
  `mcp/bridge.py`'s `extend_registry()`/`enabled_servers()` resolve through
  the module's own globals, so `test_web_run_wires_mcp_tools`'s patch on the
  `relaycli.mcp` re-export path may not reach it; (2) the
  `appconfig.CONFIG_FILE` monkeypatch in `test_web.py`'s fixture looks like
  it has the same shim-indirection gap as other ambient-config leaks seen
  elsewhere in this codebase's test suite. Flagging both for the Phase 3
  gate run to confirm whether they actually fail, rather than guessing
  further here.

### Phase 2 close-out

All 12 catalogued duplicate pairs reconciled to one canonical (layered)
location; all 3 dead shadowed modules deleted; every import across
`relaycli/` and `tests/` rewritten, including three forms a plain import
rewrite would miss (a `__import__("relaycli.context", ...)` call in
`test_memory.py`, a string-literal `monkeypatch.setattr("relaycli.web...", )`
target in `test_web.py`, and a parametrized list of module-name strings in
`test_scaffold.py::test_all_modules_importable` — the last one is exactly
the master prompt's rule 1 case: the test's assertion was correct and
untouched, only the path *strings* it parametrized over were the obsolete
part). Verified clean two ways: a repo-wide grep for any of the 12 old
names in any import form, and `importlib`/`pkgutil.walk_packages` actually
importing every single submodule under `relaycli` with zero errors — this
is Phase 3's gate G8, already green ahead of schedule.

Two uncatalogued dormant duplicates (`agent/pipeline.py`, `config/menu.py`)
were found and deliberately left alone (nothing live reaches either); see
above for why, and the final report for the recommendation to delete them
in a follow-up pass.

---

## Baseline (Phase 0)

Measured directly, 2026-07-27, before any fix was applied. All ground-truth
claims in the master prompt were re-verified and confirmed accurate; the one
correction is a mechanical detail about *how* to check them (see below).

**Git state:** This is a real, in-progress `git merge`, not just files that
happen to contain marker text.

- `git log --oneline` on `main` shows exactly one commit: `fd85c60 Initial commit`.
- `git status` shows `unmerged paths` and one clean staged addition
  (`.github/workflows/ci.yml`, added by the incoming side, non-conflicting).
- `.git/MERGE_HEAD` = `acb74e0313133d20fea0a7a26b757d8523896089` — this commit
  **does** exist as a real object in the repo (confirmed with
  `git cat-file -e acb74e0313133d20fea0a7a26b757d8523896089^{commit}`); it's
  just not reachable from any branch tip, which is normal for a merge parent
  mid-merge. `git log` alone (which only walks HEAD's ancestry) will not show
  it — `git status`/`MERGE_HEAD` is the correct thing to check first, and is
  literally step 1 of Phase 0 for exactly this reason.
- `.git/MERGE_MSG`: `Merge branch 'main' of https://github.com/joshuasetiawann/relaycli`,
  listing the same 25 conflicted paths enumerated below.
- Correction to my own first instinct: I initially ran `git log`/`git reflog`
  before `git status` and (wrongly) concluded there was no real merge in
  progress, since only one commit exists on `main`. That was premature — a
  merge-in-progress lives in the index/`MERGE_HEAD`, not in committed history.
  `git status` resolved it immediately. No discrepancy with the master
  prompt's premise after all; noting this only so the reasoning trail is
  honest.

**Conflict-marker files:** 25, exact match to the master prompt's list (verified
via `grep -rln '^<<<<<<< ' --include='*.py' --include='*.toml' . | grep -v .venv`
and cross-checked against `.git/MERGE_MSG`'s conflict list — identical sets).

**`python -m compileall -q relaycli tests`:** 24 files fail with
`SyntaxError: invalid syntax` at their first `<<<<<<< HEAD` line (the 25th
conflicted file, `pyproject.toml`, is TOML, not a compileall target — so
"24 of 109" and "25 files with conflict markers" are consistent, not
contradictory). Full failing list matches the master prompt's file list exactly
(see `relaycli/__init__.py`, `relaycli/agent.py`, ... `tests/test_ux.py`).

**`python -c "import relaycli"`:** fails —
`SyntaxError: invalid syntax` at `relaycli/__init__.py:1`.

**`pip install -e ".[dev]"` (fresh `.venv-work`, Python 3.14.5):** fails before
touching any dependency — `tomllib.TOMLDecodeError: Invalid value (at line 24,
column 1)`. `pyproject.toml` itself has a conflict block inside the
`dependencies` array, so it isn't valid TOML yet.

**`pytest -q`:** fails immediately with
`ERROR: /mnt/storage/VSCode/Repo/RelayCLI/pyproject.toml: Invalid value (at line 24, column 1)`.
This is stronger than plain "collection errors" — pytest reads
`[tool.pytest.ini_options]` from `pyproject.toml` at startup, so it can't even
start, let alone collect. Root cause is the same broken TOML.

**Total source files:** `find . -name '*.py'` (excluding `.venv`, `.git`) = 109
(82 in `relaycli/`, 27 in `tests/`) — exact match.

**Dead shadowed modules** (flat `.py` file + same-named package, both present):
`relaycli/agent.py` (55,621 B) vs `relaycli/agent/`,
`relaycli/config.py` (10,227 B) vs `relaycli/config/`,
`relaycli/mcp.py` (20,951 B) vs `relaycli/mcp/`. Sizes match the master
prompt's claims. Only `agent.py` carries conflict markers (28 blocks, confirmed
via `grep -c` before edit); `config.py` and `mcp.py` are unconflicted plain
dead files — they don't appear in the 25-file conflict list at all, so no
merge decision is needed for them, only deletion in Phase 2a. Python package-
over-module shadowing will be confirmed empirically with
`importlib.util.find_spec` once `relaycli/__init__.py` is fixed (needed first
since resolving any submodule spec requires importing the parent package).

**`json-repair` / `pyyaml` dependency check** (master prompt Phase 1, special
case for `pyproject.toml`): both are genuinely imported by live code —
`yaml` by `relaycli/heuristics.py` (a unique flat module, always kept),
`json_repair` by `relaycli/agent/__init__.py` and `relaycli/agent/loop.py`
(the live layered package — *not* just by the soon-to-be-deleted
`relaycli/agent.py`). Decision: keep both dependencies from the HEAD side.

**Note for the final report (latent issue, not fixed):** `pyproject.toml`'s
`[project] version` field is `"0.4.14"` on *both* sides of the merge — it is
not inside a conflict block at all. Only `relaycli/__init__.py`'s
`__version__` symbol actually varies (`"0.5.0"` on HEAD vs `"0.4.14"` on
`acb74e0...`). Since Phase 1's mandate is conflict resolution, not new
behavior changes (rule 5, "behaviour is frozen during Phases 1-3"), I'm
keeping `pyproject.toml`'s version field as-is rather than bumping it to match
`__init__.py`. Package metadata version and `__version__` will disagree
(0.4.14 vs 0.5.0) until someone deliberately reconciles them — flagging this
rather than silently "fixing" it since it wasn't asked for.

---

## Phase 1 — per-file conflict resolution decisions

**Important correction to the master prompt's simplifying model.** The prompt's
framing ("HEAD = newer layered v0.5.0, always prefer it") holds for about half
these files, but for several files it is backwards: the `acb74e0...` side is
the more feature-complete/more recent one, and HEAD is behind. This isn't
guesswork — verified against (a) whether the supporting module a side imports
actually exists and has the needed symbols, and (b) whether the existing,
unconflicted test suite (`tests/test_memory.py`, `tests/test_tools.py`,
`tests/test_relay.py`, `tests/test_ux.py`, `tests/test_new_tools.py`) exercises
one side's behavior specifically. Per rule 6, every deviation from "keep HEAD"
below is logged with the evidence.

Fast path — **pure import-path difference, both sides otherwise byte-identical,
kept HEAD** (`relaycli.core.*`/`relaycli.ui.*` paths; target modules independently
verified to exist and export the needed names):
`relaycli/tools/create_folder.py`, `edit_file.py`, `find_files.py`, `list_dir.py`,
`read_file.py`, `write_file.py`.

**"Re-export shim" files — HEAD is a thin `from relaycli.core.X import *` (or
`ui.X`) shim; MERGE_HEAD (`acb74e0...`) is the pre-migration inline implementation.
Kept HEAD in every case, after confirming the shim target has full symbol parity
with what the inline version provides** (checked via `grep -nE '^(class |def )'`
on both sides — every top-level name in the inline version exists in the target):
- `relaycli/appconfig.py` → shim to `relaycli.config.manager` (parity confirmed:
  `AppConfig`, `load_app_config`, `save_app_config`, `set_base_model`,
  `set_runtime_option`, `recent_models`, `mask_key`, `resolve_provider_key`,
  `resolve_role_model`, `effective_roles`, plus `config.manager` additionally
  hosts the `config_app` Typer commands that used to live only in `config_cli.py`
  — a genuine consolidation, not a regression).
- `relaycli/context.py` → shim to `relaycli.core.context` (parity confirmed:
  `PathSafetyError`, `ProjectContext`, ignore/secret constants).
- `relaycli/render.py` → shim to `relaycli.ui.render` (parity confirmed: all 19
  top-level defs/classes, including `RichReporter`, `render_welcome`, etc.)
- `relaycli/roles.py` → shim to `relaycli.core.roles` (parity confirmed: all 15
  `BUILTIN_ROLES` entries, `builtin_role`).
- `relaycli/roster.py` → shim to `relaycli.core.roster` (parity confirmed:
  `roster_template`, `specialist_model`, `specialist_runtime`, `enabled_specialists`,
  `is_assignable`).
- `relaycli/project_hints.py` → shim to `relaycli.core.project_hints` (parity
  confirmed: `project_prompt_block`, `missing_path_hint`, `likely_web_files`).

**Files kept as HEAD's own (non-shim) content**, where HEAD's side is a strict
additive feature over MERGE_HEAD and nothing tests the older behavior:
- `relaycli/permissions.py` — HEAD adds `asyncio` + `confirm_async`/`Decision`
  async path. Kept because `relaycli/tools/base.py` (HEAD side, also kept)
  calls `self.permissions.confirm_async(...)` unconditionally from
  `ToolContext.confirm_async`, and the separate layered duplicate
  `relaycli/core/permissions.py` *already* has `confirm_async` independently —
  three independent pieces of evidence agreeing HEAD is the live direction here.
- `relaycli/tools/base.py` — kept HEAD entirely (the only functional diff across
  its 6 conflict blocks is `ToolContext.confirm_async`, needed per above; the
  rest are docstring-only diffs).
- `relaycli/session.py` — HEAD adds "smart trim": summarization fallback
  (`_summarize_oldest_turn`, Indonesian-language summary marker) on top of the
  turn-dropping MERGE_HEAD already has. No dedicated session test file exists
  either way, but `relaycli/core/session.py` (the Phase-2b duplicate-reconciliation
  target, built independently) *already* contains the identical summarization
  feature — strong triangulating evidence HEAD is the intended direction, not
  a stray addition.
- `relaycli/tools/__init__.py` → thin shim to `relaycli.tools.registry`. Verified
  `relaycli/tools/registry.py` exists (not aspirational) and its
  `_register_defaults()` really does register all 16 tools including the newer
  ones (`webfetch`, `websearch`, `question`, `todo`, `git_tool`, `apply_patch`,
  `think` — all confirmed present as real, substantial files, each covered by
  `tests/test_new_tools.py`).

**Deliberate deviations from "keep HEAD" — MERGE_HEAD (`acb74e0...`) kept instead,
each because HEAD's side is either missing tested functionality or is actively
wrong:**

- **`relaycli/cli.py` — kept MERGE_HEAD's entire `main()`/`_run_once` wholesale**,
  not a blend. Three independent tests only pass with MERGE_HEAD's shape:
  - `tests/test_relay.py:616,639,662` invoke
    `CliRunner().invoke(cli_module.app, ["-p", ..., "-y"])` — the `-y`/`--yes`
    flag (→ `assume_yes` on `PermissionManager`) exists **only** on MERGE_HEAD's
    `main()`; HEAD's callback has no such option at all.
  - `tests/test_ux.py::test_one_shot_greeting_uses_local_guide` expects a
    canned `"siap bantu"` reply for `-p "hi"` with no LLM call — only
    MERGE_HEAD's `_run_once` short-circuits through `local_reply_for()`. HEAD's
    `_run_prompt` always builds an `Agent` and calls the model.
  - `tests/test_ux.py::test_one_shot_relay_preflights_role_models` expects
    exit code 2 naming the missing key for a relay role model — only
    MERGE_HEAD's `_run_once` calls `preflight_settings()` before running.
  HEAD's `--desktop/--port` inline flags are superseded by MERGE_HEAD's separate
  `web`/`desktop` subcommands (equivalent capability, no test depends on the
  inline-flag form). HEAD's `-V` short alias for `--version` is dropped (not
  tested). Net effect: cli.py also gains the relay-pipeline dispatch,
  `skills_auto` block, frontend-scaffold detection, and MCP `extend_registry`
  wiring that HEAD's bare-bones `_run_prompt` never had.
- **`relaycli/repl.py` — kept MERGE_HEAD for both conflict blocks**
  (`recommended_fast_local_model` import + the `_ensure_runnable_local_model`
  auto-switch body). Evidence: `tests/test_ux.py`'s own conflicting fragments
  disagree on the test's *name* — HEAD's fragment pairs with a test named
  `test_repl_skips_agent_for_slow_local_model`, while a *separate*,
  **unconflicted** test named `test_repl_auto_switches_slow_local_model` exists
  whose body (itself conflicted) only makes sense — asserts the model
  actually changes, `"model auto-switch" in out` — under MERGE_HEAD's fragments.
  A test named "auto switches" asserting "auto-switch disabled" under HEAD's
  fragment would be self-contradictory. Also consistent with the `cli.py`
  decision above (same `recommended_fast_local_model` helper, same UX).
- **`relaycli/tools/remember.py` — kept MERGE_HEAD** (scope-aware: `project`
  vs `global`). `tests/test_memory.py::test_remember_tool_project_scope` and
  `::test_remember_tool_global_scope` both call
  `reg.run("remember", {"fact": ..., "scope": "project"|"global"}, ctx)` and
  assert the fact lands in the corresponding file. HEAD's `RememberArgs` has no
  `scope` field at all and unconditionally writes to `GLOBAL_MEMORY` — would
  fail `test_remember_tool_project_scope`. Note MERGE_HEAD's remember.py imports
  `from relaycli import memory` (flat) — this matches `tests/conftest.py`'s
  `_hermetic_global_memory` fixture, which patches `relaycli.memory.GLOBAL_MEMORY`
  (flat), not `relaycli.core.memory`. The flat `relaycli/memory.py` is the
  currently-live memory module; reconciling it with `relaycli/core/memory.py`
  is Phase 2b's job, not Phase 1's — for now this is simply "whichever
  `remember.py` conflict-side survives must agree with what conftest patches,"
  and it does.
- **`relaycli/tools/search.py` — kept MERGE_HEAD**, and this one matters for
  security, not just features. `tests/test_tools.py::test_search_excludes_secret_contents`
  asserts `.env` contents never appear in search results. HEAD's search
  implementation has **no secret-file filtering at all** in its ripgrep path,
  and its one attempt at an ignore-filter is dead code — it computes `filtered
  = [l for l in lines if not any(...)]` and then immediately overwrites it with
  `filtered = lines` on the next line, discarding the filter unconditionally.
  MERGE_HEAD's `_normalize_rg_line` correctly drops any match under
  `proj.is_secret(path)`. Keeping HEAD here would both fail the existing test
  and reintroduce a real secret-leak bug. This is the most consequential
  decision in Phase 1.
- **`tests/test_ux.py` — kept MERGE_HEAD for all 5 conflict blocks**, downstream
  of the `repl.py` decision above: the surviving assertions must match the
  repl.py behavior actually shipped. Includes keeping the test named
  `test_repl_skips_agent_for_slow_local_model` (HEAD's differently-named test,
  `test_repl_warns_for_slow_local_model_and_proceeds`, is dropped — it tested
  behavior that no longer exists on the chosen repl.py, not a real second
  test worth preserving under a new name).

**Kept HEAD, verified against the substantially larger tool surface:**
- `tests/test_tools.py::test_default_registry_has_all_tools` — HEAD's expected
  set (21 tools, including `webfetch`, `websearch`, `question`, `todo_add/update/list`,
  `git`, `apply_patch`, `think`) matches `relaycli/tools/registry.py`'s real
  `_register_defaults()` and is independently exercised by
  `tests/test_new_tools.py`. MERGE_HEAD's expected set (12 tools) is simply
  stale relative to what's actually registered.

**Special-cased files (not a plain HEAD/MERGE_HEAD pick):**
- `relaycli/__init__.py` — combined per the master prompt's explicit instruction:
  `__version__ = "0.5.0"` (HEAD) + the richer module docstring and `__all__`
  (MERGE_HEAD).
- `pyproject.toml` — kept HEAD's added `json-repair==0.35.1` and `pyyaml==6.0.2`.
  Confirmed both are genuinely imported: `yaml` by `relaycli/heuristics.py`;
  `json_repair` by `relaycli/agent/__init__.py` and `relaycli/agent/loop.py`
  (the live layered agent package — not just the soon-to-be-deleted flat
  `agent.py`).
- `relaycli/agent.py` — not resolved, deleted outright (Phase 1 special case).
  Confirmed dead via Python package-over-module shadowing rules (a directory
  `relaycli/agent/` with an `__init__.py` always wins over a sibling
  `agent.py` for `import relaycli.agent`) and confirmed `relaycli/agent/__init__.py`
  re-exports everything the flat file could have provided
  (`Agent`, `AgentResult`, `Relay`, `RelayResult`, `Reporter`, `PlainReporter`,
  `Role`, `resolve_model`, `role_enabled`, `routing_table`,
  `fake_tool_call_text`, `text_tool_calls`, `_compact`). None of its 28
  conflict blocks were read in detail — not worth the effort for dead code.

---
