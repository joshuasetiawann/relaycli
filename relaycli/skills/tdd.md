---
name: tdd
description: Red-green loop — failing test first, minimal code to pass (after obra/superpowers)
---
Practice strict test-driven development.

- Write the failing test BEFORE the implementation, run it, and confirm it
  fails for the expected reason. A test that passes immediately proves
  nothing.
- Then write the minimal code that makes it pass; run the test again and
  show it passing. Refactor only once green.
- One behavior per test, named for the behavior (test_rejects_empty_input,
  not test_case_2).
- Never edit a test to make broken code pass; fix the code.
- Bug fix = regression test first, reproducing the bug, then the fix.
