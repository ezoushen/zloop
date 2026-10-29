# Pipeline map

Contents:
- [Guides and configuration](#guides-and-configuration)
- [The outer loop](#the-outer-loop)
- [Failure modes and where the loop catches them](#failure-modes-and-where-the-loop-catches-them)
- [Re-entry](#re-entry)
- [Flows](#flows)
- [Our methods underneath](#our-methods-underneath)
- [Left to the human](#left-to-the-human)
- [Delivery composition](#delivery-composition)

The zloop plugin: ten commands, five of them in loop order (align, refine,
dispatch, review, pr) plus [`/zloop:autopilot`](../autopilot/SKILL.md), which runs that loop hands-off after
one interview and a confirmed run card, and remediate, setup, brief and huh, and five skills the user
types or the agent loads when a request matches (diagnose, research, prototype,
explain, tdd); one artefact directory
`.scratch/<slug>/`; one principle: the
principal answers **steering** questions (goal, principles, constraints) and
**context** questions (what the machine cannot know); the agent decides everything
else and records consequential choices as a ledger row — number, decision, hinge, rejected
alternatives — the principal can veto. Align and refine append to one
`ledger.md`.

| Phase | Command | Owns | Writes | Hands on |
|---|---|---|---|---|
| Mandate and alignment | `/zloop:align` | the question filter; the mandate's tree (goal, principles, constraints, context); the ledger row shape; WHAT / WHY / HOW, depth proportional to risk; reuse accepted decisions and ask only material forks | `ledger.md` (first rows), settled terms in `GLOSSARY.md`, standing principles in ADRs/memory, `alignment.md` | slug, ledger, the brief |
| Refinement | `/zloop:refine` | ticket slicing by the board's [slicing rules](../../runtime/board/slicing.md) from the brief or a plan agreed in chat, edges, sizing and their rejected alternatives | configured tracker issues, or local Markdown tickets | dispatch-ready tickets |
| Execution | `/zloop:dispatch` | scheduling on `blocked_by`, capacity and area locks; how each ticket runs (inline or a builder worktree) and integration; builder evidence and merge-conflict jobs; handoff to shared review | `reports/*.md`, `feat/<slug>` (or `main` when the user says so) | implementation status and review report |
| Review | `/zloop:review` | direction → blast radius → implementation, complete layer batches, scoped invalidation, reproduction, optional autofix with applicable E2E, local comment drafts and follow-ups | review and verification report | reviewed commit, gate outcomes and unresolved findings |
| Delivery | `/zloop:pr` (optional) | the description assembled from the artefacts above | `pr.md`, the PR | a URL |

## Guides and configuration

For a change you want to understand before assessing it, use
[/zloop:review walkthrough](../review/walkthrough.md). It teaches and discusses the PR,
then can hand agreed criteria into the same review procedure when requested.
It is optional; direct review starts assessment without it.
For a progress snapshot of the active task or a named workstream, use
[/zloop:brief](SKILL.md); `/zloop:brief map` shows this map. When an
explanation loses you, `/zloop:huh` backs up to what you missed and re-pitches it.

Configuration is optional and separate from the delivery phases. Use
[/zloop:setup](../setup/SKILL.md) to understand the current setup and update it
at user, project or session scope, or name the setting directly;
see [configuration examples](../../runtime/config/executors.md). Existing native behavior
continues without configuration. Setup does not start workers or change review gates.

Optional [workflow decisions](../../runtime/decisions/phases.md) use a shared backend only
at explicitly enabled checkpoints. Worker selection chooses within the allowed pool;
recovery considers the cause before rerouting. The host accepts or overrides each
recommendation, reports its source and surfaces calibration warnings. Existing
phase owners and gates remain in place; there is no second planning/review loop.

Dispatch reuses [existing project execution facts](../../runtime/board/project-loop.md):
commands, areas/resources, verification and environment constraints. Missing
facts go in the workstream's alignment/report; no maintained setup file is required.
Run history stays in reports/journals. Workers call shared plugin scripts directly.

## The outer loop

Loop engineering is the design of the loop that drives agents, so a human does not.
The inner loop is the agent's own (perceive, reason, act, observe — the [TDD standard](../../standards/tdd.md)); the
outer loop is this pipeline's, and each command is one of its nodes:

| Node | What it does here | Command |
|---|---|---|
| Intent | success criteria and constraints: the mandate's limits, the brief, every criterion's `Verify` command | `/zloop:align`, `/zloop:refine` |
| Context | a state payload, not a conversation: ticket, brief, project vocabulary/instructions, absolute paths | `/zloop:dispatch` prompts |
| Action | each ticket inline by the orchestrator or by a fresh builder in its own worktree, chosen per ticket | `/zloop:dispatch` |
| Observation | the report's recorded evidence; board acceptance checks; scoped review with repros; applicable verification | `/zloop:dispatch`, shared review procedure |
| Adjustment | use blocker/failure evidence to correct context, environment, approach or worker; preserve scope and attempt limits | `/zloop:dispatch`, shared review procedure, re-entry below |

Issue state lives in the configured board; execution evidence lives in reports
and the ledger. Read both fresh every iteration. Local front matter applies only
to projects using local Markdown tickets.
Policy is the contracts: scoped source reads when needed, no `main` from a subagent,
area locks and device reservations, runtime capacity, native permissions and
human-authorized merge by the orchestrator.

## Failure modes and where the loop catches them

| Mode | Symptom | Caught by |
|---|---|---|
| Thrashing | an agent edits without converging | one `Verify` command per criterion; dispatch handles non-converging tickets; review keeps per-finding repair limits across E2E failures |
| Overfitting tests | suite green, behaviour wrong | verify the accepted behavior and affected entry points; satisfy the project's applicable compile, suite and E2E gates |
| Context drift | work on stale assumptions | fresh context per delegated ticket, inline only where the session already holds it; canonical board refreshed each iteration; reviewer verifies its range first; a resumed agent is told what survived |
| Unsafe autonomy | over-reach or destruction | subagents never push, merge or touch `main`; native permissions; runtime capacity; human-authorized merge by the orchestrator |
| Comprehension debt | the human stops reading | consequential ledger rows and a concise alignment brief; only material questions; on-demand briefings and walkthroughs; a proportional PR description |

## Re-entry

- A blocked ticket whose cause is a missing **fact** → [research](../research/SKILL.md); a **defect** →
  [diagnosis](../diagnose/SKILL.md); a **contract decision** → resolve against accepted
  priorities and record the deciding source; unresolved conflicts return for human
  alignment while independent work continues, not a retrospective rewrite of criteria; a **wrong
  design** → `/zloop:align` at the affected level, then `/zloop:refine` re-cuts.
- A question no level can settle on paper → a [prototype](../prototype/SKILL.md), which align assigns
  through its prototype job; its verdict returns as a ledger row.
- A veto or a steering answer is recorded where `/zloop:align` settles the mandate, so the
  same question is decided, not asked, next time.

## Flows

Each command's procedure is its `SKILL.md`, with its other docs and jobs in the same
folder; commands read the shared interview and board procedures and the other rules in `runtime/`.

- [Align](../align/SKILL.md): the mandate, then the WHAT / WHY / HOW brief, and the prototype job.
- [Refine](../refine/SKILL.md): ticket slicing and the ticket template.
- [Dispatch](../dispatch/SKILL.md): scheduling, the iteration, the review handoff and the ticket, merge-conflict and acceptance-rerun jobs.
- [Review](../review/SKILL.md): the shared review procedure, its walkthrough and its jobs.
- [PR](../pr/SKILL.md): the description and publication.
- [Remediate](../remediate/SKILL.md): the audit procedure and job.
- [Setup](../setup/SKILL.md): the configuration procedure and its tour.
- [Brief](SKILL.md): progress briefings and this map.
- [Housekeeping](../housekeeping/SKILL.md): residue and documentation currency, run by dispatch and PR.
- [Interview](../../runtime/interview/question-filter.md): authority, [grilling](../../runtime/interview/grilling.md), the question filter and the ledger record, shared by the commands that ask questions.
- [Board](../../runtime/board/board.md): tracker conventions, scope, ticket slicing and the project loop.
- [Diagnose](../diagnose/SKILL.md): a defect's cause and reproducer, for the user, review's reproduce job and blocked tickets.
- [Research](../research/SKILL.md): cited facts from primary sources, for the user, align, review and blocked tickets.
- [Prototype](../prototype/SKILL.md): a verdict from throwaway code, for the user and align's prototype job.
- [TDD](../tdd/SKILL.md): test-first work with the user in place of a ticket.
- [Explain](../explain/SKILL.md): telling in layers and the views, shared by briefings, the walkthrough, the setup tour and `/zloop:huh`, a sibling command this map names without a link.
- [Ask with context](../../runtime/ask/ask.md): the context and the re-pitch every question to the user carries.

## Our methods underneath

Every method the phases use ships in this plugin: test-first work from the
[TDD standard](../../standards/tdd.md), which the [tdd](../tdd/SKILL.md) skill works with the user, module design from the
[design standard](../../standards/design.md), ticket slicing from
[slicing](../../runtime/board/slicing.md), missing facts from
[research](../research/SKILL.md), defects from
[diagnosis](../diagnose/SKILL.md), and a question paper cannot settle
from a [prototype](../prototype/SKILL.md). The review command supplies
scope, priority and allowed mutation context and its own
[Standards/Spec method](../review/method.md). Simplicity is part of that review.
For PR descriptions, the [PR guide](../../standards/pr.md) owns structure,
evidence, refresh and self-check. The PR phase (`/zloop:pr`) owns the description
and publication.

## Left to the human

Authorization for merging `feat/<slug>` into `main`, releases and lockstep repins
belongs to the human; the orchestrator carries out those steps when already
authorized. Retrospective evidence stays in the existing reports or journals.

## Delivery composition

Dispatch's handoff applies housekeeping and runs the [shared review
procedure](../review/SKILL.md); only an explicit implementation-only request
omits review. Existing accepted alignment supplies scope, non-goals, priorities
and test seams. Without those decisions, review records provisional priorities or
asks for material human choices. Follow-ups never expand the current review batch;
dispatch's [overall selection](../../runtime/board/scope.md)
determines whether they enter the next batch under existing authorization.

`review --autofix` owns scoped repairs, local workstream integration and applicable E2E
verification, using the verifier's e2e-gate job and board reservations. Dispatch with
autofix uses that same procedure. Reuse its result; there is no second E2E phase
in dispatch or PR. Standalone read-only review remains a review/comment workflow.

Autofix continues until review, documentation and applicable verification gates
pass, or records concrete blockers. It does not require a separate full-delivery
request to run tests. For a zloop workstream the shared procedure also checks the
board's delivery gate. PR consumes that completed evidence and owns
its description/publication; invoking PR does not start testing or grant repairs.
Implementation-only and read-only requests can therefore leave delivery gates
unsatisfied, which their final reports must state.
