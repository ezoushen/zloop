/**
 * zloop's panel in Claude Code: this session's effort, drawn in a pane of its own.
 * The rows come from the plugin's fact source, so Pi and Claude Code show the same words for the same state.
 */
const REFRESH_MS = 2000;
const PANE = "zloop-panel";
const QUIET = "No zloop effort is live in this session.";

/** A character the terminal draws in two cells: East Asian wide and fullwidth blocks, and emoji presentation. */
const WIDE = /[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\u{1F000}-\u{1FAFF}\u{20000}-\u{2FFFD}]/u;
/** Characters that take no cell of their own: variation selectors and zero-width marks. */
const ZERO = /[\u200B-\u200F\u2060\uFE0E\uFE0F]/u;

/** The cells `character` occupies. */
const cellsOf = (character) => {
  if (ZERO.test(character)) return 0;
  return WIDE.test(character) ? 2 : 1;
};

/** `text` cut to `width` terminal cells, ended with an ellipsis when anything was dropped. */
function truncateCells(text, width) {
  if (width <= 0) return "";
  let out = "";
  let used = 0;
  for (const character of text) {
    const cells = cellsOf(character);
    if (used + cells > width) {
      while (used > width - 1 && out) {
        const dropped = [...out].pop();
        out = out.slice(0, -dropped.length);
        used -= cellsOf(dropped);
      }
      return `${out}…`;
    }
    out += character;
    used += cells;
  }
  return out;
}

/** The emphasis each row kind takes; a run row keeps the default style. */
const TONES = {
  headline: { bold: true },
  wait: { color: "warning" },
  blocker: { color: "error" },
  next: { color: "subtle" },
  counts: { color: "subtle" },
  more: { color: "subtle" },
};

/** The snapshot last read, or null when the fact source could not answer. */
let snapshot = null;
/** Whether this module instance already started the refresh clock. */
let ticking = false;
/** Whether a refresh is in flight. */
let refreshing = false;
/** Whether the pane was open at the last look, so a pane gone since was closed by the person. */
let wasOpen = false;
/** Whether the panel opened the pane unasked, which it then closes once the effort goes quiet. */
let isUnasked = false;
/** The effort live when the pane was closed by hand, which stays closed until another effort goes live. */
let closedFor = null;

/** Ask the fact source for this session's rows; failures and unparseable output leave nothing to draw. */
async function readSnapshot($, cwd) {
  try {
    const session = await $.session.id();
    const run = await $.process.run(
      ["python3", "-B", `${$.plugin.root}/scripts/panel_state.py`, "--repo", cwd, "--session-id", session, "--json"],
      { cwd, timeoutMs: 8000 });
    if (run.exitCode !== 0 || !run.stdout) return null;
    return JSON.parse(run.stdout);
  } catch {
    return null;
  }
}

/** The live effort's name, or null while nothing of this session is live. */
const liveEffort = () => (snapshot && snapshot.active ? String(snapshot.effort ?? "") : null);

/**
 * Open the pane while this session's effort is live, and close the one opened for it once it goes quiet. While the
 * pane waits undrawn (opened unasked on a narrow terminal), the snapshot's strip holds the status line instead.
 */
async function follow($) {
  const live = liveEffort();
  const find = async () => (await $.ui.panes()).find((one) => one.id === PANE);
  let pane = await find();
  if (!pane && wasOpen) {
    closedFor = live;
    isUnasked = false;
  }
  if (pane && live === null && isUnasked) {
    isUnasked = false;
    await $.ui.close({ id: PANE });
    pane = await find();
  } else if (!pane && live !== null && live !== closedFor) {
    isUnasked = true;
    await $.ui.open({ id: PANE, title: "zloop" });
    pane = await find();
  }
  wasOpen = Boolean(pane);
  $.ui.status(pane && !pane.isPlaced && live !== null && snapshot.strip ? String(snapshot.strip) : undefined);
}

/** `/zloop-panel`: close the pane when it is drawn, holding it closed for this effort; else open it until closed. */
async function toggle($) {
  // A pane waiting undrawn counts as closed: the person's own open is what seats it.
  const isOpen = (await $.ui.panes()).some((one) => one.id === PANE && one.isPlaced);
  isUnasked = false;
  wasOpen = !isOpen;
  if (isOpen) {
    closedFor = liveEffort();
    await $.ui.close({ id: PANE });
    return "zloop panel closed.";
  }
  closedFor = null;
  await $.ui.open({ id: PANE, title: "zloop" });
  return "zloop panel opened.";
}

async function refresh($, cwd) {
  // The clock does not wait for a slow refresh; one overlapping it would read the pane mid-change.
  if (refreshing) return;
  refreshing = true;
  try {
    snapshot = await readSnapshot($, cwd);
    await follow($);
    $.ui.invalidate("ui.render");
  } finally {
    refreshing = false;
  }
}

export function register(on) {
  on("session.start", async ($, e, next) => {
    const started = await next(e);
    await $.command.register({ name: "zloop-panel", description: "Open or close the zloop panel", immediate: true });
    const cwd = e?.cwd ?? (await $.session.cwd().catch(() => process.cwd()));
    if (!ticking) {
      ticking = true;
      $.clock.every(REFRESH_MS, () => refresh($, cwd));
    }
    await refresh($, cwd);
    return started;
  });

  on("command.run", { command: "zloop-panel" }, async ($) => ({ text: await toggle($) }));

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const rows = snapshot && snapshot.active && Array.isArray(snapshot.rows)
      ? snapshot.rows.filter((row) => row && typeof row.text === "string" && row.text)
      : [];
    const C = $.ui.resolve(e);
    const width = e?.props?.bodyColumns ?? 80;
    if (!rows.length) return C.Text({ children: truncateCells(QUIET, width), color: "subtle" });
    return C.Box({
      flexDirection: "column",
      children: rows.map((row) => C.Text({ children: truncateCells(row.text, width), ...(TONES[row.kind] ?? {}) })),
    });
  });
}
