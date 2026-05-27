# RelayCLI — Skills, more agents, workspace clarity (design)

Date: 2026-07-03
Status: approved for implementation (user: "ngerjainnya pas di path saat itu…
agent-agentnya boleh diperbanyak… perbagus tampilannya… kasih skill-skill
penting", with reference repos). One commit per task.

## Problems observed

1. The agent already works rooted at the launch cwd (PathSafetyError guards
   traversal), but launching from $HOME made "the project" the entire home
   directory — the agent wandered into Downloads and ballooned the context.
   Nothing tells the user this is a bad idea.
2. The relay has exactly three agents (planner → coder → reviewer); the user
   wants more.
3. No skills: no way to teach the agent durable working styles (TDD, minimal
   diffs, frontend taste) per session.

## Changes

### 1. Workspace clarity

- Welcome banner warns when the workspace root is `$HOME` or `/`: the agent
  can see everything under it; suggest `cd` into a project folder.
- The system prompt gains one line: create files inside the working
  directory with relative paths.

### 2. Skills system

- `relaycli/skills.py`: a Skill = markdown file with a `---` header
  (`name:`, `description:`) + body. Discovery, later source wins on name:
  built-ins (`relaycli/skills/*.md`, shipped) → user (`~/.relaycli/skills/`)
  → project (`.relaycli/skills/`).
- SECURITY: skills are NEVER auto-activated. `/skill <name>` is an explicit
  user action; the listing shows each skill's source (builtin/user/project)
  so a repo-shipped skill is visible as such. This mirrors the dotenv
  blocklist philosophy: a cloned repo must not be able to silently steer
  the agent.
- Active skills are appended to the system prompt under an
  "ACTIVE SKILLS" section; `/skill <name>` toggles, `/skills` lists.
  Relay: the coder role receives the same skill block.
- Built-in skills, distilled compactly (source attribution in each file):
  ponytail (DietrichGebert/ponytail, MIT — least-code discipline),
  tdd, debug, brainstorm, verify (obra/superpowers — red/green loop,
  root-cause-first, intent-before-build, evidence-before-done),
  frontend-taste (joshuasetiawann/taste-skill — anti-generic UI rules).
- Completer: `/skill ` completes skill names (completer gains per-command
  dynamic argument providers).

### 3. More agents: explorer + tester

- `Role` gains `explorer` and `tester`; settings gain `explorer_model`,
  `tester_model`, `relay_explorer: bool = False`, `relay_tester: bool =
  False` (opt-in: every extra role costs a full agent run — expensive on
  free tiers).
- Pipeline when enabled: explorer (read-only) produces a compact context
  brief prepended to the planner request; tester (read + run_command) runs
  the plan's verification step after the coder and its report is appended
  to the reviewer request. Explorer/tester failures are advisory notes,
  never aborts (same philosophy as reviewer failure).
- `/agents` shows the five roles (enabled, model, purpose);
  `/agents <role> on|off` toggles the optional two.

### 4. UI round 2 (folded into the tasks above)

- `/help` gains /skills, /skill, /agents and a Ctrl-R history-search hint.
- Welcome hints mention /skills. Slash menu covers the new commands.

## Out of scope

Auto-triggered skills, project-skill trust prompts, markdown re-rendering of
streamed output, arbitrary user-defined pipeline roles, `--skill` CLI flag.

## Tests

Skills: header parsing, discovery precedence, toggle → system prompt,
completer names, project skills listed-not-active. Agents: routing for new
roles, pipeline order with both enabled (FakeLLM), advisory failure notes,
/agents toggle + display. Workspace: home-root warning shown/absent.
Existing suites keep passing; pty drive at the end.
