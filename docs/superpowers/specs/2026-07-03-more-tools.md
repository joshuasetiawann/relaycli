# RelayCLI — More tools: navigation + background processes (design)

Date: 2026-07-03
Status: approved for implementation (user: "run_background boleh, kasih
banyak tool lain biar kaya CLI pada umumnya"). One commit per task.

## Problems observed (live sessions today)

1. No directory tool: the model shells out `ls -la` repeatedly (permission
   noise in suggest mode) and even tried `read_file .` (refused).
2. No file-finding tool: the model used `find … -name "*.tsx"` via shell.
3. Long-running commands are impossible: `npm run dev` hit run_command's
   timeout and was killed — the model could never start a dev server.

## Changes

### 1. Navigation tools (read-only, ungated)

- `list_dir(path=".")`: one directory level, dirs first with trailing `/`,
  file sizes, sorted; entry cap with an "…and N more" note. Confined by
  ProjectContext.resolve like every path.
- `find_files(pattern)`: `rglob` glob patterns (`**/*.tsx`), relative paths
  sorted; skips `.git`, `node_modules`, `.venv`, `__pycache__`, `.next`,
  `dist`, `build`; result cap with a note.
- `read_file` on a directory now points at `list_dir` in its error.
- Registries: planner (and explorer) + reviewer (and tester) get both;
  default gets both.

### 2. Background processes

- `run_background(command)`: start a long-running command (dev server,
  watcher) detached in its own session, output (stdout+stderr merged) to a
  0600 log file under the system temp dir. Returns an id (`bg1`, …), pid,
  and log path. Same "command" permission gate + scrubbed env as
  run_command. Processes intentionally survive the RelayCLI session (a dev
  server should outlive the chat); the activity line shows pid + log so the
  user stays in control.
- `check_process(id, tail=40)`: ungated; running/exited(+code) + the last
  N log lines.
- `stop_process(id)`: kills the process group; "command"-gated.
- In-session registry (module-level dict); ids from another session are
  reported as unknown with the log-path hint.
- System prompts (single-agent + relay coder) gain one bullet: use
  run_background for servers/watchers — run_command kills at its timeout.
- Registries: default gets all three; reviewer/tester get check_process.

## Out of scope

HTTP fetch tool, file delete/move tools (run_command covers them, gated),
cross-session process adoption, auto-cleanup of background processes.

## Tests

list_dir: listing shape, cap, confinement refusal. find_files: pattern hit,
skip dirs, cap. run_background: starts + writes log + gate declines;
check_process: running then exited states, unknown id; stop_process: kills
a sleeper, gate declines. Registry membership per role. Existing suites
keep passing.
