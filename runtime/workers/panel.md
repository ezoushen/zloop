# The panel

The panel shows the current session's workstream, running and waiting workers,
blocker and next step. Inspect its facts with:

```sh
python3 -B scripts/panel_state.py --repo <main checkout> --session-id <host session>
```

Hosts render the returned words; they must not re-derive scheduling, ownership or
completion facts. A missing or unreadable snapshot cannot justify a stale or
invented card. Use lifecycle and board inspection to investigate gaps.

- Pi: `/zloop-panel` toggles the widget; Ctrl+Alt+W expands extra rows.
- Claude Code: `/zloop-panel` toggles its pane; the extra-row button or `m` expands
  it. A person's closed pane stays closed until another workstream becomes live.

The panel only observes this session's work. Pause, takeover and resume remain
explicit workflow actions. Allowance, spend and context figures belong to their
own sources.

Snapshot rules are executable in `scripts/panel_state.py`; the selected adapter
owns rendering. Verify changes with
`tests/test_panel_state.py`, `tests/test_pi_panel.py` and `tests/panel.test.ts`
(`claude plugin test ./` for the Claude pane).
