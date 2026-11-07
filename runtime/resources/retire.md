# Retire a worker

For Herdr, `collect` with `disposition.kind: retire` closes the registered
conversation's pane after final use. An already collected result uses `retire
--state ... --attempt ... --input /absolute/retire.json`, with `session`, `pane`
and `jobs_evidence`; for a completed `acceptance: true` job (`ticket`) with no `accepted` or `declined`
record it also needs `acceptance` in its own input, even after a retaining `collect`: `{"decision": "accepted", "commit", "evidence"}`,
`{"decision": "declined", "reason"}` or `{"decision": "deferred", "reason"}`, and it refuses before the pane closes
without it. One check serves `retire`, `collect`, `record closed` and the reap: an intact collected
report, acknowledged receipts and stopped waits for each attempt of the conversation, and a settled acceptance. Retire
also checks the pane's native runtime, its conversation and its idle/done state before closing. An attempt registered
`owned: false` is released instead: `retire` (or `collect` with `disposition.kind: retire`) records `closed` after that check
alone, asks Herdr nothing, leaves its pane open and needs no `session`, `pane` or `jobs_evidence`; the panel then shows no row for
it. The inherited caller pane is excluded and, when caller context is present,
`pane current --current` resolves moved caller IDs in the explicit session. A
missing caller in that session is allowed; other resolution errors fail closed.
Outside a pane, the host must verify that the target is its owned worker. Job reconciliation and
creation ownership are still host-verified assertions; the helper cannot inspect
all native worker jobs or authenticate ownership claims.

Retire, `record closed` and the reap judge Herdr's answer for the recorded tab and pane alike: the tab (else the
pane) confirmed absent, or no server running, means gone, and a pane holding another conversation means the worker
is replaced. Either ends the attempt. At a stop, or on demand with `worker_lifecycle.py reap --state ... [--session ...]`,
the gate records `closed` itself for an owned attempt whose result is collected, and for a retained native subagent of
a run whose card ended; a closure the lifecycle refuses is a stop gap carrying its reason. Closing by hand
is `record closed` with the evidence: Herdr's answer, or native evidence for an attempt with no Herdr pane.
Native pane IDs are not reused. Retirement closes the shared native
conversation for all its historical attempts; `status` includes `native_closed_by`
on each historical attempt to identify that final attempt. Earlier retained
attempts no longer reserve its live handle,
but the retired conversation cannot be registered again; later work starts with
a fresh native session and a compact handoff. Only the final registered attempt may
retire a conversation, after all its attempts have intact results (not one an accepted attempt or a later attempt
recording the same report path owes), consumed receipts and stopped waits. Work gates reject the retired conversation across its history. Retain incomplete/active/blocked
workers with their concrete next use or capability limitation. For other native
transports, close through the installed lifecycle tool, confirm closure, then
record `closed` with native evidence. The helper requires a
collected report (intact when the result is completed) and acknowledged receipts. Retain only a stated next use
or a concrete runtime/ownership limitation, with `retain`, `reason`, `next_use` and
evidence.
An unsupported close operation leaves capacity unavailable until the
runtime confirms release. Existing user panes stay with their owner.
