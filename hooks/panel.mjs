/**
 * zloop's panel in Claude Code: this session's workstream, drawn in a pane of its own.
 * The rows come from the plugin's fact source, so Pi and Claude Code show the same words for the same state.
 */
import { atom, read, update } from "claude-code";

const REFRESH_MS = 2000;
const PANE = "zloop-panel";
const QUIET = "No zloop workstream is live in this session.";

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

/** Whether this module instance already started the refresh clock. */
let ticking = false;
/** Whether a refresh is in flight. */
let refreshing = false;
/**
 * What the panel remembers of the pane (types/index.d.ts), kept in the session's state so a hot reload does not
 * forget a person's close. Change the tag when its fields change: it declines what an earlier version stored.
 */
const memory = atom({ plugin: "zloop", key: "pane" }, { wasOpen: false, isUnasked: false, closedFor: null },
  { shape: "pane-1" });
/** The snapshot last read, or null when the fact source could not answer; kept so a reload draws it until the next. */
const snapshot = atom({ plugin: "zloop", key: "snapshot" }, null, { shape: "snapshot-2" });
/** Whether the person expanded `+N more` into the rows it stands for; kept so a reload keeps the choice. */
const expanded = atom({ plugin: "zloop", key: "expanded" }, false, { shape: "expanded-1" });

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

/** The live workstream's name in `facts`, or null while nothing of this session is live. */
const liveWorkstream = (facts) => (facts && facts.active ? String(facts.workstream ?? "") : null);

/**
 * Open the pane while this session's workstream is live, and close the one opened for it once it goes quiet. While the
 * pane waits undrawn (opened unasked on a narrow terminal), the snapshot's strip holds the status line instead.
 */
async function follow($, facts) {
  const live = liveWorkstream(facts);
  const find = async () => (await $.ui.panes()).find((one) => one.id === PANE);
  let pane = await find();
  // Each field is written as soon as it is decided, before the next wait, so a /zloop-panel run meanwhile writes last.
  const remember = (fields) => update($, memory, (now) => ({ ...now, ...fields }));
  let { wasOpen, isUnasked, closedFor } = await read($, memory);
  if (!pane && wasOpen) {
    closedFor = live;
    await remember({ closedFor, isUnasked: false });
  }
  if (pane && live === null && isUnasked) {
    await remember({ isUnasked: false });
    await $.ui.close({ id: PANE });
    pane = await find();
  } else if (!pane && live !== null && live !== closedFor) {
    await remember({ isUnasked: true });
    await $.ui.open({ id: PANE, title: "zloop" });
    pane = await find();
  }
  await remember({ wasOpen: Boolean(pane) });
  $.ui.status(pane && !pane.isPlaced && live !== null && facts.strip ? String(facts.strip) : undefined);
}

/** `/zloop-panel`: close the pane when drawn, holding it closed for this workstream; else open it until closed. */
async function toggle($) {
  // A pane waiting undrawn counts as closed: the person's own open is what seats it.
  const isOpen = (await $.ui.panes()).some((one) => one.id === PANE && one.isPlaced);
  const closedFor = isOpen ? liveWorkstream(await read($, snapshot)) : null;
  await update($, memory, () => ({ wasOpen: !isOpen, isUnasked: false, closedFor }));
  if (isOpen) {
    await $.ui.close({ id: PANE });
    return "zloop panel closed.";
  }
  await $.ui.open({ id: PANE, title: "zloop" });
  return "zloop panel opened.";
}

async function refresh($, cwd) {
  // The clock does not wait for a slow refresh; one overlapping it would read the pane mid-change.
  if (refreshing) return;
  refreshing = true;
  try {
    const facts = await readSnapshot($, cwd);
    // The write draws the pane again.
    await update($, snapshot, () => facts);
    await follow($, facts);
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
    const facts = await read($, snapshot);
    const drawable = (row) => row && typeof row.text === "string" && row.text;
    const rows = facts && facts.active && Array.isArray(facts.rows) ? facts.rows.filter(drawable) : [];
    const more = facts && Array.isArray(facts.more) ? facts.more.filter(drawable) : [];
    const isExpanded = await read($, expanded);
    const C = $.ui.resolve(e);
    const width = e?.props?.bodyColumns ?? 80;
    if (!rows.length) return C.Text({ children: truncateCells(QUIET, width), color: "subtle" });
    const text = (row) => C.Text({ children: truncateCells(row.text, width), ...(TONES[row.kind] ?? {}) });
    // `+N more`, and `show fewer` in its place once pressed: a click, Enter under the focus, or `m` in the pane.
    // A plain Button draws its hotkey and a colon before the label, three cells.
    const expander = (label) => C.Button({ key: "more", hotkey: "m", plain: true, dimColor: true,
                                           onPress: () => update($, expanded, (now) => !now),
                                           children: truncateCells(label, width - 3) });
    return C.Box({
      flexDirection: "column",
      children: rows.flatMap((row) => {
        if (row.kind !== "more" || !more.length) return [text(row)];
        return isExpanded ? [...more.map(text), expander("show fewer")] : [expander(row.text)];
      }),
    });
  });
}
