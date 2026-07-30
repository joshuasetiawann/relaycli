# RelayCLI Design Tokens

Single source of truth for all three surfaces (terminal, desktop web, landing
page), per the RelayCLI v2 master prompt §0. Extracted from the Claude Design
project **"Parallel orchestration design spec"**
(`https://claude.ai/design/p/e2ffba42-ff4c-4258-908f-47ca9e0df761`), files
`RelayCLI Terminal UI.dc.html` (design content) and `support.js` (the Claude
Design bundler's own rendering runtime — generated tooling, not design
content; confirmed empty of tokens).

**If code and this document disagree, this document — i.e. the design —
wins**, except where flagged as an open conflict below. When implementing,
re-read the source `.dc.html` for the specific screen in question rather
than relying solely on this extraction for pixel-level layout; this file is
the *token* source of truth, not a full mockup transcript.

---

## 0. Naming: "SLATE INSTRUMENT"

Cold slate/blue-gray base, 7 semantic hues, 2 neutrals. Warmth (amber/orange)
is reserved for `warning`/escalation only, so it stays meaningful. This
**replaces** both current identities in the codebase — terminal's
`ACCENT = "#D97757"` (Anthropic's own clay — ground truth table flags this
explicitly as "shipping a competitor in the competitor's color") and web's
`--accent: #2D5BFF`. Neither survives; both surfaces converge on the tokens
below.

## 1. Palette

Two full themes, dark and light. Every hue clears 4.5:1 contrast against its
own base; the lowest is `waiting` (4.8:1 dark / 5.9:1 light). Color is never
the only signal — every state also carries a glyph, and in agent lanes, a
word.

### Dark terminal — base `#0D1216`

| Token | Hex | 256-color | Meaning |
|---|---|---|---|
| `accent` | `#5FA8DC` | `74` | focus · caret · brand |
| `running` | `#45B3A0` | `79` | agent working |
| `waiting` | `#8E93CC` | `104` | blocked on lease / dependency |
| `success` | `#6FB77F` | `108` | done · pass · `+added` |
| `danger` | `#D4695C` | `167` | failed · reject · `−removed` |
| `warning` | `#D6A05C` | `179` | budget · escalation |
| `muted` | `#64798A` | `66` | frame · metadata · keys |
| `rule` | `#24323B` | `236` | dividers only |
| `text` | `#C3CED6` | `252` | body — 11.4:1 on base |

### Light terminal — base `#EFF2F4`

| Token | Hex | 256-color | Meaning |
|---|---|---|---|
| `accent` | `#1C6C9C` | `25` | focus · caret · brand |
| `running` | `#0F7367` | `29` | agent working |
| `waiting` | `#4E56A6` | `61` | blocked on lease / dependency |
| `success` | `#2C7A4E` | `29` | done · pass · `+added` |
| `danger` | `#A93B2C` | `124` | failed · reject · `−removed` |
| `warning` | `#8A6312` | `136` | budget · escalation |
| `muted` | `#5F7180` | `243` | frame · metadata · keys |
| `rule` | `#C2CDD4` | `252` | dividers only |
| `text` | `#1B262E` | `235` | body — 12.9:1 on base |

### Extra surface tokens (not in the state table above)

| Token | Dark | Light | Where used |
|---|---|---|---|
| `page-bg` | `#080C0F` | — | outer page background, one step below the panel bg |
| `heading` | `#E4EBF0` | `#0B141A` | bright headings / focused-lane primary text |
| `text-secondary` | `#8CA0AE` | `#43555F` | secondary body text, captions, tool targets |
| `rule-dim` | `#1B252C` | — | section-divider rule, dimmer than `rule` |
| `row-focused-bg` | `#131E26` | `#DEE7EC` | background of the focused agent-lane row |
| `caret-idle` | `#3B4A55` | `#9BAAB4` | input caret / cursor block |
| `link` | `#5FA8DC` | — | link text (= accent) |
| `link-hover` | `#8CC6EC` | — | link hover |

**Correction (2026-07-30).** This table previously said these were
dark-only, "no documented light equivalents". That was an extraction
miss, not a fact about the design: the source's own *120 COLUMNS — LIGHT
TERMINAL* screen uses all four of the tokens that appear inside a lane
row, and the light theme cannot draw a focused row without them. The four
with a `—` genuinely have no light instance anywhere in the source and are
**not** invented here — `ui/theme.py`'s `style_for()` falls back to a core
palette token for those, and refuses (raises) rather than guessing for a
missing *background*, where a foreground stand-in would make the row it
fills unreadable.

