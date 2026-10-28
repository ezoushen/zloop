# The panel

A card that shows what this session's zloop effort is doing while it is live: what is running, how long it has been
quiet, what blocks it and what comes next. Pi and Claude Code draw it from the same facts, so the words match.

## One fact source

`scripts/panel_state.py` answers for one checkout and one session:

```sh
python3 -B scripts/panel_state.py --repo <main checkout> --session-id <host session> [--json]
```

Text mode prints one row per line and nothing when the effort is not live. `--json` prints
`{"active", "effort", "phase", "draft", "rows": [{"kind", "text"}], "strip"}`, with `kind` one of `headline`, `run`,
`wait`, `blocker`, `next`, `counts`, `more`. Hosts render only these words; they never re-derive a fact.

It reads, read-only: the effort's run card (`.scratch/<slug>/autopilot.md`), its worker attempt state
(`.scratch/<slug>/reports/workers.sqlite`, the file the worker attempts protocol keeps) and its local tickets
(`.scratch/<slug>/issues/`). A session working in a worktree still reads its main checkout's state. An unreadable
state file drops only the rows it would have supplied.

Which effort is this session's: the one whose `reports/coordinator.json` names this session — the binding
`next_tickets.py --coordinate` writes — else the run card naming it, else the effort holding its decision journal.
An effort no record ties to this session is another session's, however recent, so a session just opened shows none.
An effort need not have a card: its binding or journal is enough, and the headline then has no phase.

Live means the card is unconfirmed, or an attempt is live, or a blocker is recorded, or the card is still open or
stopped on `waiting-on-user`, `paused-allowance`, `paused-by-user` or `blocked`. A run that reached `delivered`,
`delivered-local` or `merged`, or set `phase: ended`, is quiet only while nothing is live or blocked beneath it.

Rows are ordered by what the person needs first: the headline, then running workers (three, then `+N more`), a
worker whose result is ready to collect, one blocker by precedence (the run's own stop first, then a blocked
ticket), the next step, and the counts. Each host tones a row by its kind — after truncation, so the words stay
identical and styling cannot widen a line — and a run row keeps the default style:

| Row kind | Pi theme token | Claude Code `Text` |
| --- | --- | --- |
| `headline` | `accent`, bold | bold |
| `wait` | `warning` | `warning` |
| `blocker` | `error` | `error` |
| `next` | `muted` | `subtle` |
| `counts`, `more` | `dim` | `subtle` |

## Two surfaces

- **Pi:** its extension reads the snapshot every two seconds and draws the rows under the editor as a widget, sized
  to the terminal width. Its band cannot be hidden, so the card carries every fact and the status line
  stays with the engine. `ZLOOP_PANEL=off` leaves the panel unwired.
- **Claude Code:** `hooks/panel.mjs`, loaded from the plugin's `hooks/hooks.json` `modules` beside its classic hooks,
  draws a pane (`ui.render { component: "Pane" }`), docked beside the transcript in fullscreen, else inline at the prompt.
  It refreshes every two seconds, opens the pane when this session's effort goes live and closes it when the effort
  goes quiet. A pane the person closes stays closed until another effort goes live; `/zloop-panel` opens or closes it
  by hand, and one opened that way stays open, saying when nothing is live. An unasked pane on a narrow terminal waits
  undrawn, and the snapshot's `strip` holds the status line until it draws or `/zloop-panel` seats it. What it
  remembers of the pane and the snapshot last read live in the session's state (`types/index.d.ts`), so a reload of
  the plugin keeps a person's close and draws the last rows until its first read.

Either host draws no rows when the snapshot is missing, fails or prints something unparseable: a stale or invented
card is worse than none.

## Scope

The panel only shows. It has no keys, no buttons and no control of the run; pausing, taking over and resuming stay
the user's own acts. It reports the effort the session runs, not other sessions' work. Allowance, spend and context
figures are not part of it.

Tests: `tests/test_panel_state.py` (the fact source's rules), `tests/test_pi_panel.py` (the Pi widget) and
`tests/panel.test.ts` (the Claude pane, run by `claude plugin test ./`).
