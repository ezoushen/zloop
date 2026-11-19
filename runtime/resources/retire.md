# Retire a worker

Retire after final collection and an acceptance decision. Use `collect` with
`disposition.kind: retire`, or `worker_lifecycle.py retire --state <state> --attempt
<attempt> --input <retire.json>` for an already collected attempt. Herdr inputs name
`session`, `pane` and `jobs_evidence`; pane-less native children supply `evidence`.
A ticket still awaiting its acceptance decision includes `acceptance` in this input.

Reconcile the worker's owned jobs and verify creation ownership first. Those remain
host assertions: the helper cannot authenticate ownership or inspect every native
job. Existing user panes stay with their owner. An `owned: false` worker is released,
not closed. Never close or archive a replacement conversation now occupying an old
handle.

Use lifecycle `status` for outstanding receipts, results, acceptance and closure
issues, and `reap --state <state> [--session <session>]` to reconcile already-gone
workers. These operations own the deterministic closure checks. Confirm native
closure before recording `closed`; a lifecycle record alone cannot establish that
native capacity was released. Follow the selected adapter for transport operations.

Retain an incomplete or active worker only with a concrete reason, next use and
evidence. Retention for repair/recheck must preserve the bounded assignment and
independence requirements of its job.
An unsupported close operation leaves capacity unavailable until the runtime
confirms release. Keep uncertain teardown as a gap.
