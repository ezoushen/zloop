# Before a worker launch

Read with the [launch doc](launch.md) before each worker launch, including helper
workers.

## Deployment

Memory and copied alignment are discovery hints for runner capabilities; verify
their claims against the current installed runner contract before distributing
assignments. If deployment is uncertain or changed, record the resolved plugin
root/version and relevant agent/job/skill content hashes once, and verify workers use
that source. A fresh development checkout is not proof that another host loaded it.
No script is copied to a
project. `plugin_root`, `repo`, `adapter`, `agent_file` and `job_file` in worker messages are
absolute paths, not assumed shell variables.

## Executors, routing and escalation

Before each direct worker assignment, the orchestrator resolves
[optional executors](launch.md). Unconfigured native dispatch retains its existing
preferences. Configured execution applies the resolved settings once to one worker.
Pools and routers are set in [launch](launch.md); selection follows [selection](../routing/choose.md).
`selection_required` is an internal request for a decision, not executable settings.
Use the named `selection_router` and submit the result before dispatch.
Announce the [routing decision and attribution](../routing/report.md).
Worker blockers and review/check failures invoke [escalation checkpoints](escalation.md)
before the next attempt; ordinary resumes reuse the saved choice.

## Helper workers

Include helper workers, such as a research helper, in the same capacity and
assignment log. Where a configured agent applies, resolve it before launch;
otherwise record the helper's native selection and its provenance.
Retain the assignment's stopping condition and observed model/effort; label missing
execution evidence instead of treating a hard-coded model as a routed choice.

## Profiles, Herdr and thinking

The routing profile is selected as [launch](launch.md) says.
Profile lookup is by agent and worker runtime, not caller. Consume selection options
before forwarding phase arguments to the scheduler or review target helper.
When `HERDR_ENV=1`, also read Herdr environment guidance before
cross-runtime dispatch, even when no startup hook supplied it. Carry root-session
identity and canonical repo to assignments; retain their resolved settings on resume.
Workers own their bounded task; further scoping and delegation belong to the
orchestrator under [job lifetime](../resources/lifetime.md).
Agent-chosen thinking changes require the selected agent's allowed set or legacy
opt-in and the
[automatic adjustment path](thinking.md); explicit
user override flags are not a substitute for that permission.
