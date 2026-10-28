# Architecture decision records

The repository's shared decision history: each ADR records that a decision was made
and why, for members and agents who were not there. Grilling offers them, delivery
carries a workstream's lasting decisions into them, and later work follows them.

Contents:
- [Where they live](#where-they-live)
- [Format](#format)
- [When to offer one](#when-to-offer-one)
- [Change and follow them](#change-and-follow-them)
- [Deliver a workstream's decisions](#deliver-a-workstreams-decisions)

## Where they live

ADRs live in `docs/adr/NNNN-slug.md`: the number is the highest existing number plus
one, and the slug names the decision. Create `docs/adr/` lazily, with the first ADR.
With several bounded contexts ([glossary](glossary.md)), system-wide decisions stay in
the root `docs/adr/` and a context's own decisions in `docs/adr/` beside its code.

A project's existing decision-record convention wins, under its own name, location
and numbering; neither the folder nor any ADR is required.

## Format

```md
# {Short title of the decision}

{One to three sentences: the context, what was decided and why.}
```

An ADR is a title and one to three sentences. Add Considered Options and Consequences
sections only when they add something: Considered Options when a rejected
alternative is worth remembering, Consequences when a downstream effect is not obvious.
Status and supersession are front matter (`status:`, `supersedes:`); without them an ADR
is `accepted`. A status is `accepted` (the default), `superseded by ADR-NNNN` or
`deprecated`. There is no `proposed` status: a draft lives in its PR until the user's
yes accepts it.

## When to offer one

Offer one when a decision qualifies by either rule:

- **It affects others:** it shapes the structure, interfaces or dependencies others
  build on, or sets a rule every later change must follow.
- **It passes the three tests**, all of them:
  1. **Hard to reverse:** changing your mind later has a meaningful cost.
  2. **Surprising without context:** a future reader would wonder why it was done this way.
  3. **A real trade-off:** there were genuine alternatives, chosen between for specific reasons.

A bare rule with no real trade-off is not an ADR: it goes where the project keeps its
rules, such as its agent instruction or contributing file. A rule chosen over a real
alternative gets the ADR for its reason, plus a one-line rule there that points to it.

Typical cases: the architecture's shape, how contexts integrate, technology with
lock-in, ownership and scope boundaries (the explicit no's too), deliberate departures
from the obvious path, constraints invisible in the code, and a rejected alternative
someone would otherwise propose again.

Offer the ADR to the user and write it only after their yes; an agent never writes one
on its own judgment. Before offering, check the [index](adr-index.md): a decision that
changes an earlier one is offered as a replacement, never a duplicate.

## Change and follow them

Never rewrite an accepted ADR. A changed decision is a new ADR that names the ADR it
replaces in `supersedes: ADR-NNNN`; the old ADR's only edit is its status line, which
becomes `superseded by ADR-NNNN`. Repairing a broken status or supersede link in an ADR's
front matter is the one other edit; its text never changes. A partial overrule supersedes the whole ADR and
restates what still holds, so one ADR states each current decision. A decision dropped
without a replacement becomes `deprecated`. Accepted ADRs bind later work: read the
[index](adr-index.md) and the ADRs in the area before planning or changing it. Work that would contradict one
stops for a design decision; one that looks outdated is flagged to the user, not
silently followed.

## Deliver a workstream's decisions

A workstream's ledger stays in ignored `.scratch/`, so its lasting decisions reach other
members only as ADRs. When the workstream's implementation is complete, list each ledger
row that qualifies as a candidate ADR in the dispatch report, with the row it
comes from, and ask the user once for the whole list. Do not wait for the answer:
write only the candidates the user approves into the workstream branch, those answered
before its review head is pinned, so the review covers them; candidates still
unanswered wait among the actions only the user can take, and an approved candidate
written later returns that change to review and E2E.

Numbers are claimed on `main`. Commit the approved ADRs before the check. Fetch first,
then, before pinning the review head, merge the verified `main` tip into the workstream
branch and run the ADR number check; on a duplicate, renumber the workstream's ADR to the
next free number and update every reference to it in the same commit. The delivery
gate repeats the check without merging, against each `main` the workstream lands on: the
local one and, once fetched, the remote's when the workstream is published:

```sh
python3 <plugin_root>/scripts/adr_numbers.py --repo <repo> --ref <workstream branch> --against <main tip> --index [--dir <ADR folder>]
```

It prints the duplicated numbers, the next free one and any index problems, and exits
1 on either. Fix an index problem the workstream caused in the same commit as its ADR: list
the ADR under its status, or correct the status or supersede link. A problem that comes
from `main` is fixed in the workstream branch with the merge, whatever it needs: an index
line, a status line or a supersede link. Run it once for each ADR folder: the
root one and each context's. `--index` applies this convention's index; a project with
its own decision-record convention drops it, and one that also numbers differently
uses its own check or records an evidenced skip. A merge, renumbering or index fix
after the review head is pinned returns to review and E2E, and a duplicate number or
an index problem fails the delivery gate.
