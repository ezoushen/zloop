# zloop

One workflow for Claude Code, Codex, Pi and OpenCode: **align → refine → dispatch → review → PR**.
The plugin holds the procedures, agents, jobs and scripts; each workstream's state lives in
the target repository under `.scratch/<slug>/`.

## Commands

Typed as `/zloop:<name>` in Claude Code and Pi, where it completes the flags and
modes the command takes, and `$zloop:<name>` in Codex. Each command's procedure is its `skills/<name>/SKILL.md`.

| Command | Does |
|---|---|
| `align` | Settle the goal and constraints, then a WHAT / WHY / HOW plan |
| `refine` | Split the plan into tickets on the project's tracker or local Markdown |
| `dispatch` | Implement the tickets, inline or in builder worktrees, then review; `--autofix` adds repairs and E2E, and `--effort`, `--dispatch-effort` and `--review-effort` set how thorough the verification and review are |
| `review` | Review a branch or PR; `review walkthrough <target>` explains it first, and `--effort low|medium|high|xhigh|max` sets how thorough it is |
| `pr` | Open the PR once review and verification evidence is current |
| `autopilot` | Settle the goal in one interview, confirm a run card with its stop condition, then refine, dispatch, review and open the PR hands-off; `--take-over` mid-session, `--pause` to take control and `--take-over` to hand it back, `--auto-merge` to merge too |
| `remediate` | Find, prove and repair tautological tests or misleading comments |
| `setup` | Explain or change worker, routing and decision settings |
| `brief` | Report progress; `brief map` shows which command fits |
| `huh` | Back up to what lost you and re-pitch it plainly |
| `diagnose` | Find the cause of a bug or failing check and return a reproducer |
| `research` | Answer a question from primary sources, citing every claim |
| `prototype` | Settle one design question with throwaway code and report the verdict |
| `explain` | Explain code, a change or a design in layers |
| `tdd` | Build or fix test-first, one business rule at a time |

The last five can be typed, and the agent also loads them itself when a request matches
their description. It loads one more skill only by itself, housekeeping, to keep the
repository clean at any time; Claude Code keeps that one out of its command menu, and
Codex and Pi list it.

## Concepts

- **One vocabulary.** [GLOSSARY.md](GLOSSARY.md) defines each concept once, with the
  word to avoid where a drifted name exists, and every command, agent and host uses
  those names.
- **Mandate and ledger.** The user owns the goal, principles, constraints and any
  decision they reserve. Inside that mandate the agent investigates facts and makes
  reversible choices itself, asking only about material forks. Consequential
  decisions go in `.scratch/<slug>/ledger.md` with their hinge, so the user can veto
  them without approving each one. `align` writes the plan to `alignment.md`.
- **Tickets on the project's tracker.** The configured tracker (Linear, GitHub, …) is
  the authority for work items; local Markdown under `.scratch/<slug>/issues/` is the
  fallback. Existing project conventions take precedence.
- **One orchestrator, three agents.** The orchestrator owns scheduling, worktrees,
  integration and board updates. Each assignment goes to one worker, which gets an
  agent file (builder, reviewer or verifier: permissions and principles) and a job file
  (inputs, finish line, return). Workers do not create nested teams.
- **Evidence, not claims.** Reports in `.scratch/<slug>/reports/` hold check output and
  decisions. Review reconciles that evidence; `--autofix` repairs in-scope findings
  and runs applicable E2E. `pr` only publishes once review and E2E evidence is current.
- **Layered review.** Direction (WHY / WHAT / HOW), then scope, then implementation;
  an unresolved upstream decision pauses only the review that depends on it. Comments
  stay local drafts unless `--comments confirm` or `post` is given. Code comments and
  API docs are contracts, so their correctness is a hard gate, and every added test
  is checked for being [tautological](standards/kinds/tautological-tests.md).
