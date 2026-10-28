# Record and continue

For work needing a plan, keep consequential decisions in the existing workstream
ledger, normally `.scratch/<slug>/ledger.md`. Reuse its IDs across align/refine:

| # | Decision | Hinge (evidence and what would change it) | Rejected alternatives, if material |
|---|---|---|---|

Keep rows short; omit artificial alternatives and routine implementation choices.
Record the source of user decisions separately from agent decisions. Keep a
`## Standing directives` section in the ledger for the user's corrections to how
agents work (routing, reporting, retention, permissions), quoted with their date. Persist
standing principles only where this project already keeps them; settled terminology
goes to the [glossary](../domain/glossary.md).
Follow the host's rules for writing memory; this procedure supplies no extra permission.

When mandate completes, not after each question round, close the phase record.
End that reply with a short exit block, each item with its context
([ask with context](../ask/ask.md)):
- the user's decisions recorded;
- the agent's decisions, which the user may veto;
- pending actions only the user can take.

When a review triggered this mandate, add one line per open finding: fixed,
superseded, folded into decision `<id>`, or still open. The user should not have to
ask whether questions, decisions or findings remain.

Proceed with already authorized work once no material unresolved choice blocks it.
A ledger is inspectable evidence, not a compulsory approval turn or timeout-based
consent. If the request was only to plan, return the plan. With `--interactive`,
show the concise proposal and wait for the requested human check before dependent
execution; reuse approvals already given. Actual publication/resource permission
gates retain their existing authorization requirements.

Design choices belong to the design half of `/zloop:align`; ticket slicing to `/zloop:refine`.
They reuse this filter and ledger. Explicitly human-in-the-loop activities
still need their live exchange; an agent decision cannot stand in for that human.
