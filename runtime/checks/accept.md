# Plan and accept checks

Name each required check's accepted criterion or project/CI requirement, phase and
owner, preconditions, expected duration and execution/teardown deadline. Give each
check a tier in the assignment:
- **focused:** the tests for the behavior being changed, run while editing;
- **affected:** the suites covering the changed code, run before the worker
  returns and again after each repair; `low` skips them;
- **full gate:** the project's complete required gate, run once on the workstream's
  integrated head before review and E2E (not per ticket) and once after the
  last repair, by the orchestrator or by an E2E run that already includes it;
  `high` and higher run it after every ticket is integrated, merged or committed inline, too.

Workers run the full gate only when the project requires it per change; name that
requirement and its source in the assignment. E2E adds only what CI and the full
gate do not cover; `xhigh` and higher add E2E for every criterion with an observable
path. Acceptance reuses a worker's recorded checks at the same commit
rather than running them again. Repetitions, traces and negative controls follow
the accepted measurement contract or a concrete validity gap, rather than adding
another batch for confidence alone; `max` runs each E2E check and flaky-prone suite three times when
the user asked for it. Each tier rule above names the effort levels that relax it,
and only those ([verification levels](#verification-levels)).

Before execution, reconcile each required check with retained evidence in the
existing report: accepted outcome, command and preconditions, original revision
and artifacts, then **reuse** with an equivalence reason or **execute** with the
specific missing/invalidated proof. A new phase, head hash or cache hit alone does
not invalidate evidence. Changed behavior, inputs, toolchain or environment can;
guarded and unguarded runs are not automatically equivalent. Honor any explicit
requirement for fresh execution. Retest affected checks, preserving other passes.
Before an expensive run or final handoff, refresh the relevant integration base,
dependency/release state and actual built artifact identity. Equal version or ABI
labels alone cannot establish matching content or symbols. Use copied or immutable
pinned build inputs; a writable dependency symlink can change a supposedly pinned run.

For a scenario, name the observable activity that distinguishes exercising the
behavior from merely keeping a process alive, and the conditions that invalidate
the run. Check driver/service readiness and fixture suitability before a long run.
Report expected versus observed activity with retained evidence: successful
interactions, loads, state changes or the project's equivalent signal. Exit zero,
completed loop counts and no crash do not prove the scenario happened. Failed
interactions, an unsuitable fixture or missing activity leave its proof incomplete;
an observed contract failure fails the check. The orchestrator verifies these
signals before accepting a worker's pass, without replaying valid checks.
For script checks, invoke the verified script directly, or use a supported transfer
mechanism; `cat <script> | bash` can exit zero when the script is missing. Shell
pipelines must preserve producer failure. Require actual runner summaries/counts
or scenario readback, not a wrapper's printed success marker. Missing scripts or
setup leave the check incomplete even if the outer command exits zero.
Cached compilation does not imply tests executed. Record whether the relevant
test task ran or reused valid retained results; honor explicit fresh-test criteria
by rerunning that task through its supported controls, not rebuilding everything.

For a regression comparison, prove the candidate method reaches the failure on
the known failing/base build before comparing results. Match the trigger, fixture
and scene state, event sequence and observation windows. Detector calibration or
a generic smoke run does not validate a different scenario driver or input sequence.
If the method cannot reproduce the baseline, report the comparison inconclusive
and recover the method.
When two paths can share the same defect, compare against an independent oracle;
agreement between equally wrong outputs is not correctness evidence. Optimization
claims use comparable metrics and measured variability; an improved submetric does
not prove an unchanged or noisy end-to-end metric improved. Keep the original unmet
criterion visible alongside any human-approved narrowing or follow-up.

## Verification levels

A dispatch verifies at one effort level: the thoroughness of its checks, not the
model's reasoning effort. Every level runs the project's required checks — the full
gate once on the workstream's integrated head before review, and required E2E — and
the level scales only the checks beyond the ones the project requires, never those.

| Level | Beyond the required checks |
| --- | --- |
| low | each ticket's focused tests only |
| medium (default) | + each ticket's affected suites before it returns |
| high | + the full gate after every ticket is integrated, merged or committed inline, and one verifier per ticket re-runs that ticket's acceptance on the integrated head |
| xhigh | + E2E for every acceptance criterion with an observable path, even where CI covers it |
| max (only when the user asks) | + each E2E check and flaky-prone suite run three times |

E2E, required or added by a level, runs in the review's autofix E2E stage, so with no
`--autofix` no level runs E2E and the dispatch report says so.

No level relaxes the engineering standard's floor: every criterion maps to passing
evidence the acceptance cites (command, exit status, identity), and the dispatch report
says what the level left out. A lower level buys fewer extra checks, not less proof.