- **Workers and routing.** No configuration is needed. `setup` chooses each agent's
  executor (native sub-agent, a skill such as Herdr, or a Bash program), model,
  thinking level, pools and routers; see [configuring workers](docs/configuration.md).
- **Hooks.** SessionStart loads `bootstrap.md` and the host's session part. In Claude,
  PreToolUse checks require each Agent call to name a model and carry its job's inputs
  (`hooks/agent_model.py`), let `gh pr create|edit` publish only a linted body
  (`hooks/pr_body.py`) and keep a read-only subagent's writes in the checkouts to its
  reports and assigned worktree (`hooks/read_only_writes.py`); a Stop check keeps the session alive while a worker could
  finish unseen or a Herdr worker it launched is unregistered (`hooks/yield_gate.py`, fed
  by `hooks/herdr_launch.py`, which logs each `herdr agent start`; Pi and Codex apply the
  same gate; OpenCode's plugin, `hosts/opencode/plugin.js`, sends the hold as a prompt once the turn ends). There is
  no Git command blocklist; native permissions apply.

## Layout

| Path | Owns |
|---|---|
| `skills/<skill>/` | One folder per skill: its procedure in `SKILL.md`, its other docs and its job files (`jobs/`) |
| `bootstrap.md` | Session rules every host reads first, with the triggers table for rules that fire outside their owner |
| `agents/` | The builder, reviewer and verifier agents; Claude loads them natively |
| `standards/` | What good looks like: engineering judgment, test-first development, module design, PR descriptions, code comments, documentation and the remediation kinds |
| `hosts/claude/`, `hosts/codex/`, `hosts/pi/`, `hosts/opencode/` | Each host's session part and worker part; OpenCode covers model settings and the yield gate's plugin |
| `hosts/herdr.md`, `hosts/executors/` | Herdr guidance; Bash, sub-agent and skill executors |
| `runtime/interview/`, `board/`, `workers/`, `checks/`, `routing/`, `resources/`, `decisions/`, `config/`, `ask/`, `domain/` | Shared docs commands and agents read, each small enough for one read: the interview (authority, question filter, ledger record) and board procedures no command owns, then worker, check, routing, resource and decision rules; `config/` holds the configuration format; `ask/` how to ask the user and re-pitch what lost them; `domain/` the glossary and ADR rules |
| `scripts/`, `hooks/`, `schemas/` | Scheduler, resolution, checks and hooks; configuration and decision contracts |
| `benchmarks/` | Synthetic decision calibration examples |

## Architecture

The Markdown is layered so a reader follows links in one direction. Hooks and
`bootstrap.md` sit on top: the session-start hook injects the bootstrap's path, so docs name
it instead of linking it. Below them come the skills, what the user types or the agent loads
(`skills/<command>/`: brief and huh; autopilot; dispatch and remediate; pr; review; housekeeping; align,
refine and setup; diagnose, research, prototype, explain and tdd). Under them sit the internal
layers, `agents/`, `standards/`, `hosts/` and the `runtime/` modules
(interview, board, workers, checks, routing, resources, decisions, config, ask, domain), and
nothing below the skills links one. A doc links only its own module or a lower one, so a
skill may read a lower skill's procedure but never a sibling's or a higher one's, and no two
modules depend on each other. A doc path named in prose or
code counts as a dependency, and `scripts/` and `hooks/` import under the same order.
`.agents/skills/writing-agent-docs/architecture.json` records the order; the doc checker
enforces it on every commit and push, and in Claude Code after every edit. To add a host,
executor, agent, job, kind, standard, runtime module or command, follow
[extend the architecture](.agents/skills/writing-agent-docs/SKILL.md#extend-the-architecture).

## Install and develop

- [Installation](docs/installation.md): requirements and the Claude, Codex, Pi and OpenCode setups.
- [Configuring workers](docs/configuration.md): executors, models, pools, routers and decisions.
- [Development](docs/development.md): the suite, Git hooks, native checks, rehearsals and usage tools.
