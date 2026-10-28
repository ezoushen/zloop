# Choose a worker model

Contents:
- [Shadow selection](#shadow-selection)
- [Allowance](#allowance)
- [Local capacity](#local-capacity)

Read this when a resolver result includes `candidate_pool` or when choosing thinking
within configured bounds. Session overrides are under [session scope](../config/session.md). Fixed configurations
retain their existing behavior. The orchestrator chooses; the resolver validates;
the selected executor applies settings and confirms what actually ran.

1. Inspect with `--list-candidates` and filter by task fit, actual tools, context,
   permissions and availability. `candidate_routes` shows worker runtimes and
   caller routes; it does not prove live support. Preserve a fixed instance.
2. Resolve with the filtered `--eligible-candidates` and, when preflight restricts
   efforts, `--eligible-thinking '{"opus":["medium"]}'`. With the decision-model
   router, each configured candidate left out needs a reason in
   `--excluded-candidates '{"sol":"<task fit, tools, context, permission or availability fact>"}'`.
   Convenience or habit is not a reason, and neither is "no registered profile" when
   the inherited-effort path in the host adapter can launch the pair. A missing
   reason fails the call. Given alone, `--excluded-candidates` leaves every other
   configured candidate eligible; naming a candidate in both lists fails. The
   reasons travel with the decision request and its journal record. This optional map narrows
   configured thinking choices for the current assignment. Omitted candidates keep
   their configured choices, `[]` excludes a candidate and `[null]` permits only
   inherited thinking. It does not probe the runtime or persist capability state.
   The decision service sees only remaining pairs; final settings from profiles,
   host recommendations and explicit pins are checked too. An unavailable pinned
   or profile setting fails before launch. Apply the existing recovery policy to
   exclude that unavailable choice and select an eligible alternative; explicit
   pins and `fallback:false` still block substitution.
   When a runtime inherits a **verified concrete level**, expose that level if
   configuration permits it. `[null]` cannot relax an explicit thinking policy.
   Verify profile discovery in the running host; file creation or a fresh host's
   inventory does not establish the running host's capability.
   A pool without a choice
   returns `status: selection_required`, eligible options and `selection_router`,
   without executable settings. For the host router (`{"type": "host"}`, the default
   and every fallback), choose by [model routing](model-routing.md) in the
   orchestrator and submit `--selection`. For a skill router, load that skill in the
   orchestrator, choose a model and thinking setting, and submit `--selection`.
   For a decision-model router, follow [its protocol](decision-model.md).
   This is normal internal continuation, not a human approval checkpoint.
3. For `router.type: profile`, establish the target runtime from the proposed
   candidate or `--target-runtime`. Apply the specified fields only. A single
   eligible candidate can resolve directly; no list position becomes a default.
4. Preflight the final settings, save the resolved JSON with the assignment, and
   [report the decision](report.md) before passing it once to the
   selected executor. A `selection_required` result cannot launch a worker or
   render a native profile. Reuse the snapshot on ordinary resume; new failure
   evidence invokes the escalation checkpoints.
   A resolver error supplies no launch settings. Preserve the last valid snapshot;
   write a replacement to a temporary file and promote it only after successful
   resolution and JSON validation. Recover through supported choices rather than
   dropping rejected fields or bypassing resolution.

Use the plugin's Python environment, not a guessed system interpreter. Examples
extend `<plugin_root>/.venv/bin/python -B <plugin_root>/scripts/agent_models.py --repo <repo> --harness <host> --job <job> --route`;
a launch also passes `--assignment <file>`, and `--list-candidates` takes `--agent <agent>` in place of `--job`:

```text
--list-candidates
--selection '{"candidate":"sol","thinking":"high","reason":"Cross-module contracts need deeper diagnosis."}'
--config-profile balanced --target-runtime codex
--selection '{"candidate":"sol","reason":"Only this worker supports the required tools."}' --eligible-candidates '["sol"]'
--assignment /absolute/path/assignment-summary.txt --journal /absolute/path/decisions.jsonl --assignment-id task/1 --eligible-candidates '["luna","sol"]'
--assignment /absolute/path/assignment-summary.txt --journal /absolute/path/decisions.jsonl --assignment-id task/1 --eligible-candidates '["opus"]' --eligible-thinking '{"opus":["medium"]}'
--candidate sol --thinking medium
```

`--eligible-candidates` is an inline JSON array of configured IDs, not a comma
list, filename or model ID. Save a resolver response only after checking its exit
status and parsing the JSON; do not pipe away errors or overwrite a valid snapshot
with failed output. Read launch controls from `settings.model` / `settings.effort`,
the route from `executor` / `executor_config`, and decision provenance from
`decision_model`; absent fields stay absent. `selection_required` supplies no launch.

`--target-runtime` restricts catalog bindings; it never changes the caller adapter
or creates a route. `--candidate` and `--thinking` are user pins, not a way for the
orchestrator to bypass configured bounds. Reserve `--route-choice` for an explicit
user-requested complete instance outside saved policy. Within a pool, `--model`
may only confirm the selected candidate's registered ID.

A user's model pin (an exclusion or restriction the user gave, such as "only local
models") can leave an agent's pool with no eligible candidate. Do not widen the pool,
drop the pin or use `--route-choice` on your own. Ask the user, naming the pool, the
pin and the candidates it excludes. Or, only when the pin itself names the model or
models to use, treat it as the explicit request `--route-choice` needs. Either way,
record the pin and where the user gave it in the [decision report](report.md).

The [selection contract](../../schemas/selection.schema.json) is `candidate`, optional
`thinking`, and a nonempty `reason`. Omitted thinking preserves the candidate's
baseline unless the profile supplies it. `candidate: null` means no suitable worker
and stops resolution. An invalid recommendation returns `selection_required` with
`selection_error` and the built-in router; it never executes an implicit fallback.

An external routing skill owns its API, credentials and uncertainty handling.
Pass a compact assignment, eligible model/effort options, user guidance and relevant
verified outcomes. A Jev adapter can choose one enumerated model/effort pair and
record factual decision provenance; it must not invent a generated explanation.
Unavailability, abstention or invalid router output falls back once to the host
router. Distinguish router uncertainty from no suitable worker; neither consumes
worker repair attempts. The router cannot launch workers, widen pools, waive gates
or recursively select itself. No provider-specific dependency is required by core.

## Shadow selection

Every route from a pool of catalog models also returns `shadow_selection`: what a cost-aware policy would
have chosen, without changing the route. For every candidate this host can reach
through any runtime, except those the host excluded or training endpoints the
repository does not allow, it draws a
sample from a Beta(1 + successes, 1 + failures) acceptance rate for this agent and job in the
outcome ledger, seeded by the assignment ID or text so a
retry draws the same, and subtracts a cost weight when the model's `cost` is
`subscription` or unstated: 0.15, rising to 0.4 when its provider's allowance is
`elevated` and 1.0 when `critical` (see below); `allowance` shows the levels used. The highest utility is its `choice`; `agrees` compares it
with the actual route. A candidate without outcomes keeps a wide prior, so it is
sometimes chosen: that is the exploration. The lifecycle keeps the shadow choice
with each attempt's outcome so the two policies can be compared before routing
switches to it. An unreadable ledger gives `{"status": "unavailable", "error": ...}`
and never fails the route.

## Allowance

`scripts/allowance.py status` reports the Claude Code and Codex subscription windows:
used share, reset time, elapsed share, use projected at reset (once a quarter of the
window has passed, from a reading under 15 minutes old) and a pressure level. An
older reading is marked `stale` and counts only what was already used, since use
never falls before a reset. `elevated` starts at 80% used or projected
and `critical` at 90%; they end only below 65% and 80% and after five minutes at the
level. Codex readings come from the newest rollout under `$CODEX_HOME/sessions`.
Claude readings come from the status line, which
[the catalog](../config/catalog.md#allowance-and-local-capacity) shows how to wire. A provider without a current
reading is `unknown` and keeps the base weight. Routing reads the levels without
changing them.

## Local capacity

The `backend` and `capacity` settings are in
[the catalog](../config/catalog.md#allowance-and-local-capacity).

Slots are shared by every workstream on the machine: the worker lifecycle takes a lease
when it registers an attempt on that backend, after the health and free-memory
checks, and refuses registration when the backend is full or unhealthy; the lease
ends when the attempt's result is collected or it closes. Routes list the candidates
of a backend that is full, fails its health check or lacks free memory in
`unavailable_candidates`, and a route to such a backend
carries `backend` and `capacity` for the lifecycle. Route to the next candidate
rather than waiting.