In light, the focused lane's goal takes `heading` **plus bold**; in dark,
`heading` alone. That asymmetry is the design's, and it is the right call:
`#0B141A` against `#1B262E` is a much smaller step than `#E4EBF0` against
`#C3CED6`, so light needs the extra weight to carry the same emphasis.

### 16-color fallback

`accent`→blue, `running`→cyan, `waiting`→magenta, `success`→green,
`danger`→red, `warning`→yellow, `muted`→brightblack. Hue *order* is
preserved across the downgrade so muscle memory survives it.

### Contextual tints (found in specific mockup states, not core tokens)

A handful of additional hexes appear once or twice each, all clearly
derived shades of the core hues above for a specific micro-state rather
than new semantic categories — e.g. dimmed diff-hunk body text
(`#8FC79C` on added lines, `#B7635A` on removed lines, vs. the full-strength
`success`/`danger` used only on the `+`/`−` gutter marks themselves), an
empty-segment tint for the budget meter, and a couple of row-background
tints for alternating/hover states. Treat the 9-token state table as
canonical; re-derive a tint (mix toward the panel background) rather than
hardcoding one of these one-off hexes if a new one is needed. Full instances
are in the source `.dc.html` if an exact match is ever required.

## 2. Glyph set

Every glyph has an ASCII fallback for terminals without the codepoint.

### Task states

| Glyph | ASCII | State | Meaning |
|---|---|---|---|
| `○` | `.` | pending | in the graph, not yet eligible |
| `◇` | `o` | ready | deps satisfied, awaiting a slot |
| braille spinner (see below) | `*` | running | static `◆` when reduced-motion |
| `⊘` | `!` | blocked | lease or dependency held elsewhere |
| `?` | `?` | awaiting you | permission request open |
| `✓` | `+` | done | task complete, output merged |
| `✗` | `x` | failed | retryable; holds its lease until dropped |
| `╌` | `-` | cancelled | dropped by user or by parent failure |

### Markers & connectors

| Glyph | ASCII | Meaning |
|---|---|---|
| `⠋⠙⠹⠸⠼⠴⠦⠧` (+ `⠇⠏` in the runtime, 10 frames total) | — | spinner, braille U+2801–28FF, all single-width |
| `▤` `▥` | `[L]` `[~]` | lease held / waiting on lease |
| `+` / `−` | `+` / `-` | diff add / remove — gutter only, never the whole line |
| `▲` `▵` | — | escalation / retry — model routed up a tier |
| `$` | `$` | cost — always immediately left of a number |
| `├─` `└─` `│` | — | graph connectors — indentation *is* the dependency |
| `▌` | `\|` | focus rail — column 0 of the focused lane only |
| `❯` | `>` | prompt caret |
| `▮▮▮▯▯` | `[###--]` | budget meter — discrete fifths, never a fake percent |

### Role marks — 5 families, glyph + 3-letter code

The design groups roles into 5 families. **This does not match the
codebase's current 16-role list — see §6, Open Conflict, before using this
table to drive implementation.**

| Family | Glyph | Codes shown in the design |
|---|---|---|
| DISCOVER | `◇` | `orc` orchestrator · `rsc` researcher · `arc` architect |
| BUILD | `▣` | `bnd` backend · `fnd` frontend · `api` api · `dat` data · `rfc` refactor |
| VERIFY | `◈` | `tst` tester · `e2e` e2e · `prf` perf |
| OPERATE | `⊞` | `inf` infra · `rel` release · `obs` observability |
| GOVERN | `⊙` | `rev` reviewer · `sec` security · `doc` docs |

## 3. Typography

- Font: **JetBrains Mono** (`wght@400;700`), `ui-monospace, Menlo, Consolas,
  monospace` fallback stack. One font, no secondary sans in the terminal
  surface (the desktop web console's existing `--sans 'Geist'` is a separate,
  already-correct token — see §7).
- Sizes seen: `34px` page title / `16px` section headers / `13px` body and
  terminal-panel text / `12px` meta, captions, table cells / `11px`
  micro-labels, always with wide letter-spacing.
- Letter-spacing: micro-labels use `0.16em`–`0.22em`; nothing else is tracked.
- Line-height: `1.55` prose body, `1.5` inside terminal panels, `1.6`–`1.75`
  captions/annotations/rationale prose.

## 4. Layout grid

- **Canonical width: 120 columns. Degrades to 80.** Below 80, the app refuses
  to draw lanes at all and prints one line: `relaycli needs 80 columns (have
  64)` — a cramped lane list is explicitly considered worse than none.
