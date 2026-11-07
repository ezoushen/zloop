# The panel

A card that shows what this session's zloop workstream is doing while it is live: what is running, how long it has been
quiet, what blocks it and what comes next. Pi and Claude Code draw it from the same facts, so the words match.

## One fact source

`scripts/panel_state.py` answers for one checkout and one session:

```sh
python3 -B scripts/panel_state.py --repo <main checkout> --session-id <host session>
```

It prints one JSON line,
`{"active", "workstream", "rows": [{"kind", "text"}], "more", "strip"}`, with `kind` one of `headline`,
`run`, `wait`, `blocker`, `next`, `counts`, `more`, and `more` every run and wait row a `+N more` row stands for. Hosts render
only these words; they never re-derive a fact.

It reads, read-only: the workstream's run card (`.scratch/<slug>/autopilot.md`), its worker attempt state
(`.scratch/<slug>/reports/workers.sqlite`, the file the worker attempts protocol keeps) and its local tickets
(`.scratch/<slug>/issues/`), with the whole local board as `next_tickets.py` loads and checks it. A session working in a worktree still reads its main checkout's state. An unreadable
state file drops only the rows it would have supplied.

Which workstream is this session's: one a record ties to it (the `reports/orchestrator.json` binding that
`next_tickets.py --orchestrate` writes, the run card, its decision journal, or a worker attempt this session registered);
the first live one wins, else the first. A workstream no record ties to this session is another session's, so a session
just opened shows none. A card is not required.

Live means the run is not quietly over: its card is unconfirmed or open or stopped on a state that waits for the user,
an attempt is live or a blocker is recorded. A delivered run is quiet once nothing beneath it is live or blocked; on a
`local` board it stays while its workstream is not delivered, as the stop check holds it.

Rows go by what the person needs first: the headline, running workers and waiting ones (three each, the rest of both
under one `+N more`), one blocker, the next step, then the counts. A waiting worker is one whose result is ready to
collect, whose wait ended with nothing watching it, or that is retained for a later attempt. The next step is the
scheduler's first ready pick (`next_tickets.py <slug>`). A board the scheduler cannot read is said so in its row,
and the other rows stay.
Each host tones a row by its kind — after Pi's cut to the terminal's width (the Claude Code pane wraps a long row
onto the lines under it instead), so the words stay identical and styling cannot widen a line —
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
resuming stay the user's own acts. It reports the workstream the session runs, not other sessions' work, and reads no other
checkout's state. Allowance, spend
and context figures are not part of it.

Tests: `tests/test_panel_state.py` (the fact source's rules), `tests/test_pi_panel.py` (the Pi widget) and
`tests/panel.test.ts` (the Claude pane, run by `claude plugin test ./`).
