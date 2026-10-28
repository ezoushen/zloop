# Slicing tickets

How work is cut into tickets, wherever it is cut: refinement of an alignment or a
plan agreed in chat, and dispatch splitting selected work that is too large for one
worker. The cutting procedure owns stories, criteria and publication; this doc owns
where the cuts go.

## Slices

Cut thin end-to-end slices: each delivers one observable behaviour through every
layer it needs, each verifiable on its own, each sized for one fresh context window.
A slice that only builds one layer for a later slice to use is not a ticket.
Preparatory refactors and wide mechanical changes are the exceptions to end-to-end slices.

Preparatory refactors come first: when a slice is easier after a change that keeps
behaviour the same, that change is its own earlier ticket, and the slice is
blocked by it.

A wide mechanical change, such as a rename or an interface replaced across many
callers, runs as expand → migrate in batches → contract: add the new form beside the
old, move callers over in batches, then remove the old form once nothing uses it.
Expand, each migrate batch and contract are tickets: each batch is blocked by expand
and contract is blocked by every batch, with batches sized by blast radius. Batches
that cannot stay green alone share the workstream branch and end in the spec E2E: write
them as ordinary tickets in a chain, with no separate integration branch or
integrate-and-verify ticket.

## Edges

Each ticket lists what blocks it. Draw an edge only where one ticket needs another's
result; independent slices stay parallel. Ticket cycles and cycles between workstreams
are invalid.

A cross-workstream edge waits for the prerequisite workstream to finish and merge into main.
Work that must integrate before then belongs in the same workstream.

## Grouping

Weigh handoff cost before splitting adjacent outcomes. When they share the same small
implementation, fixtures and verification, keep them together as one coherent slice;
split where independent delivery, risk, dependencies or context size earns the extra
worker setup and integration. Individual settings or acceptance criteria need not
each become a ticket.

Tickets sharing a device, serving lane or mutable fixture share a reservation, even
when their files differ; account for that resource during implementation, not only E2E.

## Experiments before automation

Separate the experiment that decides value from the automation that depends on it,
and run it first. For optimization or operational work, distinguish research hypotheses from measurements
on the actual targets. When local applicability or benefit decides the implementation,
put a baseline using existing tools first, then a representative trial before wider
application. Define the metric, comparable workload conditions and success criterion,
and keep a supported no-change outcome when the change adds no value. Evidence from
another environment suggests what to investigate, not what to build. When the result
can make later work unnecessary, the later work is blocked by it and stays deferred
scope until the result is accepted.