- Themes: **dark / light / `NO_COLOR`** — all three are first-class, not a
  dark-mode-plus-token-swap afterthought. `NO_COLOR` replaces every state
  color with a glyph, a bold weight on the focused/needs-you lane, and a
  spelled-out word (`WAIT`, `HELD BY`, `NEEDS YOU`, `PASS`) — no fact present
  in the color version is lost.

### Agent-lane column allocation

| Field | 120-col width | 80-col width | Truncation rule |
|---|--:|--:|---|
| focus rail | 2 | 2 | never |
| state glyph | 2 | 2 | never |
| id + role mark | 10 | 10 | never — fixed 3-char codes |
| goal | 34 | 29 | clip at width−1, no ellipsis |
| tool + target | 30 | 23 | drop directories, keep `basename:line` |
| model | 15 | — | cut 3rd (first field dropped at 80 cols) |
| tokens | 8 | — | cut 2nd |
| cost | 7 | 7 | **never cut** |
| elapsed | 7 | — | cut 1st |
| gutter | 2 | 2 | 1 col each side, no vertical frame line |

Cut order at 80 columns: elapsed → tokens → model. Cost is never cut — "it
is the number you cannot recover by looking harder."

**These widths are inclusive of their own trailing gap.** Each value is
left- or right-justified inside a fixed span, and the space between
columns is whatever the value did not use. Adding a separator *on top of*
them — which the first implementation did, to stop right-justified numbers
colliding — makes the row 121 characters wide on a 120-column terminal, so
every row wraps. The right fix is the other one: clip each column's
content at `width - 1` so the gap is always there. Widths sum to 115, plus
the 2-column gutter = **117**, which is what a rendered row measures.

The gutter is 1 column on each side ("no vertical frame line"), and the
whole frame shares it — the status bar, every rule, the group headers and
the lane rows all start at column 1, which is what keeps them aligned.

### Value formats

| Field | Format | Why |
|---|---|---|
| elapsed | `0m41s`, `1m12s`, `1h02m` | always spells the minutes, so the column is one shape and scans vertically |
| elapsed, never started | `—` | `0s` would claim the task ran and took no time |
| cost | `$0.42` — two decimals | what every screen in the source shows |
| cost, sub-cent but non-zero | `<$0.01` | `$0.00` is reserved for actually free; rounding a real charge down to it would read as free |
| tokens | `999`, `12.3k`, `1.2M` | fits 8 columns through any run |
| id + role | `a1 ▣ bnd` | family glyph groups, three-letter code identifies |
| model, escalated | `llama3.1-8b ▲` | the marker is protected from truncation; the name gives way |

### Grouping above 5 agents

The list sorts into **RUNNING / BLOCKED / NEEDS YOU / SETTLED**, each under
its own header row (`RUNNING 3 ─────`), and SETTLED folds to one summary
line per status (`✓ 1 done · a4 ◈ tst regression suite · 31.0k · $0.11`). A
group of exactly one still names its task — the count alone hides the only
question a folded row gets asked. The bands are exhaustive and mutually
exclusive over `TaskStatus`, so no lane can land in two or fall out of the
list. The lane region is never taller than nine rows at any agent count,
and a lease sub-row counts against that ceiling like any other row.

### Status bar

`▌relaycli ~/src/relay-api  git:feat/lease-queue ±3  mode:auto-edit` on the
left; `4 agents  128.4k tok  $1.87 / 3.00  ▮▮▮▯▯ 62%` right-aligned. At 80
columns it compacts to `▌relay relay-api feat/lease-queue±3` … `4a  $1.87
▮▮▮▯▯`. When both halves cannot fit, the left gives way — first the mode,
then the path down to its basename — because the spend and the agent count
are the two facts you cannot recover by looking elsewhere.

The meter fills a segment only once that whole fifth is spent, so it can
never overstate what is left; any non-zero spend lights the first segment.
It turns amber at 60% and picks up `▲` at 90%. (One source screen draws
24% as two segments rather than one — the mockups are hand-set and
disagree with each other on the rounding. Never-overstate is the rule
that is safe to be wrong in.)

### Vertical row budget — 24-row terminal, 120 cols

| Region | Rows | Behaviour |
|---|--:|---|
| status bar | 1 | pinned |
| lane list | 1–9 | pinned; groups above 5 agents |
| transcript | rest | the only scrolling region |
| permission band | 0 or 5 | pushes transcript up, never scrolls |
| input + key strip | 2 | pinned |
| *(floor)* min transcript | 6 | lane list collapses to a strip first |

