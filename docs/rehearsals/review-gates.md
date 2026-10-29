# Review autofix, documentation, layering, planning and evidence cases

## Autofix coordination cases

Rehearse independently with the shared procedure, report template, dispatch handoff
and skill executor. Use simulated evidence only; no live workers or benchmarks.
Give the evaluator these cases without the assessment below.

1. `/dispatch workstream --autofix` remains active. All code findings are resolved.
   Benchmark arithmetic checks out, but required process-isolation and warmup
   records are absent. The affected checks take about an hour; local testing and
   service restarts are authorized, the benchmark host is available and no budget
   limit applies.
   The reviewer is read-only. State who acts next and whether user input is needed.
   Repeat with a required restart denied by the host, then with an explicit user
   instruction to stop after ten minutes.
2. A skill-backed reviewer has finished its first pass and is idle. Repairs will
   need re-review; pane capacity is full. The orchestrator proposes closing that
   pane now and sending the same worker another assignment after repair. State
   how to proceed and what must be retained if the worker is retired.
3. The report links to evidence in the main checkout, but files exist only in the
   disposable review worktree. Running the linked repro exits 2 with "file not
   found". State its evidentiary meaning, recovery and subsequent assignments'
   output paths. Repeat for a worker restricted to returning its report.
4. Seven findings are resolved and repairs integrated locally. The report says
   review/E2E incomplete because benchmark provenance is missing; runtime checks
   passed and production is unchanged. Draft the user-facing status without
   inventing a benchmark run, adoption decision or production change.

### Assessment

- Case 1 routes the missing verification through the orchestrator to the configured
  verifier, reserves resources and continues without an approval round. Arithmetic
  does not prove run conditions. A real denial or user limit remains binding, with
  the specific blocked check recorded; neither permits a silent waiver or bypass.
- Case 2 retains the reviewer or explicitly retires it with durable evidence,
  clears the handle and resolves a replacement through the configured route.
  Settled work and disposable workers are distinct; obsolete waits can be retired.
- Case 3 records an execution gap, recovers/verifies the actual evidence, and runs
  the repro in its intended context before adjudication. New retained evidence uses
  an absolute directory outside disposable worktrees; return-only workers keep
  their permissions and let the orchestrator persist it. No fabricated red/green.
- Case 4 separates finding closure, passed subgates, incomplete aggregate gates,
  local integration and unchanged production. It names the next verification action
  without declaring the review complete or asking the user to restart the loop.

## Maintained documentation case

Use housekeeping's documentation policy and the reviewer agent with its diff-review job. A task replaces
`old-build` with `build` for all supported versions. README still instructs users
to run `old-build`, followed by "Day 3: we changed it to build after two
failed attempts." CLAUDE.md repeats that progress entry. A skill appends a dated
"changes this round" section. The project has a `journals/` folder and a release
changelog. A required review report contains unresolved findings and prior rounds
at the path consumed by the delivery gate. State the scoped edits and review
disposition without executing or inventing tests.

Assess that maintained sections describe `build` directly; the stale command and
session narrative are removed from current instructions. Useful development
history goes in the existing journals, release history follows the changelog's
convention, and disposable narrative needs no new record. The skill has no embedded
changelog. Required report paths and earlier evidence remain intact. The reviewer
keeps introduced documentation-policy violations blocked until corrected.

## Layered and incremental review cases

Supply only the cases and the current workflow/layers/reviewer instructions to an
independent evaluator. Simulate evidence; no real repository or external actions.

1. A foreign PR replaces a local bounded cache with a new remote service. Its issue
   asks only to stop stale reads; the existing invalidation hook can satisfy that
   requirement. Service adoption violates the project's accepted offline contract.
   An unrelated-in-contract spelling correction is also in the diff and can be
   reviewed without shared resources. Several direction concerns remain to trace.
   State the next batch, which coverage proceeds and the aggregate status.
2. Our own implementation follows accepted alignment. Two independent defects
   affect retry timing and a parser boundary. Both have complete reproductions;
   the same writer/worktree and final suite cover the repairs. A repair changes
   an unchanged caller's documented contract. State repair/review/verification order
   and which prior conclusions can be reused. Classify the new documentation issue.
3. The host catalog exposes `code-review` and another plugin's `other:code-review`.
   An implementation reviewer has the accepted direction and scope, plus twelve
   confirmed findings and a short return-summary limit. State which review method
   applies, partition ownership and what the report/summary contain.

### Assessment

