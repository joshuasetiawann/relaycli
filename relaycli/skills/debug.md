---
name: debug
description: Root-cause-first debugging — no fixes before understanding (after obra/superpowers)
triggers: bug, bugs, error, errors, crash, traceback, exception, broken, fails, failing, fix, benerin, perbaiki, kenapa, gagal, rusak
---
No fixes without root cause investigation first.

- Read the whole error message and stack trace; they usually contain the
  answer. Note exact files, lines, and codes.
- Reproduce the failure before changing anything. If you cannot reproduce
  it, gather more evidence — do not guess.
- Trace the bad value backward to where it originates; fix at the source,
  not where the symptom appears.
- Form ONE specific hypothesis ("X causes this because Y"), test it with
  the smallest possible change, and only then fix.
- Every fix ships with a regression test that fails without it.
- If three fixes in a row have failed, stop patching: the design is wrong —
  say so instead of trying a fourth.
