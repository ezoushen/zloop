# Setup tour

Help the user understand the effective setup and adjust it for their work. With
no request to change settings, stay read-only. A scope flag focuses the discussion;
it does not itself authorize edits. No board, new session or configuration file is
required to have this conversation.

Use the [configuration procedure](SKILL.md) for inspection and any requested changes.
Read only the relevant referenced branches. The tour and a direct change are two ways
through the same command; do not require the user to type a second command.

## Explain the actual setup

Establish the active host, canonical project, root session identity if available,
and applicable configuration sources. Inspect with the existing resolver and
offline decision status. Show configured values and where they came from; label
missing runtime evidence rather than starting workers or probing decision services.
Use existing project instructions for tracker and verification conventions. Those
remain project conventions, not fields in executors.json. Installation and
provider/account settings remain with their own tools; explain any gap without
installing or reconfiguring them merely to complete this tour.

Begin with a compact picture of what is active and why. Explain responsibilities
before field names, using one representative agent from the actual configuration:

- **Available workers:** model definitions bind registered IDs and thinking limits
  to worker runtimes; each agent's pool controls which it may use.
- **How a worker runs:** runtime routes choose a native child, skill bridge or Bash
  executor for the caller. Named executor types reuse skill instructions.
- **Who chooses:** the router uses host judgment through a skill, a sparse profile,
  or a decision backend. A profile applies only its specified agent/runtime fields;
  configured pins and bounds still apply.
- **Which workflow decisions are delegated:** enabled decision families and their
  calibration evidence. Worker selection and recovery are separate opt-ins; showing
  a backend or profile does not activate it.
- **How decisions are checked:** the [decision journal](../../runtime/decisions/journal.md)
  links recommendations, host choices, observed worker settings and outcomes. Explain
  missing evidence and calibration warnings; inspection does not run a replay.

Tailor this map to the request. An unconfigured project can use native defaults;
do not fill every field or recommend every optional feature. Distinguish a possible
route from a selected assignment and requested settings from runtime-confirmed ones.
Tell it in layers, as [explain](../explain/SKILL.md) says, with
a small text picture or before/after view when it helps; no extra command.

## Discuss one useful topic at a time

Go deeper one topic at a time, as [explain](../explain/SKILL.md) says,
rather than over every field. Give the next useful topic its tradeoff, and apply the
[question filter](../../runtime/interview/question-filter.md) to material unresolved choices.
Discover facts from configuration and installed contracts yourself. Reuse stated
preferences; a fully specified change can proceed without a mandatory tour.

Explain scope before saving a change:

| Scope | Applies to | Save location |
| --- | --- | --- |
| User | Personal defaults across projects | `~/.config/zloop/executors.json` |
| Project | Overrides for this canonical project | `<repo>/.zloop/executors.json` |
| Session | This root session and its resumes in this project | Resolver-managed session record |

Show precedence from native defaults through user, project and session to explicit
assignment choices. A one-assignment choice is not another persistent scope. Explain
which objects replace rather than merge only when relevant to the proposed edit.
Project is the default for an unspecified durable change. A request for a temporary
or session-only change uses session scope; do not persist it as a project default.

When something is shadowed, show both the saved value at the requested scope and
what currently wins. Removing an override reveals the lower layer; it is not always
the same as disabling a feature or inheriting native settings. Explain that result
before proposing a reset. Resolve genuine ambiguity without adding an approval
round for an already explicit scoped request.

## Apply and explain the result

For an authorized change, use the shared procedure in this conversation: preserve
unrelated values, validate the candidate and affected references, save at the
requested scope, and inspect the resulting effective settings. Show a focused diff
and the behavioral consequence, including any higher layer that still masks it.
Settings apply to new assignments; existing workers keep their resolved choices.
Calibration runs and live worker probes are separate requests, not setup checks.

Finish an explanation when the user's question is answered; no configuration change
is required. Finish an edit with the saved path/scope, effective result and any
remaining runtime check. If the user wants to begin work afterward, point to the
appropriate phase in the pipeline map (`/zloop:brief map`) and carry existing
authorization forward. A setup tour alone does not launch a workflow.
