# Retire a worker

For Herdr, `collect` with `disposition.kind: retire` closes the registered
conversation's pane after final use. An already collected result uses `retire
--state ... --attempt ... --input /absolute/retire.json`, with `session`, `pane`
and `jobs_evidence`; for a completed `acceptance: true` job (`ticket`) with no `accepted` or `declined`
record it also needs `acceptance` in its own input, even after a retaining `collect`: `{"decision": "accepted", "commit", "evidence"}`,
`{"decision": "declined", "reason"}` or `{"decision": "deferred", "reason"}`, and it refuses before the pane closes
without it. The helper checks ownership, intact collected report,
acknowledged receipts, stopped waits, native runtime/conversation identity and
idle/done state before closing. The registered `worker` must be the returned pane
ID, unique agent name, or explicit `session/pane/name` handle; use the latter for
retained cross-session records. The inherited caller pane is excluded and, when caller context is present,
`pane current --current` resolves moved caller IDs in the explicit session. A
missing caller in that session is allowed; other resolution errors fail closed.
Outside a pane, the host must verify that the target is its owned worker. Job reconciliation and
creation ownership are still host-verified assertions; the helper cannot inspect
all native worker jobs or authenticate ownership claims.

Closure is recorded only after `pane get` returns the native `pane_not_found`
error and, for an attempt that recorded its Herdr tab at registration, `tab get`
returns `tab_not_found` for that tab. A tab that survives, because another pane
joined it, holds closure: close the remaining panes or the tab, then retire again.
A transport error or replaced conversation never counts as closure. If a
pane disappeared before any retirement intent, the host must inspect its native
closure and reconcile owned jobs, then use the evidence-backed `record closed`
path; absence alone does not establish ownership or safe cleanup. At a stop (and on demand with
`worker_lifecycle.py reap --state ... [--session ...]`) the gate records `closed` itself for an owned attempt whose result is
collected and whose recorded tab (else pane) its Herdr server confirms gone, or whose recorded pane it shows holding
another conversation, with that answer as the evidence, and for a retained
native subagent of a run whose card ended `delivered`, `delivered-local` or `merged`, deferring an acceptance still
unsettled, within a few seconds in all (the rest waits for the next stop); a closure the lifecycle refuses is a stop
gap carrying its refusal. A
retirement intent survives interruption: retry the original target after its
monitor is gone, and confirm absence without closing another pane or duplicating
the result. Native pane IDs are not reused. Retirement closes the shared native
conversation for all its historical attempts; `status` includes `native_closed_by`
on each historical attempt to identify that final attempt. Earlier retained
attempts no longer reserve its live handle,
but the retired conversation cannot be registered again; later work starts with
a fresh native session and a compact handoff. Only the final registered attempt may
retire a conversation, after all its attempts have intact results (not one an accepted attempt or a later attempt
recording the same report path owes), consumed receipts and stopped waits. A retirement intent blocks registration of a continuation;
work gates reject the retired conversation across its history. Retain incomplete/active/blocked
workers with their concrete next use or capability limitation. For other native
transports, close through the installed lifecycle tool, confirm closure, then
record `closed` with native evidence. The helper requires a
collected report (intact when the result is completed) and acknowledged receipts. Retain only a stated next use
or a concrete runtime/ownership limitation, with `retain`, `reason`, `next_use` and
evidence. An unsupported close operation leaves capacity unavailable until the
runtime confirms release. Existing user panes stay with their owner.
