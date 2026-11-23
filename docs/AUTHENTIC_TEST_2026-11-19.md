# Authentic tennis generation test — 2026-11-19

Target: `C:\Users\ACER V16\Desktop\relaycli-tennis-authentic-test`.
Brief: BASELINE / AFTER HOURS, a fictional evening tennis club; ivory/ink/acid
editorial design, responsive navigation, session selector, honest demo booking,
four sections and footer, keyboard access and reduced motion.

## Integrity

Only RelayCLI's model-driven tools write the website. No manual website edits,
scaffold output, or externally generated replacement files are used. Each run
sets `local_scaffolds=False`, `relay_enabled=True`, and
`experimental_parallel=False` in memory. Persistent model configuration is
unchanged. Explicit frontend requests skip Planner by existing design.

## Findings

- Ollama is installed and its service responds. No models were installed at
  the initial check. The requested `qwen2.5-coder:14b` pull was started.
- GPU: NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB VRAM.
- Existing settings already support per-role models; no new hybrid router is
  necessary. The active configuration had `local_scaffolds=True`.
- Sonnet 5.5 through OpenRouter was rejected because the provider default
  reserved 65536 output tokens, exceeding available credit. A retry with a
  2048-token output cap reached the model but produced truncated tool calls;
  later calls were rejected for insufficient credit. Premium generation did
  not complete. The displayed $0 estimate is not proof of zero charges.
- `cohere/north-mini-code:free` wrote HTML, then repeatedly returned malformed
  tool JSON with provider finish reason `error`. That run was stopped.
- Final fallback `google/gemma-4-31b-it:free` was rate-limited on its first
  call. The website remains incomplete; only the earlier HTML exists.

## RelayCLI fixes

- Optional `max_output_tokens` / `RELAYCLI_MAX_OUTPUT_TOKENS` passes `max_tokens`
  to the provider. Default remains unset. This is independent of iteration
  limits, context budget, and Ollama's interrupt-only timeout.
- Streaming argument reconstruction now accepts only JSON objects as complete
  tool payloads. On truncation it preserves all fragments instead of selecting
  an unrelated number/string fragment from generated file contents.
- Responses with explicit `length`, `error`, or `content_filter` finish reasons
  now fail before any incomplete tool call can execute.
- Claude Code read-only audit found a first-review handoff bug after quality
  gate revisions and an Arial false positive in page copy. Both were fixed;
  malformed text-tool repair now retains the original reply in conversation.
- The Qwen 14B Ollama pull finished after the initial test attempts.
- A full-suite run exposed three older `question` tool regressions: ambiguous
  interactive requests were always auto-resolved. Interactive prompting was
  restored; `--yes` runs still select defaults without blocking.

## Validation

- Initial focused regression suite: 153 tests passed (frontend quality, Relay,
  LLM, intent, routing), after the output-cap change.
- After the parser fix: 49 LLM/configuration tests passed, including four
  truncated-fragment cases and four output-cap/provider cases.
- `git diff --check` passed. Existing working-tree edits were preserved.
- Claude Code 2.1.286 was invoked for a read-only review. Its confirmed
  findings were checked against the code before changes. The suggested stream
  index edge case remains unverified and was not patched speculatively.
- After the audit fixes, the focused Relay/quality/LLM/agent/question tests
  passed. The full-suite run before the question fix had only the three
  question-tool failures; the question test file passed afterward.

## Local Qwen 14B retest

- The first local run used the existing 16 KB HTML. Reading it whole made the
  second model turn take several minutes without a tool result. The run was
  stopped before any file write.
- A second run exposed an intent bug: "Complete the ... website" went to
  Planner instead of directly to Coder. The planner repeatedly read HTML and
  produced an empty response. Continuation verbs now route as frontend work.
- The third run entered Coder directly. `read_file` returned the HTML in three
  context-sized chunks, and Coder wrote `styles.css` (2348 bytes) and `app.js`
  (1118 bytes) through RelayCLI tools. Ollama reported 39% CPU / 61% GPU at
  context 6144; the CSS generation turn took about four minutes.
- The local Reviewer listed all three files, then incorrectly claimed
  `index.html` was missing and requested revision. That revision was stopped
  before it could overwrite the existing HTML. The review prompt now includes
  a filesystem-verified list of current frontend files.
- A read-only Claude Code review found the generated CSS did not style the
  hero, story, membership, contact, or footer; it also used a conflicting
  indigo palette. The mobile menu had no handler, and membership/contact
  controls were inert. These defects were in the model's generated website,
  which was not manually edited.
- The deterministic quality gate previously accepted the generated CSS/JS.
  It now checks that at least three actual section classes have CSS rules,
  that the mobile toggle has a handler, and that JS is present and linked.
- The frontend brief now explicitly requires styling every major section and
  wiring every visible control, including forms and the mobile menu.
- The Ollama session history budget is now bounded by `ollama_num_ctx` with
  reply space reserved. Local `read_file` calls are chunked and support
  `offset_bytes`. The cap follows the resolved agent model, including hybrid
  role overrides.

Raw local logs are in `docs/test-logs/`. The website remains at the Desktop
target and is incomplete by the premium brief; it is an authentic model output.
After these fixes, the complete `pytest -q` suite finished with exit code 0.

