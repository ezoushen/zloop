# Decision-model router

`decision-model` describes the router's job; `systemone` names the supported API
protocol. Jev/OpenJev is the model serving it. The router is separate from worker
models, runtimes and executors. Shared backend configuration supports the
[decision loop](../decisions/phases.md) and [calibration](../decisions/calibration.md);
[decision backend](../config/decision-backend.md) holds the configuration example,
`timeout_seconds` and `instruction`. Use the service's registered model ID and full
POST endpoint; a local lane is not a bundled server or a required port.
This client supports unauthenticated
HTTP(S) System One endpoints. Other protocols or authentication can stay behind
an installed routing skill. No chat-completions compatibility is assumed.

After worker preflight, pass a compact text file with `--assignment` and the
filtered `--eligible-candidates`. Include the problem, acceptance checks,
uncertainty, relevant verified outcomes and user priorities. Reuse an existing
assignment summary when suitable; do not send transcripts, full repositories or
secrets. The helper adds the agent, job, caller and eligible model descriptions, IDs,
resolved runtimes and model/effort options, excluding executor instructions and
commands. The summary must fit 16000 characters and the request 64 KiB.
For live decisions, also pass `--journal <absolute-session-decisions.jsonl>` and
`--assignment-id <assignment/attempt>`. The resolver records a configured
`worker-selection` checkpoint and reuses it on unchanged resumes. The returned `decision_model.decision_id`
links the exact request to the host action and verified outcome through the
[decision journal](../decisions/journal.md). Reuse that ID on resume; do not repeat a
service call to recover missing history.

One [System One Choice](https://docs.typesafe.ai/primitives/choice) scores complete
model/effort pairs together, with an abstain option. Only declared bounded
thinking values are enumerated. A string baseline stays fixed for this router;
an absent thinking setting stays inherited. Define an `allowed` set to let the
decision model choose among efforts. Explicit task thinking filters the options
and wins. The service cannot invent candidates, effort labels or routes. At most
254 worker options fit one request; narrow larger pools during preflight.

`--list-candidates`, fixed instances, explicit candidate pins and supplied
`--selection` decisions do not call the service. An assignment summary alone
does not change a skill/profile router. Without `--assignment`, ordinary
resolution remains offline; a sole candidate can use its baseline. Supplying
the summary allows effort selection even for a sole candidate. When filtering
leaves exactly one model/effort option, the resolver sends no request; with a journal
it records a `settled` worker-selection checkpoint and reports `sole-option` as the
selection source, so there is no decision ID to annotate. Persist and
reuse the returned decision until an escalation checkpoint
warrants a replacement; resume alone does not invalidate it.

Successful output includes `decision_model` provenance: returned model, option
mapping, choice and probabilities, plus confidence when provided. The selection
reason records the service's choice; it is not a generated rationale. Confidence
measures score concentration, not calibrated correctness; no universal confidence
threshold is imposed. Preflight and final verification still apply.
`decision_warnings` carries calibration status and detected benchmark regressions;
surface new warnings using the [decision workflow](../decisions/decision.md).
Configured periodic calibration replays are separate diagnostic requests and never
replace the saved baseline or consume worker attempts.

Add `"consult": "after-failure"` to route by rule first; it requires a
`fallback_profile`, which then settles routine routes with no service call and
reports `selection_source: rule-profile`. When the eligible pool spans worker
runtimes and none is targeted, the rule uses the host runtime's entry, or else the
only entry whose eligible candidate routes to that entry's runtime. The service
is consulted only when the profile has no eligible route for that runtime, or when the host retries after
a failed or rejected attempt with `--after-failure "<failure evidence>"`, which the
request states. The default, `"always"`, consults on every `--assignment`.

With an optional `"fallback_profile": "<profile>"` on the decision-model router, an
outage, timeout, abstention or invalid answer first applies that
[sparse profile](../config/routers.md#routers-and-sparse-profiles) deterministically. The result reports
`selection_source: fallback-profile` with the `selection_error`, the decision record
and a `decision_fallback` entry in `decision_warnings`; report that warning so a
backend outage stays visible. Configuration, journal-identity or eligibility errors
never use the fallback. If the profile leaves the choice open, or
none is configured, the following host path applies.

An outage, timeout, abstention or invalid answer returns `selection_required`,
`selection_error` and the host router (`{"type": "host"}`) as `selection_router`. Continue
there with the same exclusions and pins, then submit `--selection`; this bypasses
another service call. Do not loop on the endpoint, consume worker repair attempts
or ask the user to route an ordinary assignment. If no permitted worker fits,
stop under the existing no-suitable-worker rule. Each choice request is sent once;
the HTTP client rejects redirects and limits response size to 64 KiB. Saving or
inspecting configuration never probes the endpoint.
