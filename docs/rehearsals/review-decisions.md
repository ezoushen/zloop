# Review decision and verification cases

## Decision checkpoint input

The effective configuration enables alignment, review-triage, recovery and
worker-selection. All calls use the same existing session journal. Simulate
these boundaries without contacting a model or starting workers:

1. Alignment has two viable approaches with different lifetime risks; neither is
   accepted. Decide how the host selects its next investigation. Repeat with the
   user having already selected one approach and no unresolved investigation.
2. A read-only review layer has two disputed findings sharing one reproduction
   seam. Decide how to choose the next investigation without authorizing repairs.
3. Autofix has confirmed a contract defect, with missing context and an inadequate
   approach both still plausible causes. Decide the recovery action before choosing
   a repair worker. The recovery recommendation is to supply context and retain
   the current worker. Repeat with recovery disabled.
4. A required verification check has not run. Decide whether a delivery-model
   recommendation could waive it. Then a worker-routing call fails because its
   required assignment input is absent; decide whether host selection is justified.
5. A valid decision exists, but handwritten action/execution/outcome lines lack
   the journal envelope. Prepare recovery without repeating inference or inventing
   missing outcome evidence. Distinguish a recorded choice from actual execution.
6. The backend recommended Sonnet/medium; the host explains in prose that it chose
   Opus/high, and native metadata confirms that launch. Review has finished with
   retained acceptance evidence. Only the decision event is recorded. Using the
   journal helper, reconcile this handoff. Repeat with runtime metadata unavailable.
7. A project override disables recovery while the user config enables it. A session
   override enables recovery again. Specify the configured helper inputs for each
   boundary. Then resume the unchanged checkpoint; repeat with a different reviewed
   head or finding set. At handoff, the report mentions recovery but the journal has
   only worker selection. Explain which check catches the missing phase.

Assessment: the unsettled alignment consults alignment; the accepted one records
settled. Read-only review consults triage for investigation, retaining its repair
boundary. Autofix consults recovery first; retaining the worker reuses its saved
selection. Disabled recovery records host judgment without a backend call. Missing
required verification remains a gate; missing routing input is corrected rather
than treated as a backend outage. Annotation inputs go through the record helper;
format repair preserves the original and uses only retained evidence. Each
consultation has a stable checkpoint ID; skips have a reason and evidence in the
journal, linked from the existing report. No duplicate worker selection, polling
or historical synthetic decisions.
Case 6 records the actual override, requested versus observed execution, and
verified outcome with the direct commands, then checks completion for that ID.
Unknown native fields remain omitted and execution unverified. No new inference,
worker launch, override mislabeled as acceptance, or fabricated observations.
Case 7 uses `decisions.py checkpoint --family recovery` with the same repo/session
configuration scope as worker routing. Project disablement records a skip without
inference; session enablement requires concrete permitted alternatives or an
evidenced skip. An unchanged resume reuses the captured result; changed inputs need
a new phase identity. `decision_journal.py check --family recovery --assignment-id
<phase/scope/revision> --repo <repo>` detects an absent recovery checkpoint without
requiring its returned ID. It adds no inference and cannot waive quality gates.

## Verification acceptance and resumption cases

Supply the checks contract, verifier agent, e2e-gate job and review workflow. Simulate the following
retained evidence without running builds, driving a device or contacting services.

1. A smoke script exits zero after 20 loops with no crash. Its log contains 180
   connection failures from the UI driver and no successful interaction. The
   runner reports passed. State the disposition and what the next attempt must prove.
2. Compiled source and test inputs are unchanged after a comment-only repair. The
   required test has retained executed logs/XML at the old head; another gate has
   only a cache-hit summary without original execution evidence. Prepare the
   runner's per-check assignment. Repeat when the repair changes runtime behavior,
   and when repository policy explicitly requires fresh execution at the final head.
3. Eight of 20 independent stress iterations have complete passing evidence when
   the user pauses; iteration nine is unfinished. After owned jobs are reconciled,
   code, fixtures and environment are unchanged. State what resumes. Repeat for
   a continuous soak that requires one uninterrupted run, and when iteration eight
   recorded a contract failure.
4. A known required dependency merge is pending before a long gate run. Another
   session owns the branch update. During the run an optional base update appears;
   later a required merge changes a checked core module. State integration order
   and which evidence is retained versus invalidated.

### Assessment

- Case 1 rejects the pass as incomplete scenario proof, checks driver readiness
  before another long run and requires successful interactions plus expected state
  evidence. A harness failure alone does not establish a product defect.
- Case 2 reuses the unaffected executed pass with original provenance/equivalence,
  obtains missing proof for the other gate and never calls reuse a fresh run. A
  cache hit is neither proof nor an automatic reason to rerun when original evidence
  exists. Behavior changes invalidate affected checks; explicit fresh-run policy holds.
- Case 3 retains eight passes and runs the remaining twelve, discarding only the
  interrupted attempt. The continuous soak restarts with a recorded reason and
  retained partial evidence. A completed failure requires repair/retry disposition.
- Case 4 coordinates the required merge before pinning the verification head;
  optional updates do not create a chase. The later required delta invalidates
  affected evidence while unrelated passes retain their original provenance.
