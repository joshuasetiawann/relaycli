---
name: ponytail
description: Write the least code that correctly solves the task (after DietrichGebert/ponytail, MIT)
triggers: refactor, cleanup, simplify, sederhanakan, rapikan, bersihkan
---
Write the least code that correctly solves the task.

- Before writing anything, ask: can existing code, the stdlib, or an already
  installed dependency do this? Reuse beats new code; deletion beats reuse.
- Prefer the one-line fix. If the diff feels big, look again for the smaller
  correct change first.
- No speculative work: no abstractions, options, config, or error handling
  for cases that cannot occur in this codebase today. Keep every guard that
  protects a case that CAN occur.
- No drive-by refactors, renames, or comment rewrites outside the task.
- Match the surrounding file's style exactly. Add no new dependency unless
  the task is impossible without it.
- When done, the best report is short: what changed and why it is enough.
