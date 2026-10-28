# zloop

The language for coordinating work from an agreed goal through implementation,
review and delivery. Each concept has one name across commands, agents and hosts.

Contents:
- [Language](#language)
- [Intent and decisions](#intent-and-decisions)
- [People and execution](#people-and-execution)
- [Routing](#routing)
- [Verification and delivery](#verification-and-delivery)

## Language

**Workstream**:
A body of work organized around one goal, containing its alignment, tickets, assignments and evidence.
_Avoid_: effort, effort branch, effort worktree, effort slug, effort spend

**Ticket**:
A bounded deliverable within a workstream, with acceptance criteria and dependencies, recorded on its tracker.
_Avoid_: none

**Assignment**:
A bounded responsibility given to one worker under an agent and job, with inputs and a required return.
_Avoid_: none

**Attempt**:
One execution of an assignment by a worker; a replacement execution is another attempt of the same assignment.
_Avoid_: run

**Project**:
The repository and its governing conventions in which a workstream is carried out.
_Avoid_: none

**Milestone**:
A tracker grouping or delivery boundary used to select a set of tickets within a project.
_Avoid_: none

**Issue**:
A tracker's record of a work item; a ticket may be represented by an issue.
_Avoid_: none

**Slug**:
A stable, filesystem-safe name identifying a workstream and its shared records.
_Avoid_: none

**Board**:
The coordination view of tickets, dependencies and progress for a workstream, backed by its tracker.
_Avoid_: none

**Tracker**:
The project's authoritative store of work items, either an external service or local Markdown tickets.
_Avoid_: none

## Intent and decisions

**Goal**:
The outcome the user wants a workstream or command to achieve.
_Avoid_: none

**Principle**:
A user-owned priority for choosing among ways to achieve the goal.
_Avoid_: none

**Constraint**:
A boundary that work toward the goal must respect under the mandate.
_Avoid_: none

**Scope**:
The agreed boundary of work or change being implemented, reviewed or repaired under the mandate.
_Avoid_: none

**Mandate**:
The user's goal, principles, constraints and reserved decisions that bound the agent's authority.
_Avoid_: none

**Interview**:
The conversation that settles the mandate and material choices needed for alignment.
_Avoid_: none

**Alignment**:
The agreed WHAT, WHY and HOW of a workstream, grounded in its mandate and ledger.
_Avoid_: none

**Decision**:
A choice made within the mandate or reserved for the user, with its reason and supporting evidence.
_Avoid_: none

**Hinge**:
The fact or priority a decision turns on, including what would change that choice.
_Avoid_: none

**Ledger**:
A workstream's record of the mandate, consequential decisions, their hinges and standing directives.
_Avoid_: none

**Veto**:
The user's rejection or revision of an agent decision recorded in the ledger.
_Avoid_: none

**Standing directive**:
A continuing user instruction recorded in the ledger that governs later decisions and work.
_Avoid_: none

**Autopilot**:
The command that runs a workstream's whole loop hands-off: one align interview, a confirmed run card, then refine, dispatch with autofix, review and the PR without further questions.
_Avoid_: none

**Run card**:
The user-confirmed agreement for an autopilot run, including its scope, stop condition and reserved actions.
_Avoid_: none

**Run**:
An autopilot execution governed by a confirmed run card, spanning its phases until a stop condition is reached.
_Avoid_: none

**Stop condition**:
The agreed outcome or boundary at which an autopilot run ends or returns control under its run card.
_Avoid_: none

**Phase**:
A stage of a workstream's workflow, such as alignment, refinement, implementation, review or delivery.
_Avoid_: none

**Checkpoint**:
A decision boundary where current evidence is assessed before a phase, worker or recovery action proceeds.
_Avoid_: none

**Decision family**:
A named class of judgment with a shared input and result contract, used by decision checkpoints.
_Avoid_: none

**Journal**:
The retained record of decision requests, results and outcomes used for inspection and calibration.
_Avoid_: none

**Case**:
A bounded example with an expected decision, used to evaluate or replay a decision family.
_Avoid_: none

**Calibration**:
Evaluation of a decision policy against cases and observed outcomes before relying on its judgments.
_Avoid_: none

**ADR**:
An architecture decision record preserving an accepted structural decision and its reason for later work.
_Avoid_: none

## People and execution

**Agent**:
A role definition supplying permissions and principles shared by its jobs; builder, reviewer and verifier are agents.
_Avoid_: none

**Builder**:
The agent responsible for scoped implementation, tests and documentation under an assigned job.
_Avoid_: none

**Reviewer**:
The agent responsible for assessing a change against its requirements, standards and evidence.
_Avoid_: none

**Verifier**:
The agent responsible for independently exercising assigned checks and reporting observed results.
_Avoid_: none

**Job**:
A contract under an agent defining the inputs, finish line and return for one kind of assignment.
_Avoid_: none

**Worker**:
An executing agent instance responsible for one bounded assignment and its attempts, checks and report.
_Avoid_: none

**Orchestrator**:
The coordinating session responsible for scheduling workers, worktrees, integration and board updates.
_Avoid_: none

**Host**:
The app running the current session and supplying its interaction and tool interface.
_Avoid_: harness

**Runtime**:
The environment in which a worker actually executes, which may differ from the orchestrator's host.
_Avoid_: none

**Session**:
A continuing conversation in a host or worker runtime, carrying its identity and context across turns.
_Avoid_: none

**Skill**:
A loadable procedure with a defined trigger; a user-invocable skill is exposed as a command.
_Avoid_: none

**Command**:
A user-facing invocation of a skill, with arguments selecting its goal, target or operating policy.
_Avoid_: none

**Hook**:
A host event handler that supplies context or enforces a workflow rule at a session or tool boundary.
_Avoid_: none

**Worktree**:
An isolated checkout assigned to a worker or workstream so its changes can be checked and integrated separately.
_Avoid_: none

**Branch**:
The named line of changes for a workstream or ticket that review and integration target.
_Avoid_: none

**Panel**:
The host's progress view of the active workstream, including its tickets or worker assignments.
_Avoid_: none

## Routing

**Model**:
A cataloged reasoning model with task-fit information and bindings to supported worker runtimes.
_Avoid_: none

**Runtime binding**:
A model's registered identity and thinking policy within one worker runtime.
_Avoid_: none

**Executor**:
The mechanism that launches, communicates with and collects a worker through a selected route.
_Avoid_: none

**Executor type**:
A reusable launch mechanism definition referenced by executors, such as a native sub-agent, skill or program.
_Avoid_: none

**Route**:
A reachable launch path from the caller's context to a worker runtime through an executor.
_Avoid_: none

**Candidate**:
A configured worker option considered for an assignment, with its model, runtime binding and resolved route.
_Avoid_: none

**Pool**:
The allowed candidates for an agent or job, from which eligible options are selected for an assignment.
_Avoid_: none

**Router**:
The selection policy that chooses a candidate and optional thinking setting from an eligible pool.
_Avoid_: none

**Profile**:
A named set of candidate and thinking overrides by agent or job and worker runtime, within existing pools and routes.
_Avoid_: none

**Thinking**:
The routing configuration's name for the model reasoning setting, subject to its runtime binding's policy.
_Avoid_: none

**Effort**:
The model's reasoning effort, expressed in the routing configuration as `thinking`.
It controls reasoning intensity for an assignment within the selected runtime binding's policy.
_Avoid_: none

**Provider**:
The service or organization supplying model access and, where applicable, subscription allowance.
_Avoid_: none

**Backend**:
The service supplying a decision judgment or local inference, with availability or capacity tracked separately from routing policy.
_Avoid_: none

**Capacity**:
The available execution slots and resources that constrain which eligible workers can start together.
_Avoid_: none

**Allowance**:
A provider's remaining subscription budget or usage headroom, used to constrain routing and autopilot continuation.
_Avoid_: none

**Configuration scope**:
The user, project or session layer in which worker and decision settings apply.
_Avoid_: none

## Verification and delivery

**Report**:
An assignment's durable return recording its result, decisions, checked inputs and supporting evidence.
_Avoid_: none

**Evidence**:
Retained observations and artifacts tied to the inputs actually checked, supporting a report's claims.
_Avoid_: none

**Check tier**:
The focused, affected or full-gate scope of a check, with an assigned owner and acceptance condition.
_Avoid_: none

**Full gate**:
The project's complete required verification on the final integrated candidate, owned by the assigned integrator or verifier.
_Avoid_: none

**E2E**:
End-to-end verification of observable behavior through applicable real entry points beyond what the full gate covers.
_Avoid_: none

**Review**:
Assessment of direction, scope and implementation against the mandate, accepted requirements and evidence.
_Avoid_: none

**Review layer**:
One of the ordered levels a review assesses: direction (why, what, how), then scope, then implementation; an unresolved decision in a layer pauses only the review that depends on it.
_Avoid_: none

**Walkthrough**:
A read-only explanation of a change that precedes a review, giving the user understanding and review criteria rather than an exhaustive assessment.
_Avoid_: none

**Autofix**:
The review setting that permits scoped local repairs of confirmed findings and applicable E2E verification, and never authorizes publication.
_Avoid_: none

**Review target**:
The branch or PR whose change range and supporting evidence a review assesses.
_Avoid_: none

**Comment policy**:
The review setting that keeps comments as local drafts, requires confirmation or authorizes posting them.
_Avoid_: none

**Base**:
The reference defining the beginning of a change range for review or remediation.
_Avoid_: none

**Head**:
The candidate revision defining the end of a change range, or the working tree when explicitly selected.
_Avoid_: none

**Finding**:
A supported defect or standards violation reported against a specific part of the reviewed change.
_Avoid_: none

**Repair**:
A bounded correction of confirmed findings, checked against their original failure scenarios and required gates.
_Avoid_: none

**Kind**:
A named category of defect with a standard for recognizing, proving and repairing it during review or remediation.
_Avoid_: none

**Standard**:
A maintained engineering rule defining acceptable work for the agents and jobs to which it applies.
_Avoid_: none

**Focus**:
The part or question of a task that a command's answer should prioritize within its accepted scope.
_Avoid_: none

**Depth**:
The requested amount of explanation and supporting detail in an explanation command's answer.
_Avoid_: none

**PR**:
A pull request presenting a workstream's changes for review and integration after required evidence is current.
_Avoid_: none
