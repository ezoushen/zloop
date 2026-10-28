# Phase checkpoints and decision requests

Run a checkpoint through the [configured checkpoint command](checkpoint-command.md).

Contents:
- [Phase checkpoints](#phase-checkpoints)
- [Low-level decision request](#low-level-decision-request)

## Phase checkpoints

Inspect effective `decisions.enabled` using offline status for this repo/session
and retain it until configuration changes. Which families to check, and the flow when
none is enabled, follow [phase checkpoints](phases.md#phase-checkpoints). When any
family is enabled, run the [configured checkpoint command](checkpoint-command.md)
at each reached phase's named boundary and check its expected phase record before
handoff. Include an evidence-backed skip when no consultation is needed. Worker
selection does not cover alignment, review triage or recovery. Recovery chooses an action before any worker reselection.
Surface new calibration warnings
with their cause and recalibration action, deduplicating unchanged warnings.

## Low-level decision request

The [request schema](../../schemas/decision.schema.json) contains `family`, `state`,
`instruction`, an `options` map of labels to descriptions and optional ordered
`images`. The helper adds
`abstain`; callers supply 1–254 options. State/instruction are bounded to 16000
characters each; request text and the response are each limited to 64 KiB.
Images have [separate limits](images.md). Send the narrow relevant
facts, not transcripts, entire repositories or secrets. Retain the request with
the assignment when needed for evidence, using existing project conventions.

```bash
<plugin_root>/.venv/bin/python -B <plugin_root>/scripts/decisions.py decide \
  --repo <canonical-project> --request <absolute-request.json> \
  --journal <absolute-session-decisions.jsonl> --assignment-id <assignment/attempt>
```

Use `--session <root-session-id>` for session overrides; `ZLOOP_SESSION` also
applies. `--user-executors` selects an alternate user configuration for evaluation.
No subprocess worker is created. `recommended` includes the choice and proposer;
`host_required` means disabled, abstained, failed or invalid. Resume reuses the
accepted decision until relevant evidence changes. The caller links assignment,
attempt and evidence identity through the returned `decision_id` in its existing
report. With `--journal`, the helper records the exact composed request, raw response,
validated result, model/configuration fingerprint and request latency. Failed calls
are recorded too. The [journal](journal.md), [annotation](annotate.md) and
[retrospective](retro.md) procedures cover host actions, runtime settings
verification, outcomes, offline retrospectives and bounded prompt replay. Recording adds no inference
and calibration traffic is excluded from the session journal. The helper remains
stateless about assignment execution; it never dispatches or retries work.

Scores and confidence retain the provider's meaning. They do not establish factual
correctness or authorization. Record actual proposer, host acceptance/override,
action and evidence. Attribute the host's explanation to the host. Surface new
`warnings` to the user once per session/cause, then retain them in the report.