The transcript has no left or right frame column, only a 1-column gutter of
spaces — selecting any run of rows yields clean, copy-pasteable text. Only
the permission band and inline diff previews draw a full box, and both are
ephemeral by design.

## 5. Spacing

Observed scale (px, prose/document chrome — not the character-grid terminal
panels above, which are unit-less `ch`/row-based by design): `4, 6, 7, 8, 10,
12, 14, 16, 18, 20, 28, 40, 48, 56, 64, 120`. No formal 4pt/8pt-grid claim is
made in the source; treat this as the observed set rather than inventing a
stricter scale.

## 6. Motion

- **Spinner**: 10 braille frames (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) at **90ms**, one shared clock
  for every lane — spinners tick in unison, not independently.
- **Frame ceiling**: whole-TUI redraw at most **15fps**, dirty-region only.
  Token counters coalesce to 4 updates/sec; cost to 1/sec.
- **Streaming text** appends, never re-lays-out — a long message never
  reflows the rows above it.
- **Never animates**: budget meter, lane order, diff content, anything in
  the permission band. A moving target you must answer is hostile.
- **Lane reordering**: only on state change, only at grouping boundaries;
  never on a token/cost tick.
- **Reduced motion**: `NO_MOTION=1` env var, or a non-TTY, replaces the
  spinner with a static `◆` and drops to 2fps. Piped output prints one line
  per event and zero control codes.

## 7. Key map

**Global** — `tab`/`shift-tab` next/previous lane · `1`–`9` jump to lane ·
`enter` focus mode on selected lane · `esc` back out one level (stop all
agents at top level) · `m` toggle focused/merged transcript · `d` open diff
queue · `^g`/`^b`/`^r` graph/budget/roles · `^k` collapse/expand lane list ·
`?` key overlay.

**Lane** — `s` steer (message this agent only) · `p`/`r` preempt lease
holder / retarget to another file · `l` jump to the agent holding a
contended lease · `R`/`x` retry / drop task · `^c` stop this agent (twice
within 2s: quit).

**Consent** — `y`/`n` accept/reject (a diff hunk, or a permission prompt) ·
`Y`/`A` accept file / accept all files · `a` always allow this tool +
argument shape · `j/k h/l` hunk/file navigation · `e` open in `$EDITOR`.

## 8. Design rationale (from the source appendix — keep for future readers)

Three decisions the design explicitly defends:

1. **The lane list is pinned and bounded.** Costs up to nine rows of
   transcript, but it's what makes "what's happening right now" answerable
   at a glance. *Bounded* matters as much as *pinned* — an unbounded pinned
   region is just a second scroll region.
2. **Lease conflicts are a first-class state, not a log line.** The blocked
   lane names the holder, duration, queue position, an estimate, and three
   resolving keys. A user who can't see *why* an agent is idle assumes the
   tool is broken.
3. **No fake progress, anywhere.** Every progress-shaped element reports a
   measured fact (elapsed, tool count, tokens, cost, tests passed) — never a
   percentage of an unknown denominator. The one bar in the product (the
   budget meter) has a real, user-set denominator.

Three alternatives explicitly rejected — worth re-reading before Part A/D
implementation proposes any of them again:

- **A drawn DAG.** Box-and-arrow graphs eat ~6 rows to show what indentation
  shows in one, and break the moment a task has two parents.
- **Split-pane multi-transcript.** Four live streams at ~30 columns each is
  four unreadable columns; one merged, role-prefixed transcript plus the
  lane list gives the same coverage at legible line length.
- **Color-coding agents (as opposed to color-coding state).** Fails at 4
  agents × 7 semantic colors, in 16-color mode, and under `NO_COLOR`.
  Identity lives in the id + role mark; color stays reserved for state,
  which is what needs to be seen peripherally.

## 9. Role taxonomy — resolved

