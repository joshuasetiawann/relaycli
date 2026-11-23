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
