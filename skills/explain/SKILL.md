---
name: explain
description: "Explain code, a change or a design so the user follows it: the headline first, then the story in layers, with one small text picture (call tree, file tree, sequence, diff, table) where a shape helps. Use when the user asks to explain, walk through or show how something works."
argument-hint: "[<what to explain>] [<depth or focus>]"
---

# Explain

First read: this procedure alone. Skip the runtime bootstrap and the host session part:
explaining launches nothing.

How to explain something so the user follows it: tell it in layers, re-pitch when they
are lost, and show a shape with the smallest view. The user's request, briefings,
walkthroughs, the setup tour and `huh` use it; each caller supplies what to cover.
Questions to the user [ask with context](../../runtime/ask/ask.md).

Contents:
- [Tell it in layers](#tell-it-in-layers)
- [Re-pitch what lost the reader](#re-pitch-what-lost-the-reader)
- [Choose the smallest view](#choose-the-smallest-view)
- [Keep to the caller's scope](#keep-to-the-callers-scope)
- [Invoked directly](#invoked-directly)

## Tell it in layers

1. Lead with the headline in plain language: the outcome, the most consequential
   current fact, or the decision that needs the user.
2. Then tell the story in the order the reader needs it. For a change: a concrete
   problem scenario and the intended outcome, current against proposed behavior, then
   one representative flow. For a state: what is complete, what is running now, what
   comes next, and what needs the user.
3. Introduce a technical name when its responsibility becomes relevant. Explain
   mechanisms or tradeoffs only when they help the user steer, and surface the
   consequential ones the user may not know to ask about. Use the glossary's terms and
   avoid its `_Avoid_` words ([glossary](../../runtime/domain/glossary.md)).
4. Go deeper on request, one topic at a time: follow the user's question rather than a
   questionnaire, answer at the depth it needs, and match an explicitly requested depth
   or focus. Recommend a useful next topic and leave room for questions.
5. Explain before asking. Present the explanation in the conversation before any
   decision; a question, file names or an artifact link alone cannot establish it.
6. At meaningful checkpoints, and at the end, say what is settled, what remains
   uncertain, and the recommended next step with any input needed from the user. After a
   tangent, return to the remaining decision.

## Re-pitch what lost the reader

When the user says they are lost, re-pitch by
[re-pitch what lost the reader](../../runtime/ask/ask.md#re-pitch-what-lost-the-reader)
instead of telling it in layers again, with a view from
[the smallest view](#choose-the-smallest-view) only when the shape is what confuses them.

## Choose the smallest view

Pick one view that makes the point, put it next to the sentence it supports, and keep
only the steps, files, states and boundaries the question needs. Several views are
sometimes right; all of them never are.

- **Steps or a rule:** short pseudocode.

  ```text
  on retry
    if the attempt changed nothing
      stop and report the blocker
    otherwise start the next attempt
  ```

- **Who calls whom at run time:** an indented call tree.

  ```text
  dispatch
    select ticket
    launch worker
      wait for return
    accept and merge
  ```

- **How a screen is built:** a component tree, with the state and module boundaries
  that matter.

  ```text
  SessionPage           owns the session state
    Toolbar
      RunButton         from the shared UI package
    Timeline
  ```

- **What lives where:** a shallow file tree with one comment per entry.

  ```text
  skills/     what the user types or the agent loads
  runtime/    shared rules the skills read
  hooks/      checks the host runs on events
  ```

- **Order between parties:** a sequence as text arrows (`user -> host: request`), one
  line per message.
- **What changes:** a `diff` of the same shape (call tree, component tree, file tree,
  steps or a block), so unchanged lines give the context. Show the whole block instead
  when most of it is new, when leaving lines out would hide ownership or order, or when
  the user needs a copyable target.
- **A layout, a comparison of states, or a picture too dense for text:** a short table,
  or, when the user asks for a file, one focused page: a diagram, an infographic or a
  short deck. Match the product's colors, type, spacing and components, use real labels
  and data, support desktop and mobile, and open it through the host's preview when it
  has one.

Draw with text pictures by default: they read the same in every terminal. Use a rendered
diagram format only where the reader's surface renders it, such as a PR body or a
Markdown page, and confirm it renders there.

## Keep to the caller's scope

The calling procedure decides what is in scope. An explanation stays in the
conversation; create or export a file only when the user asks. Explaining runs no
investigation of its own: re-read facts already established, and mark a claim you have
not checked as unchecked.

## Invoked directly

When the user asks for an explanation, the user's question sets the scope and this
session is the caller. Read the code, documents or state the question concerns first,
read-only, so each fact comes from its source; changing anything is outside an
explanation. Then explain by this procedure, and mark a claim you have not checked as
unchecked. Progress on an effort, branch or PR is a briefing, not an explanation: point
the user to the brief skill, typed the host's way (`/zloop:brief` in Claude Code,
`$zloop:brief` in Codex, `/skill:brief` in Pi), which reads the live worker and
check evidence.
