# The panel

A card that shows what this session's zloop workstream is doing while it is live: what is running, how long it has been
quiet, what blocks it and what comes next. Pi and Claude Code draw it from the same facts, so the words match.

## One fact source

`scripts/panel_state.py` answers for one checkout and one session:

```sh
python3 -B scripts/panel_state.py --repo <main checkout> --session-id <host session> [--json]
```

Text mode prints one row per line and nothing when the workstream is not live. `--json` prints
`{"active", "workstream", "phase", "draft", "rows": [{"kind", "text"}], "more", "strip"}`, with `kind` one of `headline`,
`run`, `wait`, `blocker`, `next`, `counts`, `more`, and `more` every run and wait row a `+N more` row stands for. Hosts render
only these words; they never re-derive a fact.

It reads, read-only: the workstream's run card (`.scratch/<slug>/autopilot.md`), its worker attempt state
(`.scratch/<slug>/reports/workers.sqlite`, the file the worker attempts protocol keeps) and its local tickets
(`.scratch/<slug>/issues/`), with the whole local board as `next_tickets.py` loads and checks it. A session working in a worktree still reads its main checkout's state. An unreadable
state file drops only the rows it would have supplied.

Which workstream is this session's: one a record ties to it, in this order — its `reports/orchestrator.json` naming this
session (the binding `next_tickets.py --orchestrate` writes), the run card naming it, its decision journal, or a
worker attempt this session registered (the `session` the lifecycle records). Of those, the first live one wins,
else the first. A workstream no record ties to this session is another session's, however recent, so a session just
opened shows none. A workstream need not have a card: any of these records is enough, and the headline then has no phase.

Live means the card is unconfirmed, or an attempt is live, or a blocker is recorded, or the card is still open or
stopped on `waiting-on-user`, `paused-allowance`, `paused-by-user` or `blocked`. A run that reached `delivered`,
`delivered-local` or `merged`, or set `phase: ended`, is quiet only while nothing is live or blocked beneath it; one
that records a delivered state on a `local` board stays while its workstream is not delivered, as the stop check holds it.

Rows are ordered by what the person needs first: the headline, then running workers and waiting ones (three each, the
rest of both under one `+N more`), a waiting one being a worker whose result is ready to collect, whose wait ended with
nothing collected and no new wait (`wait ended`: nothing watches it) or that is `retained` open until a later
attempt of its conversation uses it; an attempt whose conversation any attempt closed shows no row. Then one blocker by precedence (the run's own stop first, then the board's failed check, then `not delivered:` and the
first of what keeps a delivered state from holding, with `(+N more)`, then a blocked ticket), the
next step, and
the counts: the tickets' tally, or for a workstream without tickets `done:` and, per job of its workers, the
assignments whose result completed; a job with a plan (`worker_lifecycle.py plan`) counts only its planned
assignments, over the plan's size, even before its first worker. The failed check is this workstream's blocker when a
problem names one of its tickets, or it has a ticket not done, since the scheduler then starts nothing: it shows the
first problem, this workstream's first, with `(+N more)`. The next step is the first ready ticket whose blockers the
board counts as met (each done, and one of another workstream integrated), none while the check fails. A board the
scheduler cannot read is said so in the row it would have supplied, and the other rows stay.
Each host tones a row by its kind — after truncation, so the words stay identical and styling cannot widen a line —
and a run row keeps the default style:

| Row kind | Pi theme token | Claude Code `Text` |
| --- | --- | --- |
| `headline` | `accent`, bold | bold |
| `wait` | `warning` | `warning` |
| `blocker` | `error` | `error` |
| `next` | `muted` | `subtle` |
| `counts`, `more` | `dim` | `subtle` |

## Two surfaces

- **Pi:** its extension reads the snapshot every two seconds and draws the rows under the editor as a widget, sized
  to the terminal width. The card carries every fact, so the status line stays with the engine. It starts open
  unless `ZLOOP_PANEL=off`; `/zloop-panel` opens or closes it for the session, and a closed panel reads nothing.
  Ctrl+Alt+W, or in fullscreen a click on `+N more`, shows the rows it stands for in its place, then a `show fewer`
  line that hides them again.
- **Claude Code:** `hooks/panel.mjs`, loaded from the plugin's `hooks/hooks.json` `modules` beside its classic hooks,
  draws a pane (`ui.render { component: "Pane" }`), docked beside the transcript in fullscreen, else inline at the prompt.
  It refreshes every two seconds, opens the pane when this session's workstream goes live and closes it when the workstream
  goes quiet. A pane the person closes stays closed until another workstream goes live; `/zloop-panel` opens or closes it
  by hand, and one opened that way stays open, saying when nothing is live. An unasked pane on a narrow terminal waits
  undrawn, and the snapshot's `strip` holds the status line until it draws or `/zloop-panel` seats it. What it
  remembers of the pane and the snapshot last read live in the session's state (`types/index.d.ts`), so a reload of
  the plugin keeps a person's close and draws the last rows until its first read. Its `+N more` is a button (a
  click, or `m` while the pane holds the focus) that does what Pi's key does; the expansion is kept there too.

Either host draws no rows when the snapshot is missing, fails or prints something unparseable: a stale or invented
card is worse than none.

## Scope

The panel only shows: its one control expands `+N more`, and it has no control of the run; pausing, taking over and
resuming stay the user's own acts. It reports the workstream the session runs, not other sessions' work: its run and wait rows come only from attempts
registered by a session the workstream's binding names (this session when it keeps none), and it reads no other
checkout's state. Allowance, spend
and context figures are not part of it.

Tests: `tests/test_panel_state.py` (the fact source's rules), `tests/test_pi_panel.py` (the Pi widget) and
`tests/panel.test.ts` (the Claude pane, run by `claude plugin test ./`).
