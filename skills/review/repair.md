# Review repair and evidence pin

The repair and pin stages of the [shared review procedure](SKILL.md).

## Optional repair

Only with `--autofix`, assign confirmed in-scope findings in the agreed priority
order to a builder's [repair](jobs/repair.md) job. Include the exact allowed files/behaviors,
priority authority and accepted test seams. No automatic fix for unverified claims,
follow-ups, personal taste or unresolved design decisions. A materially wider fix
returns for human alignment; meanwhile finish independent permitted work.

Batch compatible confirmed findings after the layer's findings are consolidated.
Attribute each confirmed finding to the collected worker attempt that introduced it,
when known, with the lifecycle's `attribute --kind review-defect`; a repair attempt
names the attempt it fixes in `repairs`
([attribution](../../runtime/workers/collect.md#accept-and-record-outcomes),
[repair registration](../../runtime/workers/prepare.md)).
Before assigning the next repair or retry, apply the shared
[escalation checkpoints](../../runtime/workers/escalation.md)
to this evidence: run the configured `recovery` checkpoint for the action before
deciding who executes it. A worker-routing call alone does not satisfy recovery.
Check this phase record before acting, record its outcome at the repair handoff,
then report any separate worker decision with attribution. An orchestrator-authored
fix after review is a repair too. It needs the `recovery` record and a recheck of
its delta before delivery.
One writer per worktree; one required suite run can cover compatible repairs, while
each finding retains its own deciding evidence. After repairs, verify original
repros and required checks; review the repair delta and affected behavior. Preserve
initial coverage, reopen impacted items, and update the reviewed head only after they pass. Two
unsuccessful repair attempts for the same finding → record blocked with evidence;
continue other findings. No unchanged third attempt. When fixes keep needing fixes,
attack the premise: a repair of a fix of a fix names in `premise` the assumption the
earlier fixes shared; remove the skewed cause the returned census shows (route or
design) rather than patch again. With `--autofix`, continue
through the [E2E verification](e2e.md) even if no repairs were needed. A read-only
review does not automatically run E2E.

## Pin review evidence

Before finalizing, re-read the target head and base. If they moved, report this
snapshot as stale and review the delta against a newly pinned pair before claiming
the current PR was reviewed. Preserve user scope decisions; don't carry old inline
locations forward blindly. For autofix, distinguish the local repair head from
the unchanged remote head; preview fixed code locally and leave unresolved comments
anchored only where they remain valid on the actual PR diff.

Set `review_status: clean|findings|incomplete` and `review_head` in the report, and
set `review_by` to the reviewer runs (agent or attempt IDs) that produced it.
Clean requires complete coverage, no unresolved in-scope findings or scope decisions
(including undecided agent deferrals of the three non-deferrable kinds in [coverage](coverage.md)),
evidence for the pinned revision, and `documentation_status: passed`. A missing
documentation check cannot be treated as passed; an old report needs that review
before reuse. Follow-ups outside this batch don't prevent its clean status;
requested follow-up closure still governs completion of the overall invocation.
Record `review_base` and `review_diff_base` too; a changed base invalidates scope.
