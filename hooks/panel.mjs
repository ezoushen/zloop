/**
 * zloop's panel in Claude Code: this session's effort, drawn in the band above the prompt while it is live.
 * The rows come from the plugin's fact source, so Pi and Claude Code show the same words for the same state.
 */
const REFRESH_MS = 2000;

/** A character the terminal draws in two cells: East Asian wide and fullwidth blocks, and emoji presentation. */
const WIDE = /[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\u{1F000}-\u{1FAFF}\u{20000}-\u{2FFFD}]/u;
/** Characters that take no cell of their own: variation selectors and zero-width marks. */
const ZERO = /[\u200B-\u200F\u2060\uFE0E\uFE0F]/u;

/** The cells `character` occupies. */
const cellsOf = (character) => (ZERO.test(character) ? 0 : WIDE.test(character) ? 2 : 1);

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

/** The snapshot last read, or null when the fact source could not answer. */
let snapshot = null;
/** Whether this module instance already started the refresh clock. */
let ticking = false;

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

async function refresh($, cwd) {
  snapshot = await readSnapshot($, cwd);
  $.ui.invalidate("ui.render");
}

export function register(on) {
  on("session.start", async ($, e, next) => {
    const started = await next(e);
    const cwd = e?.cwd ?? (await $.session.cwd().catch(() => process.cwd()));
    if (!ticking) {
      ticking = true;
      $.clock.every(REFRESH_MS, () => refresh($, cwd));
    }
    await refresh($, cwd);
    return started;
  });

  on("ui.render", { component: "AbovePrompt" }, async ($, e, next) => {
    const rows = snapshot && snapshot.active && Array.isArray(snapshot.rows)
      ? snapshot.rows.filter((row) => row && typeof row.text === "string" && row.text)
      : [];
    const strip = snapshot && snapshot.active && snapshot.strip ? String(snapshot.strip) : undefined;
    const held = e?.props?.hasSurvey === true;
    const room = typeof e?.props?.maxRows === "number" ? e.props.maxRows : 0;
    if (!rows.length || held || (room > 0 && rows.length > room)) {
      $.ui.status(strip);
      return next(e);
    }
    $.ui.status(undefined);
    const C = $.ui.resolve(e);
    const width = e?.props?.bodyColumns ?? 80;
    return C.Box({
      flexDirection: "column",
      children: rows.map((row) => C.Text({ children: truncateCells(row.text, width) })),
    });
  });
}
