# RelayCLI — Multi-Agent Relay + Smart Model Router (design)

Date: 2026-07-02
Status: approved for implementation ("lanjutkan" — decisions below made per
recommendation; user can override any of them before or during implementation)

## Goal

The layer that makes RelayCLI *RelayCLI*: an opt-in **relay pipeline**
(Planner → Coder → Reviewer with a bounded reflection/retry loop) and a
**model router** that lets each role run on a different model (cheap model for
planning/review, strong model for coding). Built strictly on top of the stable
single-agent core — the existing `Agent`, tool registry, permission system,
and `llm.py` gateway stay the single sources of truth.

## Decisions (made autonomously, flagged for review)

1. **Scope**: relay + router built together in this layer. The router is what
   makes the relay economical; separately neither is useful.
2. **Invocation**: opt-in. Single-agent remains the default for every request.
   Relay activates via `--relay` at launch, `/relay on|off` in the REPL, or
   `relay_enabled = true` in config. No change to existing behavior when off.
3. **Router**: explicit config-driven role→model mapping
   (`planner_model` / `coder_model` / `reviewer_model`, each falling back to
   `model`). No automatic complexity heuristics in this layer — `router.py` is
   the seam where they would later slot in.
4. **Review loop**: the Reviewer ends its assessment with `VERDICT: approve`
   or `VERDICT: revise`. On revise, its feedback goes back to the Coder for
   another pass, bounded by `max_review_cycles` (default 2).

## Architecture

```
repl.py / cli.py ──┬── relay off ──► Agent.run()            (unchanged)
                   └── relay on  ──► Relay.run()
                                        │
                                        ▼
                        router.resolve_model(role, settings)
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
   Planner Agent                   Coder Agent                    Reviewer Agent
   read_file, search               full tool registry             read_file, search,
   (read-only)                     (edits + commands,             run_command
   cheap model                     honors permission mode)        (can run tests)
        │                               ▲       │                       │
        └── plan (text) ────────────────┘       └── report ─────────────┤
                                                ▲                       │
                                                └── revise feedback ────┘
                                                    (≤ max_review_cycles)
```

Each role is an ordinary `Agent` instance with its own `Session`, its own
system-prompt template, its own tool subset, and a routed model. Handoffs are
explicit text artifacts (plan, coder report, reviewer feedback) passed as user
messages — no shared hidden state. The Coder and Reviewer keep their sessions
across revision cycles (so they remember prior context); each new user request
starts a fresh pipeline.

## Components

### 1. `router.py` (new, small)

- `Role` — `StrEnum`: `planner`, `coder`, `reviewer`.
- `resolve_model(settings, role) -> str` — role-specific model or fallback to
  `settings.model`.
- `routing_table(settings) -> dict[Role, str]` — for the banner / `config`
  command display.

This module is deliberately trivial: it is the future home of smarter routing
(task-complexity heuristics, cost caps) without touching callers.

### 2. `config.py` — new `Settings` fields

| field | default | notes |
|---|---|---|
| `relay_enabled` | `False` | run requests through the relay pipeline |
| `planner_model` | `None` | falls back to `model` |
| `coder_model` | `None` | falls back to `model` |
| `reviewer_model` | `None` | falls back to `model` |
| `max_review_cycles` | `2` | `ge=0`; 0 = review is advisory only (no retry) |

Env names follow the existing `RELAYCLI_` prefix (e.g.
`RELAYCLI_PLANNER_MODEL`). None of these joins `_DOTENV_BLOCKED_FIELDS`: they
carry the same risk class as the already-loadable `model` field (a model id
resolves to a known provider; credentials and endpoints still come only from
trusted config), and `relay_enabled` cannot escalate permissions — every role
still goes through the same `PermissionManager`.

### 3. `agent.py` — two minimal, backward-compatible extensions

- `Agent(..., prompt_template: str | None = None)` — role prompts reuse the
  existing placeholder set (`{cwd}`, `{mode}`, `{mode_desc}`, `{tool_list}`);
  default stays `_SYSTEM_TEMPLATE`.
- `Agent(..., model: str | None = None)` — a per-agent model override held as
  `self._model_override`; a `model` property returns the override or (live)
  `settings.model`, so `/model` keeps working for the single agent while relay
  roles pin their routed model. `run()` and `refresh_system_prompt()` switch
  from `settings.model` to this property.

No changes to the loop logic, permissions, or tool execution.

### 4. `tools/__init__.py` — registry subsets

- `planner_registry()` → `read_file`, `search` (cannot edit or run anything —
  enforced by construction, not by prompt).
- `reviewer_registry()` → `read_file`, `search`, `run_command` (can run tests;
  `run_command` still honors the active permission mode).
- `default_registry()` unchanged (Coder uses it).

### 5. `relay.py` (new) — the orchestrator

Data types:

- `RoleRun` — `role`, `model`, `result: AgentResult` (one per role invocation,
  in order).
