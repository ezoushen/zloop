# Managed dispatch decisions

A managed run retains its scheduling policy in `autopilot-control.json`: consider
independent ready work, use configured routes, isolate concurrent writers and retain
one integration owner. Resume and compaction preserve that policy and the card's
standing user decisions. This grants no new permission, provider or merge authority.
Follow [managed run controls](control.md) for every card change, including completion:
use supported control/phase commands. Editing the card view does not end the run;
it creates divergence that must be reconciled before continuing or claiming delivery.
Unmanaged cards retain their existing launch protocol. An older managed card without
the policy requires explicit re-adoption using its existing confirmation reference;
re-adoption preserves its pause state and grants.

Contents:
- [Resolve each assignment](#resolve-each-assignment)
- [Check the whole dispatch pass](#check-the-whole-dispatch-pass)
- [Launch and continue](#launch-and-continue)
- [What the check establishes](#what-the-check-establishes)

## Resolve each assignment

Write the complete packet first. It names `assignment`, `attempt`, `dispatch_card`
and `execution`, each with its value on one line, alongside the normal job inputs.
The last two are absolute paths to this run's card and the execution JSON about to
be written. Resolve through the existing router:

```sh
python3 <plugin_root>/scripts/agent_models.py --repo <repo> --host <host> --job <job> --route \
  --dispatch-card <card> --assignment-id <attempt> --assignment <packet> --worktree <worktree>
```

Retain the full output as the packet's `execution` file. Complete any
`selection_required` response internally with the configured selection procedure;
it cannot issue a receipt. Existing pins, eligibility, thinking bounds and one-run
overrides still apply. A configured or unconfigured native default remains valid
when the resolver permits it. A handwritten inherited route cannot replace its
receipt. `--assignment-id` identifies the receipt without requiring a journal;
when recording decision calibration, also pass the existing `--journal`.
The run records its original routing session scope. A fresh host uses that scope
automatically, preserving session-level provider/model choices rather than falling
through to another host's defaults. An explicit conflicting `--session` is refused;
normal one-run choices and edits to the original session configuration remain supported.

The receipt binds the run revision, attempt, packet, checkout, resolved settings and
configuration content. A changed packet, route, configuration or control revision
requires resolution again. Repeating an identical resolution reuses its receipt.

## Check the whole dispatch pass

At dispatch, completion and resume, reconcile native handles, the existing board,
lifecycle plans, account reservations and [shared capacity](../resources/capacity.md).
Assess whether a coarse assignment contains useful independent parts; record the
assessment without manufacturing extra workers for a coherent small task.

Write the pass as JSON, then run:

```sh
python3 <plugin_root>/scripts/dispatch_state.py plan --card <card> \
  --expected-revision <current-control-revision> --input <batch.json>
```

The JSON contains:

| Field | Value |
|---|---|
| `assignments` | Approved unfinished assignments needing admission; active workers are read from the lifecycle |
| `launch` | Attempt IDs to launch now |
| `deferred` | Every other attempt ID mapped to its reason |
| `capacity` | `limit` and `evidence`, absolute nonempty files retaining current status/reservation checks |
| `decomposition` | The assessment of independent parts and relevant user constraints |
| `integration_owner` | One session bound to this card, responsible for shared wiring, integration and builds |

Each assignment has `job`, `assignment`, `attempt`, `depends_on` and `touches`.
Dependencies use stable `job:assignment` keys. Writers name their write areas;
overlapping areas serialize them. Selected assignments also name absolute `packet`,
`execution` and `worktree` paths. The job definition determines write permission.
A packet's `ticket` connects partitions to their local board ticket. Local board
dependencies and write areas add to the pass; an empty list cannot erase them.
Continue using the board's scheduler for eligibility and integration checks.

`capacity.limit` is the reconciled allowance for productive assignments in this
workstream, including its current live assignments, after accounting for other
sessions, retained native agents, account and resource limits. It is between zero
and eight. Evidence must establish that allowance; eight is a ceiling, not a grant.
Existing backend claims still apply. Concurrent writers need distinct Git worktree
roots belonging to their respective repositories. Helpers also count.

Deferrals have one of these forms:

- `{"kind":"dependency","because":["ticket:other"]}` names an actual unfinished prerequisite.
- `{"kind":"conflict","because":["ticket:other"]}` names selected or active work sharing a write area.
- `{"kind":"capacity"}` requires the reconciled allowance to be exhausted.
- `{"kind":"restriction","evidence":["/absolute/user-decision.txt"]}` retains an applicable user restriction.
- `{"kind":"availability","evidence":["/absolute/preflight-failure.txt"]}` retains the actual outage and permitted recovery boundary.

No deferral means an error, rather than implicit one-at-a-time execution. The pass
must include unfinished lifecycle plans, local tickets and unfinished assignments
from the previous pass. A dependency completes when its job result completes; the
board's own acceptance/integration gates remain binding.

## Launch and continue

The native prelaunch hooks check the exact packet and the resolved tool settings,
then claim its attempt once. Pi managed calls launch individual `subagent` packets;
opaque workflow scripts cannot consume receipts for children hidden inside them.
Independent calls can launch back to back without waiting for a sibling to finish.
Selected siblings require background calls: Pi `async` stays enabled, Claude uses
`run_in_background: true`, and T3 delegation avoids `mode: wait`.
Unmanaged workflow scripts are unaffected. T3 startup probes use the exact task from:

```sh
python3 <plugin_root>/scripts/dispatch_state.py probe --card <card> --execution <execution.json>
```

T3 writers also request native worktree isolation. The probe does no productive work;
registration, startup verification and productive submission follow the normal
[worker protocol](prepare.md). Registration and supported submission validate the
receipt and batch again, including for Herdr and T3 transports.
The Bash executor also checks a managed packet before invoking its argv. Its
structured assignment uses the attempt as `assignment_id` and the exact packet as
`prompt`; the caller still owns its asynchronous execution and completion protocol.

Before yielding, register every chosen attempt and arm its normal completion wake.
A selected but unregistered attempt keeps the managed yield hold; repeating the
same stop three times does not clear it. An explicit supported pause still wins.
An active managed run without a terminal or completion wake also keeps holding;
a handwritten terminal cannot clear an active canonical run. Complete or pause it
through the supported controls, retaining a real blocker when work cannot proceed.
After collection, a changed lifecycle plan, board, route, capacity evidence or control
revision invalidates the pass. Reconcile and check the next pass before another
launch; the user need not repeat the standing scheduling instruction.
Already registered, unsubmitted workers count as active in the refreshed pass.
They may continue their existing unchanged admission after re-evaluation, including
after an authorized resume, while its configuration remains unchanged. They do not
consume a second native launch claim or silently switch settings.

Inspect persisted policy, claims, pass and gaps with `dispatch_state.py status --card
<card>`, including while paused. A failed native call may leave a launch claim.
First prove the child did not start, or stop it and reconcile its owned work, then
release only that unregistered claim with `dispatch_state.py release --card <card>
--attempt <attempt> --expected-revision <revision> --evidence <absolute-proof-file>`.
Never release an uncertain live child merely to retry. Registered attempts use the
existing lifecycle recovery path.

## What the check establishes

The check rejects routing bypass, duplicate native calls and unexplained omissions
of known work. Capacity and restriction evidence remain factual responsibilities of
the orchestrator; a file hash preserves the evidence rather than proving its meaning.
Remote tracker inventories must be included by the caller. The validator cannot
invent a decomposition or discover unrecorded work and external account use.
Full-access hand edits are outside this workflow guarantee. Native hooks must be
loaded in the running host; on-disk source identity alone does not prove that.