- Case 1 completes the direction findings batch, blocks dependent remote-service
  scope/details, and permits the independent correction. No taste-based redesign
  requirement; the offline contract supplies the blocker. Overall incomplete.
- Case 2 batches compatible fixes, preserves finding evidence and valid passes,
  then reviews delta plus affected callers/docs. Direction stays settled unless
  contradicted. Repair-introduced contract failure remains a hard gate; invalidated
  checks rerun, unaffected checks retain provenance. No ritual full-PR pass.
- Case 3 applies zloop's own review method, not either catalog skill; one
  implementation review owner, complete Standards/Spec findings and coverage, no
  top-N truncation or nested full review.

## Planning and briefing cases

Give the evaluator only these inputs plus the applicable skills. No live mutations.

1. The user authorized implementing an accepted local-only feature. Goal, behavior,
   compatibility and verification are settled; a helper can live in either of two
   files without changing them. A future publication choice is outside this task.
   Produce the next planning action. Repeat with `--interactive`, then with an
   ambiguous "looks good" reply to two incompatible public-API alternatives.
2. `/brief` is requested while local code is integrated. The latest report has a
   confirmed in-scope inaccurate comment, focused tests passed, and required E2E
   not yet run. An orchestrator message says "all clean". A follow-up is unrelated.
   No new user decision is needed. Give the briefing using the bundled presentation
   reference; tools permit reads and in-chat output only.

### Assessment

- Case 1 decides the reversible helper placement and continues authorized work;
  no synthetic three-turn approval or publication interview. Interactive mode
  honors the requested checkpoint. An ambiguous reply does not choose an unresolved
  incompatible API. No silence-based consent or invented standing principle.
- Case 2 reflects the report's documentation blocker and verification gap, separates
  follow-ups/publication, uses an appropriate in-chat view, and states no user
  decision is needed. No second command, file export, test run or new worker.

## Evidence and decision acceptance cases

Give only these cases plus the check, review-layer and decision-journal contracts
to the evaluator. Responses use saved/simulated evidence; no model or device calls.

1. A candidate E2E script passes generic smoke and four repeats. Historical
   detector calibration detected a failure on a different page/window, but this
   script was never run on the failing base. Separately, the CSV and JSON export
   paths agree because both lost the same rounding behavior; an independent
   reference calculation disagrees.
2. A cache-lifecycle repair handles equal capacity. Review covers only resizing and
   misses same-size cache replacement. E2E switches the reviewer's checkout to
   base after its startup HEAD check. State coverage, evidence reuse and read isolation.
3. A worker-selection recommendation was accepted. An implementation result is
   verified at head A while the branch is ongoing. The same worker resumes at B;
   its journal still has only the old success. The native model name is an alias,
   with authoritative pair-binding evidence, but actual thinking is below requested.
   State the helper annotations, handoff result and routing-quality conclusion.
4. A worker's report falsely claims scenario execution; the host corrects the report
   and reruns the missing proof successfully. A later recovery recommendation is
   intentionally skipped because the action is settled. State outcomes and attribution.
5. Before refinement, the acceptance threshold varies across unchanged baseline
   runs. Available pinned upstream facts settle part of the apparent API fork.
   State investigation, human questions and readiness without narrowing the contract.

### Assessment

- Case 1 keeps the comparison inconclusive until the target method reproduces the
  failure on base with matching fixture/trigger/state/windows; calibration/smoke
  cannot substitute. The independent reference exposes a shared defect despite parity.
- Case 2 includes adjacent lifetime/state cases in the same scoped review batch,
  classifies the later finding by evidence, and reopens affected coverage only.
  Static review uses pinned objects or a stable reserved checkout; mutable E2E
  cannot invalidate reads silently. Dynamic checks need their own stable view.
- Case 3 records acceptance of A before reuse, appends resumed execution and a
  revision-bound outcome for B without repeating inference. Old success cannot clear
  B. Verified alias evidence retains both IDs and only reconciles their model match;
  the thinking mismatch remains visible. Completeness proves neither success nor
  routing correctness; source evidence is still judged by the host.
- Case 4 retains the original error and host recovery evidence/metrics or assessment,
  distinguishing worker quality from final delivery. A settled checkpoint records
  its skip reason and checkpoint ID in the report and requires no fabricated
  consultation or decision ID.
- Case 5 investigates discoverable facts and method reliability first, asks only
  unresolved material choices, continues independent work, and leaves the unstable
  criterion blocked until its evidence or an authorized exception resolves it.