## Claude Code implementation and Codex review

Claude Code was asked to improve RelayCLI only. It added deterministic checks
for dead same-page links, forms without a submit path, and buttons with no
observable JavaScript hook. It did not edit the generated website. Its focused
frontend/scaffold tests and Relay/agent tests passed.

Codex reviewed the change and corrected two HTML cases: only an anchor's
`name` is a legacy fragment target (an input's `name` is not), and a
`method="dialog"` form has native close behavior. The resulting gate reports
five actionable issue groups on the authentic tennis output: unstyled major
sections, inert mobile menu, three missing fragment targets, contact form with
no submit handler, and unhandled membership buttons.

This gate checks static evidence, not rendered design quality or JavaScript
execution. A hook string appearing in `app.js` can still be a false sign of
working behavior; multiple forms may share one detected submit handler. A
stronger model or browser-level validation remains necessary for a premium
frontend verdict.

## NIGHT COURT test (2026-11-19, continued 2026-11-23)

Brief: NIGHT COURT, a fictional evening tennis club; ivory, near-black and
tennis-ball yellow; hero, story, weekly sessions, membership, contact, footer;
working mobile menu with `aria-expanded`, an accessible booking summary,
honest demo feedback, no `alert()` or inline `onclick`. Every run used local
`qwen2.5-coder:14b`, relay on, `local_scaffolds=false`, and parallel off.
No website file was edited by hand.

### Run 1 — `Desktop/relaycli-nightcourt-test` (2026-11-19)

The run stopped mid-step in cycle 2; its log ends at 15:38. The log is
`docs/test-logs/nightcourt-local.log`, and the cycle-1 files are saved in
`docs/test-logs/nightcourt-cycle1/`. Cycle 2 answered the two issues it was
given by pasting a ~290-line `<style>` block and about 30 placeholder meta tags
into `index.html` (`your-google-site-verification-code`, `https://example.com`).

Testing the result in Chromium at 390 px found five defects. The static gate
already reported three: membership buttons crash on a missing
`#membership-feedback`, the false "Your message has been sent!", and no focus
style. It missed two:

- the mobile menu never opens: the toggle flips `aria-expanded`, but the CSS
  shows `#menu[aria-expanded="true"]`, an attribute the menu never gets;
- `site.webmanifest`, `apple-touch-icon.png` and two favicons are linked but
  never written, so every page load 404s.

### Run 2 — `Desktop/relaycli-nightcourt-test-2` (2026-11-23)

Log: `docs/test-logs/nightcourt-local-v2.log`. Capped at four revision cycles.
It ended `review_exhausted` after 5 coder passes and never reached the
Reviewer. The gate's issue count fell from 7 to 5, then 4, 3 and 3.

- About 50 of the 86 minutes were lost to Windows Modern Standby
  (13:37–13:52 and 13:52–14:28). Ollama's token log shows matching gaps.
  Generation ran at ~3.5 tokens/s while the machine was awake, and the
  blocking Ollama path printed nothing during the gaps. A sleep, a slow turn
  and a hang look identical in the terminal.
- Cycle 2 ran `npx http-server src` as a "check". It never exits, so 120 s
  were lost to the command timeout.
- Cycle 3's fresh repair coder never read the draft. It listed the root and
  rewrote all three files from scratch, cutting the page from 27 structural
  elements to 10. The gate counted that as progress, because a skeleton has
  fewer features left to fail.
- Cycles 4 and 5 each made one small edit, then ended on "Next, I will create
  the `styles.css` file…" without calling a tool. The agent loop accepted that
  as a finished turn.
- Final output: 4 bare sections, no nav, no session buttons, a form whose
  submit calls `alert()`, and 17 px of horizontal overflow at 390 px.

### RelayCLI changes from these runs

- The gate's rejection list is now shown in the terminal and web UI
  (`RelayObserver.notice`). Before, a new cycle started with no visible reason.
- New gate checks: a second `<style>` block pasted next to `styles.css`;
  placeholder metadata (`your-…`, `example.com`); local `href`/`src` files that
  do not exist; and a menu opened through its own `[aria-expanded]`, inline or
  in CSS. Together with the 2026-11-19 checks, the gate now reports every
  defect the browser found in run 1.
- The repair prompt names the bundle folder and says to read each file
  before changing it and keep what the issues don't mention. CSS fixes go in
  `styles.css` and JavaScript fixes in `app.js`.
- Rollback: if a revision more than halves the page's structural elements,
  Relay restores the previous cycle's files byte for byte and tells the next
  coder why.
- The agent loop nudges a reply that ends by announcing a next step without
  a tool call. This works even after earlier writes, and is limited to two
  nudges per turn.

The changes are covered by regression tests. The full suite passes (1403
passed, 8 known skips). Run 2 already had the visible notice, the `<style>`
and placeholder checks, and the styles.css/app.js instruction. The missing-file
and menu checks, the read-first wording, the rollback and the announced-step
nudge came after it. A third run is needed to measure their effect. Horizontal overflow, the menu's actual
visibility, and visual quality still need a browser; the gate is static.
