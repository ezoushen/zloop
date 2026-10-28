# Decision phases

Contents:
- [Phase checkpoints](#phase-checkpoints)
- [Apply a checkpoint](#apply-a-checkpoint)
- [Recovery](#recovery)

zloop owns the workflow, allowed actions and evidence requirements. An optional
decision backend recommends one of the supplied choices or abstains. The host
accepts or overrides that recommendation and records the source. The phase
procedures continue to own planning, investigation and review; this interface adds no second
planning/review loop. No backend or decision family is enabled by default.

## Phase checkpoints

Read effective configuration once per root session, refreshing after changes.
Only enabled families consult the [decision workflow](decision.md).
The following defines each extension point. When no family is enabled, keep the ordinary host flow without creating
a journal or adding phase checks. With configured decisions, account for reached
boundaries of enabled families through consultations or recorded skips. Disabled
families need no checkpoint or check call: configuration is their record.

| Family | Checkpoint and bounded question | Existing owner retained |
| --- | --- | --- |
| `mandate` | Before grouping unresolved questions: investigate, make a reversible decision, or request material human direction? | User intent, reserved decisions and the question filter |
| `alignment` | After concrete alternatives exist: which alternative or uncertainty deserves investigation? | The design standard, open-ended reasoning and accepted scope |
| `refinement` | After task boundaries exist: which supplied split/group addresses an evidenced gap? | Task identity, dependency validity and acceptance criteria |
| `worker-selection` | After preflight: which permitted model/thinking pair fits this assignment? | Existing router, pins, candidate eligibility and executor |
| `review-triage` | After a review layer: which proposed grouping or next investigation fits the evidence? | Finding proof, all required coverage, priorities and comment policy |
| `recovery` | At a blocker, confirmed review defect or failed required check: which permitted recovery action addresses the cause? | Attempt budget, safe handoff and unchanged acceptance gates |
| `skill-selection` | Before a launch, per open skill candidate: load or skip? | Agent core gates, user-named skills and the assignment's facts |
| `delivery` | After assembling delivery evidence: which unresolved follow-up needs attention? | Verified tests/revisions and publication authorization |

Worker and skill selection score both option orders and average them (`order_balanced`),
so the result doesn't depend on how the host listed the options. It also carries the
agent and job's earlier outcomes from the journal and warns `missing_outcomes`. The worker
lifecycle records outcomes for the attempts it registers; record any other
outcome when the work is accepted or rejected. Phase families whose
host-written options differ greatly in length return `option_imbalance`; describe
each alternative at the same depth. Measure a backend with the
[shadow evidence](shadow-evidence.md) and calibration tools before enabling
more families.

`failure-cause` and `wake-triage` are shadow measurements ([failure cause](shadow-pilots.md#failure-cause-shadow-pilot),
[wake triage](shadow-pilots.md#wake-triage-shadow-pilot)): they never change the host's action.
`test-shape` [routes test review](test-shape.md) and is measured the same way.
None of the three is a phase boundary: they need no checkpoint or skip.

## Apply a checkpoint

At the phase's named boundary, run the [configured checkpoint command](checkpoint-command.md)
before choosing the next action. The host supplies the phase and concrete
alternatives, or evidence that the next step is settled. Code loads the effective
user/project/session configuration, consults an enabled family or records a skip,
and reuses unchanged checkpoints. An enabled unresolved choice requires a request;
it cannot silently skip consultation. Worker selection uses the same code through
the resolver; do not submit a second worker-selection checkpoint.

If the family is disabled, keep the ordinary host flow with no checkpoint call. If
the choice is already settled, or a required check or authorization rule determines
the next step, make no inference call. Record the family and that reason through the
helper and link the result in the existing
ledger/review/dispatch report. For a consulted checkpoint, include its decision ID.
A failed/abstaining backend
records host fallback with its cause; a changed state starts a new checkpoint,
while an ordinary resume reuses the record. Do not manufacture alternatives or
repeat a service call merely to populate every family. Leave out options the
workflow forbids, such as autofixing design-dependent findings or ignoring a user
instruction. Offer only permitted actions.

Before leaving a reached phase, reconcile its entry in the existing report:
family, checkpoint ID, consultation ID or skip reason, actual host action, and
outcome or pending state. Check the expected family and phase identity at the
handoff; this detects omitted checkpoints. Consulted decisions also require host
action and outcome at the applicable stage. A skip creates a checkpoint without
a fabricated decision or backend call. Repair omitted bookkeeping from retained
evidence or report its capture gap; a worker-selection entry does not account for
a separate phase choice.

The family follows the question, not the slash command or the assigned agent or job.
`review-triage` chooses the next investigation/grouping after a review layer;
`recovery` chooses how to address a confirmed defect or failed check before repair;
`worker-selection` then chooses who carries out that action if a worker is needed.
`--autofix` authorizes repairs and verification, not these opt-ins; read-only review
also uses enabled triage. Alignment uses `alignment` without an autofix flag.

Never substitute a recommendation for discovering a fact or executing a required
check. Conditional questions requiring new evidence wait for that evidence. The
helper asks one question per call; independent questions can share prepared state,
but backend parallelism and savings are not assumed. Avoid consulting the service
on each tool call. The host's context preparation and checking cost also count.

## Recovery

At existing escalation checkpoints, reconcile
the worker/jobs and budget before constructing options. No remaining authorized
attempt means follow the existing stop rule, without a recovery inference call.
Include current settings, concrete failure, attempted correction, available context,
remaining attempts and permitted alternatives. Recovery accepts a subset of:

- `retain-worker`: focused correction or an ordinary wait fits the current worker.
- `supply-context`: provide missing evidence or the accepted contract.
- `repair-environment`: restore an authorized missing fixture/dependency/environment.
- `replan`: change the approach or task boundaries within accepted scope.
- `reselect-worker`: enter existing model routing with current failure evidence.

Filter `reselect-worker` out when pins, `fallback:false` or permissions forbid the
needed replacement. Filter actions that require unavailable authorization. Changing
model never creates another attempt. The host evaluates the recommendation and
performs a safe handoff before another writer starts. A service outage or unusable
recommendation falls back to host judgment once; no decision-service retry loop.
