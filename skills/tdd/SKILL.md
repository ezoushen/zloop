---
name: tdd
description: "Build a feature or fix a bug test-first in this session: agree the seams, then one failing test, the least code to pass it and a refactor, one business rule at a time, each test shown to fail when its rule breaks. Use when the user wants test-first work, red-green-refactor, or tests that protect business rules."
argument-hint: "[<the behavior to build or the bug to fix>]"
---

# Test-first work

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

Build a behavior change test-first in this session; read
[test-first development](../../standards/tdd.md) in full before proposing the seams. The
standard is written for a builder working a ticket; here there is no ticket, so the
user stands in for it: the rules, their expected values and the seams come from the
user.

Contents:
- [Settle the rules](#settle-the-rules)
- [Agree the seams](#agree-the-seams)
- [Work the loop](#work-the-loop)
- [Where the standard names a ticket](#where-the-standard-names-a-ticket)
- [Finish](#finish)

## Settle the rules

Name each business rule the change must satisfy as an observable outcome, and where its
expected values come from: the user's words, a spec, a worked example or a reproducer
of the bug. A rule with no such source is a question for the user,
[with context](../../runtime/ask/ask.md), not a value to read off the code. Read the
project's instructions and test conventions: where tests live, the runner, and the
command that runs one test.

## Agree the seams

Before any test, propose the public interfaces the tests will reach, each with one line
on what it catches and what it misses. Prefer the highest existing seam that exposes the
rule, and the fewest seams, in the vocabulary of [module design](../../standards/design.md).
Confirm them with the user: a seam the user did not confirm gets no test.

## Work the loop

Run the relevant tests once before the first new one, and record pre-existing failures as
such, so none is later taken for this change's. Then work the standard's loop one rule
per cycle, recording each cycle's failing, passing and break runs as the standard says.
Show them as they happen, or keep them in the effort's report when one is under way.

## Where the standard names a ticket

- Its seams line is the seams agreed with the user.
- A seam the ticket does not name goes back to the user, not to an orchestrator.
- A refactor wider than the change's own code is offered to the user, not done.
- Acceptance criteria are the rules settled with the user.

## Finish

Report each rule with the test that protects it, its recorded failing, passing and break
runs, the refactors made after green, and any rule left without a test and why. Run the
project's required checks before calling the work done. Report no coverage figure as
proof; coverage means every rule has a test that fails when the rule breaks.
