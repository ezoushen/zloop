# Hold a design decision early

Escalate when the worker is unsure how to proceed inside the agreement, or when
findings might break it. Possible public API/ABI or observable behavior changes,
platform exceptions, a conflict with an accepted [ADR](../domain/adr.md) and impossible/stale criteria are agreement
risks even before they are confirmed. Stop dependent edits immediately; continue
independent authorized work. Do not wait for a finished implementation or let a
reviewer infer the intended decision from an unfinished diff.

Use `escalate --state ... --attempt ... --input /absolute/escalation.json` through
the worker's supplied progress channel, with this shape:

```json
{
  "risk": "possible-breach", "reason": "Evidence may conflict with the accepted public API",
  "criteria": "/absolute/canonical-ticket.md", "brief": "/absolute/decision-brief.md",
  "phase": "alignment", "evidence": ["native finding and governing requirement"]
}
```

`criteria` must exist; the brief can still be pending so a writing delay does not
delay the hold. The hold is committed before notification, using the attempt's
armed parent channel or an explicit `notify` object in this request. Delivery
failure leaves the hold and its event pending. With no callback, consume the
foreground return through the native progress channel; read-only workers return
the structured blocker and the orchestrator records it immediately. A blocked
return is not permission to continue dependent work.

Before writing any new artifact, prefer the direct form when the worker can invoke
the permitted lifecycle command. It creates no intermediate request/brief file:

```sh
python3 /absolute/plugin/scripts/worker_lifecycle.py escalate \
  --state /absolute/reports/workers.sqlite --attempt ticket-12/implementation-1 \
  --risk possible-breach --criteria /absolute/canonical-ticket.md \
  --brief /absolute/decision-brief.md --reason 'API compatibility is uncertain' \
  --evidence 'native finding and governing requirement'
```

The armed parent channel is inherited. If a native approval blocks the worker's
progress operation, the orchestrator consumes its available finding and records
the hold through its own authorized progress channel. Preserve the worker's actual
permission dialog; this fallback neither approves it nor performs its pending
file/code operation. Finish the brief before a ruling, after the hold is visible.

Use `within-agreement` only when compliance is known and the uncertainty is about
the engineering choice. The selected `alignment` or `recovery` phase uses its
existing decision opt-in; `consultation_enabled` reports the registered snapshot,
not proof that consultation ran. The host runs the current configured checkpoint
with concrete alternatives. If disabled, unavailable or abstaining, investigate
or obtain suitable technical advice and retain the escalation until resolved.
Settled engineering choices need no consultation. Backend advice never grants
authority to change the agreement. A later `possible-breach` finding upgrades an
existing in-scope hold; it cannot be downgraded by another model's opinion.
Carry forward already accepted decisions with their source; implementing that
settled contract requires no new hold or user confirmation.

Complete the brief before adjudication: governing requirement, current evidence,
alternatives, compatibility/user effects, recommendation and the decision owner. A
brief that goes to the user [asks with context](../ask/ask.md).
Use a separate fresh assignment for independent adjudication. Routine reversible
engineering stays with the orchestrator; user-reserved product/API choices need
the user's ruling. Model strength does not transfer that authority.

Record `ruling` only after the brief exists and the actual authority has decided:
include `authority` (`host`, `human` or `user`), `decision`, the held canonical
`criteria` path and an `evidence` array containing the ruling and ticket/ledger
revision. Escalations through `escalate` additionally require an immutable existing
`ruling_source` file and `source_kind`: `user-message` for a human ruling, or
`host-investigation`/`phase-decision` for an in-scope host ruling. A possible breach
requires human authority and a user-message source; a backend response cannot
stand in for it. The orchestrator verifies that the source actually contains that
human's decision. Legacy manual `record hold` defaults to possible breach and
retains its reference-based criteria contract; it does not provide the canonical
file integrity checks of `escalate`.

The helper keeps work and completion gated until resolution, then retains hashes
of the approved criteria and immutable ruling source across retries. Changed or
missing sources fail subsequent gates. Update the governing criteria before resuming;
do not rewrite them later merely to match the delivered implementation.
A host may record the ruling against a closed blocked attempt; closing a worker
does not dispose of its outstanding decision.

The helper checks record structure, not the legitimacy of an asserted approval or
the substance of a criterion. The orchestrator must verify those sources. Keep
backend decision/action/execution/outcome recording under
[routing decisions](../routing/report.md); lifecycle receipts
do not substitute for measured worker-quality or completion-cost evidence.
