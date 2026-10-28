---
name: research
description: "Answer a question from primary sources, citing every claim and checking every real consumer, and save the findings as notes. Use when a plan, review or decision depends on a fact nobody has checked: documentation, an API, code usage or behavior."
argument-hint: "<question>"
---

# Research a question

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

Read when a plan, review or blocked ticket depends on a fact nobody has checked:
the user's request, align, review and dispatch use it. The procedure gathers evidence;
the caller decides what to do with it.

## Rules

- Read primary sources: the project's own code, configuration and data, the official
  documentation, specifications and first-party interfaces of what it depends on.
  A secondary write-up is a lead to its source, not evidence.
- Cite every claim to the source that owns it: a path and line, a command and its
  output, or a link with the version it describes. A claim with no source is marked
  unverified, never stated as fact.
- Measure every real consumer, not one. When a fact is about how something is used,
  find all its callers, readers, inputs or users in scope and check each; a single
  sample shows only that one case. Name the ones that could not be checked.
- Settle every available fact before asking the user to choose. A question to the
  user carries only what the evidence cannot decide, [with context](../../runtime/ask/ask.md).
- No separate fact-checker is mandatory. The researcher's citations are the check;
  the caller adds an independent verification only where a wrong fact would be costly.

## Run

Run it as a background worker so the caller keeps working while it reads: a helper
agent of the host, not a job of the builder, reviewer or verifier, on the cheapest
model adequate for reading. Count it in [shared capacity](../../runtime/resources/capacity.md)
and record it in the workstream's run report when one exists, like any other worker. It reads and runs read-only
commands; it changes no code, configuration or tracker state.

Save the findings where the project already keeps such notes, following its
convention; with none, use the workstream's `.scratch/<slug>/` folder and say where.
Return the file's path, the answer in a few lines, and the open points with what
each would take to settle.

## Invoked directly

When the user asks for research, the user's question is the question and this session
is the caller. With no workstream under way and no notes convention, save the findings in
`.scratch/<topic>/`, named for the question. With no run report, name the helper in the answer.
Then answer in the conversation: the answer with its citations, the file's path, and
the open points.
