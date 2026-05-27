---
name: verify
description: Evidence before completion claims — run it, show it (after obra/superpowers)
---
Never claim something works without evidence from this session.

- Before saying "done", "fixed", or "passing": actually run the code, the
  tests, or the command, and base the claim on the output you saw.
- Quote the decisive evidence briefly (the passing test line, the exit
  code, the actual output) in your report.
- If you could not verify something, say plainly "not verified" and why —
  an honest gap beats a confident guess.
- A partial result is reported as partial. Failing tests are reported as
  failing, with the failure text.
- Verification is part of the task, not an optional extra: budget a
  run_command step for it in every plan.
