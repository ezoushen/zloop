---
name: diagnose
description: "Find the cause of a bug, failing check or regression: build a command that fails on it, shrink the case, test ranked hypotheses, and report the cause with a reproducer. Use when the user reports something broken, throwing, failing or slower, or asks why it happens."
argument-hint: "[<symptom, failing command or issue>]"
---

# Diagnose a defect

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

Read when a defect needs its cause established: the user's request, review's
reproduce job and dispatch's blocked-ticket step use it. The procedure establishes the
cause and a reproducer; whoever owns the repair applies the fix.

Contents:
- [Rules](#rules)
- [Build the loop](#build-the-loop)
- [Reproduce and shrink](#reproduce-and-shrink)
- [Rank hypotheses](#rank-hypotheses)
- [Return](#return)
- [Invoked directly](#invoked-directly)

## Rules

- Diagnose only, never repair: edit no product code, test or configuration to make
  the defect go away. A workaround that removes the symptom does not establish the cause.
- One reproducer per finding. A batch of findings gets one reproducer each, so each
  verdict stands or falls alone.
- Redact every secret before showing a command, its output or a captured artifact:
  write `<REDACTED>` in its place, keep credentials in the environment rather than in
  the command, and quote only the lines that carry the signal. When the redacted
  output cannot show the defect, say so and [ask with context](../../runtime/ask/ask.md).
- Ask the user before touching their devices, accounts or running services, naming
  what the step changes and how it is undone. Reading them is not consent to change them.

## Build the loop

First build one fast, repeatable command that fails on this bug, before any theory.
Reading code to form a theory before that command exists is the failure this
procedure prevents. The command:

- drives the code path the symptom comes from and checks the symptom as reported,
  not merely that something ran;
- gives the same verdict every run; for an intermittent defect, raise and record
  the failure rate until it can be debugged;
- finishes in seconds where the project allows, and runs unattended.

Use the project's own test runner, entry point or a small script with a fixed input.
Run it under check [planning](../../runtime/checks/plan.md) and [execution](../../runtime/checks/run.md),
with stable input identity. When no such command can be built, stop and report what
was tried; hypotheses without one are not evidence.

## Reproduce and shrink

Run the command and confirm it fails with the reported symptom, not a nearby failure.
Then shrink it to the smallest case that still fails: remove inputs, callers,
configuration and steps one at a time, rerunning after each cut, until every
remaining part is needed for the failure.

## Rank hypotheses

Then rank 3–5 falsifiable hypotheses before testing any. Each states its prediction:
"if X is the cause, changing Y makes the failure disappear". Test them in rank order,
one variable per probe, and tag any temporary instrumentation so it can be found and
removed. Remove it before returning.

## Return

Report the confirmed cause with the evidence that separates it from the other
hypotheses, the redacted command and its output, and the shrunk case. The failing case
is kept as the regression test: hand it to the repair owner, who turns it into a
failing test at the seam where the defect occurs before fixing. When no seam can hold
it, report that as a finding.

## Invoked directly

When the user asks for a diagnosis, the user's report is the symptom: build the loop
from it and return to the user as this procedure describes. When they also asked for
the fix, this session owns the repair, and it starts only after the confirmed cause is
reported: the shrunk case becomes the failing test at the seam where the defect occurs,
and the fix follows [test-first development](../../standards/tdd.md) with the user in
place of the ticket, as `/zloop:tdd` works it: confirm the seam with the user before
the test, and offer a refactor wider than the fix rather than making it. A fix the user
did not ask for is offered, not applied.