The design's role-mark table (§2) groups roles into 5 families with 17
three-letter codes, which didn't match `core/roles.py`'s actual 16 roles
(5 design-only codes — `api`, `e2e`, `infra`, `release`, `observability` —
and 4 codebase-only roles — `planner`, `coder`, `devops`, `debugger` — with
no code). **Decided: keep the 16-role roster exactly as-is** (per the master
prompt's own ground-truth table: `core/roles.py` is "already excellent," "do
not rewrite the prompts, add capability metadata only") and adapt the
design's glyphs to fit, rather than expanding the roster to match the
design. This is the canonical mapping — use this table, not §2's, for
implementation:

| Family | Glyph | Codes (all 16 roles) |
|---|---|---|
| DISCOVER | `◇` | `orc` orchestrator · `rsc` researcher · `arc` architect · `pln` planner |
| BUILD | `▣` | `bnd` backend · `fnd` frontend · `dat` database · `rfc` refactorer · `cod` coder |
| VERIFY | `◈` | `tst` tester · `prf` performance · `dbg` debugger |
| OPERATE | `⊞` | `dvo` devops |
| GOVERN | `⊙` | `rev` reviewer · `sec` security · `doc` documenter |

12 codes carried over unchanged from the design (`orc, rsc, arc, bnd, fnd,
dat, rfc, tst, prf, rev, sec, doc`). 4 new 3-letter codes assigned to
existing roles the design didn't cover: `pln` (planner → DISCOVER, since
decomposition is a discovery-stage activity alongside orchestrator/
researcher/architect), `cod` (coder → BUILD, the general-purpose builder
alongside the specialized backend/frontend/database/refactorer roles),
`dbg` (debugger → VERIFY, alongside tester/performance — root-causing a
failure is a verification-stage activity), `dvo` (devops → OPERATE, the
role's sole current member).

`api`, `e2e`, `infra`/`inf`, `release`/`rel`, `observability`/`obs` are
**unassigned** — no role currently uses them. Left as reserved family slots
rather than deleted from the design language: if any of those five roles is
added to the roster later, its glyph and family are already spoken for and
consistent with this table.

## 10. Relationship to the existing desktop web tokens

`relaycli/web_ui.html` already has its own token block (`--accent #2D5BFF`,
`--bg #060608`, `--panel #0B0B0E`, etc. — see the master prompt §7.1). Those
are **superseded** by this document per §7.2 point 1 ("any colour appearing
in the HTML that isn't a token is a bug"). `--mono 'JetBrains Mono'` already
matches; `--sans 'Geist'` has no terminal equivalent and is fine to keep for
web-only chrome (headings, body copy outside the character-grid regions).
The web console's existing dark-only palette should be replaced with §1's
dark table, and a new light theme (currently absent in web) added from §1's
light table — this is explicitly called out as cheap "once tokens exist"
in §7.2 point 4.

## 11. What the terminal actually draws today

The design is one long document; the terminal implements a specific part
of it. This section says which, so a reader can tell a gap from an
oversight.

**Built and matching the source** (`ui/frame.py`, `ui/lanes.py`,
`ui/live.py`, `ui/theme.py`):

- the status bar, both the 120- and 80-column forms, dark / light /
  `NO_COLOR`, with the budget meter;
- the rules above and below the middle of the frame;
- the pinned lane list — every column in §4, the state glyphs, the shared
  90ms braille spinner, the focus rail and focused-row background, the
  per-state detail column, the grouping bands and the folded SETTLED row;
- the `└─ held by a1 for 41s` lease line;
- the transcript, focused and merged (`m`), with the
  `├─ a1 ▣ bnd transcript ───` header;
- the input caret line and the key strip;
- the `?` overlay, built from the key table so it cannot drift.

**Not built, and why** — every one of these is blocked on a capability
that does not exist yet, not on a renderer:

| Screen / element | What it needs first |
|---|---|
| `s` steer | a channel into a running Agent; `Agent.run` is synchronous inside a thread, with no inbox |
| `p` / `r` preempt & retarget, `l` jump to holder | `LeaseManager` has no reassignment and no queue |
| lease queue position + ETA on the lease line | a lease *queue*; there is none, and §8 rules out inventing the numbers |
| permission band (§08), and the `?` awaiting-you lane state with it | the live frame only runs in full-auto, which is the mode that does not prompt. The renderer draws the state and the key strip carries its badge — both are tested — but nothing sets it, because in this mode nothing asks. |
| diff review queue, `y`/`n`/`Y`/`A`/`a` | a staged-edit permission model; edits are applied as they happen, so there is nothing held back to approve |
| plan review screen (§07) | per-task cost estimation before the run |
| `^g` / `^b` / `^r` panels | — |
| `^c` stop one agent | same limit as `x`: a Python thread cannot be killed |

The key strip and the `?` overlay deliberately advertise only the keys
that work. A strip offering `d diffs` that does nothing is worse than a
shorter strip.

**One deliberate departure from the mockups.** The frame is drawn
bottom-pinned, not on the alternate screen. The mockups show a bordered
panel, which the alternate screen would match more closely — but it would
also hide the one prompt full-auto still raises (`read_secret`), leaving
the run waiting on input the user cannot see. The border is worth less
than that.
