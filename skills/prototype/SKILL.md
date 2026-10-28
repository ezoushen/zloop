---
name: prototype
description: "Build throwaway code that answers one design question paper cannot settle (does this logic hold, or what should it look like) and report the verdict. Use when the user wants to sanity-check a state model, an ordering or a rule's edge cases, or to compare a few interface or output variants."
argument-hint: "<the one question to settle>"
---

# Prototype

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

Build throwaway code that answers one question evidence on paper cannot settle.
The user's request or align's prototype job assigns it; the answer, not the code, is
the deliverable.

## The question

The question is one of two kinds: does the logic hold, or what should it look like.
A logic question concerns a state model, an ordering or a rule's edge cases; a look
question, an interface or output a person judges by seeing it. A request with more
than one question, or one of neither kind, goes back to whoever asked.

## Build it

- Write only what answers the question: one entry point, nothing reusable, no
  abstraction for later. Put it beside the code it informs, named and headed so a
  reader sees it is clearly marked as a prototype and not product code.
- Make it trivial to run: one command from the project's existing runner or tooling,
  stated at the top of the prototype and in the verdict.
- Keep no persistence: state lives in memory. When the question is about storage
  itself, use a scratch store named for the prototype and remove it afterwards.
- Show the whole relevant state after each step or variant, so the answer can be
  read from the output rather than inferred.
- For a logic question, drive the cases that are hard to reason about on paper; for
  a look question, offer a few clearly different variants side by side.

Commit it only to the throwaway branch; it never merges into the workstream branch or
`main`, which keep only the verdict.

Done when the run gives a verdict on the question, or shows the question cannot be
answered this way.

## Verdict

State the question, the verdict with the output that decided it, the run command, and
the throwaway branch and commit.

## Invoked directly

When the user asks for a prototype, build it on a throwaway branch in its own worktree
created from the current commit, so the user's checkout stays as it was. Give the
verdict in the conversation, with the run command and the worktree's path, which stays
until the user drops it, so they can see it for themselves; for a look question, say
which variant to look at and how. When the verdict settles a design decision, offer to
record it: a [ledger row](../../runtime/interview/record.md) when a workstream is under way,
and an ADR when it qualifies under the [ADR rules](../../runtime/domain/adr.md#when-to-offer-one).
