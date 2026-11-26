# Managed dispatch decisions

Reconsider approved unfinished work at dispatch, completion and resume. Decompose
useful independent work without manufacturing workers for a coherent small task.
Use configured routes, isolate concurrent writers and retain one integration owner.
The saved policy survives continuation; it grants no permission, provider or merge authority.

Inspect the run before deciding its next pass:

```sh
python3 <plugin_root>/scripts/dispatch_state.py inspect --card <card>
```

The result supplies current revision, known unfinished assignments, active workers,
gaps and JSON input templates. Include approved remote-tracker work too: inspection
cannot discover unrecorded work or external account use. Reconcile native handles,
reservations and [shared capacity](../resources/capacity.md); evidence must establish
available allowance, rather than assume the scheduling ceiling is capacity.

Write each complete job packet with `assignment`, `attempt`, `dispatch_card` and
`execution` headers. Resolve it through the configured router and save the full
result at its absolute `execution` path:

```sh
python3 <plugin_root>/scripts/agent_models.py --repo <repo> --host <host> --job <job> --route \
  --dispatch-card <card> --assignment-id <attempt> --assignment <packet> --worktree <worktree>
```

Complete a `selection_required` response through the existing selection procedure.
For decision calibration, also supply `--journal`. Keep using the board scheduler
for ticket eligibility and integration checks.

Fill the inspected input template with the admission decision and its evidence:

```sh
python3 <plugin_root>/scripts/dispatch_state.py plan --card <card> \
  --expected-revision <revision> --input <batch.json>
```

Launch selected siblings through the active adapter without waiting for one another.
The launcher records a claim for each assignment and packet digest before starting it.
Registration keeps the matching claim; a missing or mismatched claim records
`unclaimed-launch` and holds the owner's stop pending [reconciliation](collect.md#accept-and-record-outcomes).
Older call-ID-only claims prevent duplicate launches but cannot prove the packet in a
new registration. Verify startup, arm the completion wake after delivering the assignment,
and verify the returned result separately. Follow inspection gaps after collection or
resume. Use [managed controls](control.md)
for phase changes, pause and completion.

A failed launch can leave a claim. Prove that the child did not start, or stop it and
reconcile its owned work, before `dispatch_state.py release --card <card> --attempt
<attempt> --expected-revision <revision> --evidence <absolute-proof-file>`.
Never release an uncertain live child merely to retry.

The scripts check provenance, admission and recorded dependencies/conflicts. They
cannot judge a decomposition, the meaning of capacity/restriction evidence, or
whether the running host actually loaded its hooks. Verify those facts; on-disk
identity alone and unrestricted hand edits do not establish the workflow guarantee.
