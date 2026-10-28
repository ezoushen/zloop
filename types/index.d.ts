/** What zloop's Claude Code panel keeps in the session's state, which outlives a hot reload of hooks/panel.mjs. */
export type PaneMemory = {
  /** Whether the pane was open at the last look, so a pane gone since was closed by the person. */
  wasOpen: boolean
  /** Whether the panel opened the pane unasked, which it then closes once the effort goes quiet. */
  isUnasked: boolean
  /** The effort live when the pane was closed by hand, which stays closed until another effort goes live. */
  closedFor: string | null
}

/** One row of the fact source's snapshot (scripts/panel_state.py), drawn with its kind's emphasis. */
export type Row = { kind: string; text: string }

/** What scripts/panel_state.py answers for this session; the pane draws only what it reads. */
export type Snapshot = {
  active: boolean
  effort: string | null
  rows: Row[]
  /** The run rows `+N more` stands for; absent from a snapshot an earlier version stored. */
  more?: Row[]
  strip: string | null
}

declare module 'claude-code' {
  interface PluginState {
    zloop: { pane: Shaped<PaneMemory>; snapshot: Shaped<Snapshot | null>; expanded: Shaped<boolean> }
  }
}