- `RelayResult` — `final_text` (the Coder's last report), `stopped_reason`
  (`done` | `error` | `max_iterations` | `review_exhausted`), `cycles` (revision
  cycles used), `role_runs`, aggregate `usage`, `elapsed`.

`Relay` construction mirrors `Agent` (settings / console / project /
permissions injectable; one shared `LLM` instance — it is stateless per call).

`Relay.run(request, *, observer)` flow:

1. **Plan.** Planner agent runs `request` under the planner template.
   Not `done` (LLM error / iteration cap) or empty plan → abort with that
   reason; nothing has been modified yet.
2. **Code.** Coder agent gets the request + plan (first cycle) or the
   reviewer's feedback (later cycles, same session). LLM error or iteration
   cap → abort with the Coder's partial report as `final_text`.
3. **Review.** Reviewer agent (same session across cycles) inspects the work —
   it reads files itself and may run tests — then must end with a `VERDICT:`
   line. Parsing: last case-insensitive `VERDICT: (approve|revise)` match in
   its final text wins; **no match → treat as approve** (bias to terminating;
   the note is surfaced in the summary). Reviewer LLM error → return the
   Coder's result as `done` with the failed review recorded in `role_runs`
   (review is advisory; the work already exists).
4. **Reflect.** `revise` with cycles remaining → back to step 2 with the
   feedback. `revise` with none remaining → `review_exhausted`, with the
   feedback shown so the user can act on it.

The `observer` is a small presentation protocol (`role_start(role, model)`,
`reporter_for(role) -> Reporter`) so relay.py contains zero Rich code, same as
`Agent`/`Reporter` today.

### 6. Role prompt templates (in `relay.py`)

All three keep the existing SECURITY block (untrusted file/command content,
fixed permissions) verbatim.

- **Planner**: senior engineer; explore with read-only tools; output a short
  numbered plan (goal, steps with file paths, verification step); no code
  edits, no filler.
- **Coder**: the existing RelayCLI working rules + "you are the Coder in a
  relay; follow the plan; if the plan is wrong, deviate minimally and say why;
  end with a brief report of what changed."
- **Reviewer**: verify the changes against the request and the plan; read the
  changed files; run the test suite via `run_command` when one exists; end
  with `VERDICT: approve` or `VERDICT: revise` plus numbered, actionable
  feedback.

### 7. Entry points & rendering

- `cli.py`: `--relay/--no-relay` launch flag (default: `settings.relay_enabled`);
  applies to both the REPL and `-p` one-shot. `relaycli config` gains a
  routing table (role → resolved model) and the relay flag.
- `repl.py`: `/relay [on|off]` (bare `/relay` prints status), banner shows
  `relay on` + routing when active, `_run_agent` dispatches to `Relay` when
  enabled. `/clear` resets the pipeline. `/help` updated.
- `render.py`: `RelayRichObserver` implementing the observer protocol — a role
  banner line (`◆ planner · gpt-4o-mini`), the existing `RichReporter` per
  role, and `render_relay_summary` (per-role line: steps / tokens / cost;
  verdict + cycles; aggregate totals).

## Error handling summary

| failure | behavior |
|---|---|
| Planner LLM error / cap / empty plan | abort before any modification |
| Coder LLM error / iteration cap | abort; partial report + reason shown |
| Reviewer LLM error | Coder's result stands (`done`); failure visible in summary |
| Malformed verdict | approve + note (never loops on unparseable output) |
| Ctrl-C mid-role | propagates exactly like the single agent (tool-result stubbing already handled in `Agent`) |
| Review never satisfied | `review_exhausted` after `max_review_cycles`, feedback shown |

## Testing

`tests/test_relay.py`, using the existing scripted/mock-LLM pattern from
`test_agent_loop.py` (no real API calls):

- Happy path: plan → code → review approve; role order, routed models, session
  isolation, aggregate usage all asserted.
- Reflection: revise → coder revision (same coder session) → approve; `cycles`
  counted.
- Exhaustion: persistent revise → `review_exhausted` at the bound.
- Malformed verdict → approve + note.
- Planner failure aborts before the coder runs; coder failure aborts before
  review; reviewer failure still returns `done`.
- Registry subsets: planner schemas contain no write/run tools; reviewer has
  no write tools (enforcement by construction).
- `router.resolve_model` fallback chain; `routing_table`.
- Config: new fields' defaults + `RELAYCLI_*` env loading.
- CLI/REPL: `--relay` one-shot path and `/relay` toggle (mocked pipeline).

Existing suites must stay green untouched (the single-agent path is
unchanged).

## Docs

README: a "Relay pipeline" section (what it is, when to use it, config
example). `.env.example`: the four new variables with comments.

## Out of scope (future layers)

Automatic/heuristic routing, conversation continuity across relay requests,
parallel roles, more than three roles, per-role iteration caps.
