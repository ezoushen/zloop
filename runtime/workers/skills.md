# Assignment skills

Read when building an assignment: which skills a worker gets, who picks them and
how the choice is recorded.

Contents:
- [Three tiers](#three-tiers)
- [Pick situational skills](#pick-situational-skills)
- [Open candidates](#open-candidates)
- [Record and learn](#record-and-learn)

## Three tiers

1. **Core:** the agent file and the core skills it names; the job file comes with
   it. Their gates apply whenever their condition holds, such as
   the test and comment checks when a change touches tests or comments. Never
   re-decide or drop them per launch, and never send them to the decision model.
2. **Situational:** other skills this assignment needs, picked by the orchestrator
   for each launch from facts the assignment states. List them on one `skills:` line holding a bracketed list, as absolute paths, or host skill names where no stable path
   exists; the facts that selected them stay in the Crew rows the worker reads through `brief`.
3. **On demand:** everything else. The worker finds it through the host's own skill
   listing when a step needs it, and reports what it loaded beyond its agent and job
   files and the listed skills.

Hosts load only skill names and descriptions until a skill is used, so unlisted
skills cost little. What costs is required reading the task doesn't need, and a long
candidate list, which makes the right skill harder to pick. Keep the situational
list short; usually zero to three. A longer list usually means the assignment holds
more than one job; split it.

Pass a situational skill as a path or name the worker reads before the step it
serves. That works on every host: Claude Code's subagent `skills` preload is fixed
per agent definition, and Codex agents can only disable inherited skills. Honor each
skill's invocation policy in the host's catalog: never read a skill's body to get
around an invocation that is denied or reserved for the user.

## Pick situational skills

Decide each skill from the assignment's facts: its criteria, the files or areas it
changes, its checks, and the tools, services or sources it must use.

| Fact | Result |
|---|---|
| The user named the skill for this work | settled: list it |
| Project instructions name the skill for this area, check or tool | settled: list it |
| A required check or tool needs the skill, and the host lists it | settled: list it |
| The skill only relates to the topic; no criterion, check or tool needs it | settled: leave it on demand |
| A fact points to the skill but the assignment does not settle whether that step happens | open candidate |

A project's own trigger rows belong in its instructions, not in this framework.
Ask the user only when a skill would change scope, cost or permissions; that is
their decision, not a skill choice.

## Open candidates

When the `skill-selection` family is [enabled](../decisions/phases.md#phase-checkpoints),
run one [checkpoint](../decisions/checkpoint-command.md) per
open candidate before launch, with assignment ID `<assignment>/<attempt>/skill/<name>`
(add the revision for Git scopes) and two options: `load` and `skip`. Describe
both at the same depth: the fact for, the fact against, and what the worker would
miss. Send only the assignment's relevant
facts and the skill's description, not the skill's body. The helper scores both
option orders. The host accepts or overrides the recommendation; an abstention,
failure or disabled family leaves the choice to the host. Record the host's
accept or override with the [annotation commands](../decisions/annotate.md) before
launch; the outcome is whether the worker used the skill, recorded at collection.
Run the [phase check](../decisions/checkpoint-command.md) at
both stages. The boundary exists only for an open candidate: settled skills and a
launch with no open candidate need no checkpoint, skip or check.

## Record and learn

Record the situational skills in the [launch decision record](../routing/report.md)
with each one's selecting fact and source: rule, user, `decision_id` or host. At
collection, record the skills the worker loaded on demand. At delivery, record them
in the crew profile (`.scratch/crew.md`) with the listed skills workers left unused: a skill
that workers keep loading for the same fact is a missing trigger, and a listed skill workers
report unused is a trigger to narrow; moving one into the project's tracked instructions
needs the user's approval.
