---
name: readiness
agent: verifier
inputs: ["report", "checks"]
returns: ["## Readiness"]
independent_of: []
acceptance: false
---

# readiness

Probe the preconditions of the E2E checks a dispatch plans, before any diff exists. Dispatch
assigns it once per workstream, in the step that launches the first builders, when it plans E2E
(with `--autofix`).

## Inputs

The assignment names the `report`, a file of its own that the orchestrator creates before the
launch, and the planned E2E `checks`: each check's command, the worker routes the E2E stage
will launch, the models or local backends those routes use, the hooks that need trust and the
devices. No diff or head is involved; run from the `repo`.

## Finish line

Probe each precondition the checks need, with a command that fails when it does not hold:
each tool and version the checks call, each worker route (a short probe that it starts and
answers), each model or local backend a route uses, hook trust where a check needs it, and
each device. Probe only what the checks need. Fix nothing and run no E2E check itself; a
precondition that fails stays failed, and the probes of the others continue.

## Return

Write each precondition under `## Readiness` in `report`: its command, exit status, output
and verdict. For each that fails, add the action that clears it and who can take it: the
orchestrator or the user. Return control with the verdicts; the orchestrator clears what it
can and sends the user the rest in one request.
