import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// The Pi panel draws the fact source's rows below the editor while the workstream is live, and clears both surfaces when
// it is not. Each case changes the snapshot's words, so the panel's repaint memory starts fresh for it.
const bootstrap = resolve(dirname(fileURLToPath(import.meta.url)), "../hosts/pi/bootstrap.ts");
const panel = await import(bootstrap);

function recording() {
  const calls = [];
  const ctx = {
    hasUI: true,
    ui: {
      setWidget: (key, content, options) => calls.push({ call: "widget", key, content, options }),
      setStatus: (key, text) => calls.push({ call: "status", key, text }),
    },
  };
  return { calls, ctx };
}

const snapshot = (rows, extra = {}) => ({
  active: true, workstream: "demo", phase: "dispatch", draft: false, strip: "demo · dispatch · 1 running", rows, ...extra,
});

const widgets = (calls) => calls.filter((entry) => entry.call === "widget");
const statuses = (calls) => calls.filter((entry) => entry.call === "status");
// Pi takes a factory that builds the component for the active terminal and theme. The stub theme marks each tone so a
// case can assert it; the words are compared with the marks stripped.
const THEME = {
  fg: (token, text) => `<${token}>${text}</${token}>`,
  style: (text, options) => `<${options.fg}${options.bold ? '+bold' : ''}>${text}</${options.fg}>`,
};
const plain = (line) => line.replace(/<\/?[^>]+>/g, "");
// The cells a line occupies, as the panel counts them: zero-width marks take none, wide characters take two.
const cells = (line) =>
  [...line].reduce((total, character) => {
    if (/[\uFE0E\uFE0F\u200B-\u200F\u2060]/.test(character)) return total;
    return total + (/[\u{1F000}-\u{1FAFF}\u{20000}-\u{2FFFD}]/u.test(character) ? 2 : 1);
  }, 0);
const rendered = (call, width) => call.content({}, THEME).render(width).map(plain);

// Rows draw below the editor, in the snapshot's order, through the factory form that re-renders on a resize.
{
  const rows = [{ kind: "headline", text: "demo · dispatch" }, { kind: "run", text: "▶ builder · 02 · sonnet · quiet 1m" }];
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot(rows));
  const [widget] = widgets(calls);
  assert.equal(widget.key, "zloop");
  assert.equal(widget.options.placement, "belowEditor");
  assert.deepEqual(rendered(widget, 80), rows.map((row) => row.text));
  // The card carries every fact; the status line stays with the engine while the card is drawn.
  assert.deepEqual(statuses(calls), []);
}

// An inactive snapshot leaves no card and no strip behind.
{
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot([], { active: false, rows: [], strip: null, workstream: "idle" }));
  assert.deepEqual(widgets(calls).map((widget) => widget.content), [undefined]);
  assert.deepEqual(statuses(calls).map((status) => status.text), [undefined]);
}

// The same words on the next tick draw nothing new.
{
  const rows = [{ kind: "headline", text: "demo · dispatch · unchanged" }];
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot(rows));
  const first = widgets(calls).length;
  await panel.panelTick(ctx, snapshot(rows));
  assert.equal(widgets(calls).length, first);
}

// Lines cut to the terminal end in an ellipsis and never exceed the width.
{
  const rows = [{ kind: "run", text: `▶ builder · zloop-panel/02 · sonnet · quiet 1m · ${"x".repeat(120)}` }];
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot(rows));
  const [widget] = widgets(calls);
  const lines = rendered(widget, 40);
  assert.equal(lines.length, 1);
  assert.ok(lines[0].length <= 40, lines[0]);
  assert.ok(lines[0].endsWith("…"), lines[0]);
}

// A snapshot that could not be read draws nothing and raises nothing.
{
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, null);
  assert.deepEqual(widgets(calls).map((widget) => widget.content), [undefined]);
  assert.deepEqual(statuses(calls).map((status) => status.text), [undefined]);
  const broken = recording();
  await panel.panelTick(broken.ctx, { active: true, rows: [{ kind: "run" }] });
  assert.deepEqual(widgets(broken.calls).map((widget) => widget.content), [undefined]);
}

// A stale context cannot break the tick: the panel drops its surfaces instead of raising.
{
  const throws = { hasUI: true, ui: { setWidget: () => { throw new Error("stale ctx"); }, setStatus: () => { throw new Error("stale ctx"); } } };
  await panel.panelTick(throws, snapshot([{ kind: "headline", text: "demo · stale" }]));
}

// Wide characters the terminal draws in two cells — including emoji outside the CJK blocks — keep the line inside the
// width. Cell counts here come from this harness's own table, not from the renderer's.
{
  const WIDE_CASES = new Set(['✅', '⏳', '界']);
  const cells = (line) => [...line].reduce((total, character) => total + (WIDE_CASES.has(character) ? 2 : 1), 0);
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot([{ kind: "run", text: "⏳⏳⏳⏳⏳ ✅ done" }], { workstream: "wide" }));
  const [widget] = widgets(calls);
  const [line] = rendered(widget, 8);
  assert.ok(cells(line) <= 8, `${cells(line)} cells: ${line}`);
  assert.ok(line.endsWith("…"), line);
  // A line that fits is drawn unchanged, so an over-counting table cannot pass.
  const fits = recording();
  await panel.panelTick(fits.ctx, snapshot([{ kind: "run", text: "abc ✅" }], { workstream: "fits" }));
  assert.equal(rendered(widgets(fits.calls)[0], 8)[0], "abc ✅");
  // An astral wide character (🚀) takes two cells.
  const astral = recording();
  await panel.panelTick(astral.ctx, snapshot([{ kind: "run", text: "🚀🚀🚀🚀" }], { workstream: "astral" }));
  assert.equal(rendered(widgets(astral.calls)[0], 8)[0], "🚀🚀🚀🚀");
  assert.ok(rendered(widgets(astral.calls)[0], 7)[0].endsWith("…"));
}

// Rows carry their kind's tone from the active theme, and the words stay the same.
{
  const rows = [
    { kind: "headline", text: "demo · dispatch" },
    { kind: "run", text: "▶ builder · demo/02 · sonnet · quiet 1m" },
    { kind: "wait", text: "result ready · demo/01 · quiet 2m" },
    { kind: "blocker", text: "blocked: demo/04 · no credential" },
    { kind: "next", text: "next: demo/06" },
    { kind: "counts", text: "1 done · 1 blocked · 1 ready · 1 live" },
    { kind: "more", text: "+2 more" },
  ];
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot(rows, { workstream: "toned" }));
  const [widget] = widgets(calls);
  assert.deepEqual(widget.content({}, THEME).render(80), [
    "<accent+bold>demo · dispatch</accent>",
    "▶ builder · demo/02 · sonnet · quiet 1m",
    "<warning>result ready · demo/01 · quiet 2m</warning>",
    "<error>blocked: demo/04 · no credential</error>",
    "<muted>next: demo/06</muted>",
    "<dim>1 done · 1 blocked · 1 ready · 1 live</dim>",
    "<dim>+2 more</dim>",
  ]);
}

// A toned row is cut before it is styled, so the tone mark wraps exactly the text that remains.
{
  const rows = [{ kind: "wait", text: "result ready · demo/01 · quiet 2m" }];
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot(rows, { workstream: "narrow" }));
  const [widget] = widgets(calls);
  const [raw] = widget.content({}, THEME).render(20);
  const words = raw.replace(/<\/?[^>]+>/g, "");
  assert.equal(raw, `<warning>${words}</warning>`, "the mark wraps the cut text, not part of it");
  assert.ok(words.endsWith("…"), `expected an ellipsis, got ${JSON.stringify(words)}`);
  assert.ok(words.length < rows[0].text.length, "the row was actually cut");
  assert.ok(cells(words) <= 20, `expected at most 20 cells, got ${cells(words)}`);
}

// The extension under a stub Pi, its panel timer captured rather than left running: each tick reads the real fact
// source over a fixture workstream this session runs, whose card the source reports as the headline "demo · dispatch".
const fixture = mkdtempSync(join(tmpdir(), "pi-panel-"));
process.on("exit", () => rmSync(fixture, { recursive: true, force: true }));
mkdirSync(join(fixture, ".scratch/demo"), { recursive: true });
writeFileSync(join(fixture, ".scratch/demo/autopilot.md"), ["# demo — autopilot run card", "", "## State",
  'confirmed: yes 2026-10-27 "go"', "session: S", "phase: dispatch", "terminal: ", ""].join("\n"));
// Each read of the fact source leaves a mark: the python3 first on PATH logs a panel_state.py call, then runs the real one.
const shims = join(fixture, "shims");
mkdirSync(shims);
writeFileSync(join(shims, "python3"), `#!/bin/sh\ncase "$*" in *panel_state.py*) echo read >> "${join(fixture, "reads")}";; esac\n`
  + 'PATH="${PATH#*:}" exec python3 "$@"\n', { mode: 0o755 });
process.env.PATH = `${shims}:${process.env.PATH}`;
const reads = () => (existsSync(join(fixture, "reads")) ? readFileSync(join(fixture, "reads"), "utf8").split("\n").length - 1 : 0);

function started(repo = fixture) {
  const starts = [];
  const commands = new Map();
  const shortcuts = new Map();
  panel.default({
    on: (name, handler) => name === "session_start" && starts.push(handler),
    registerCommand: (name, options) => commands.set(name, options),
    registerShortcut: (key, options) => shortcuts.set(key, options),
    registerTool() {},
    sendUserMessage() {},
  });
  const { calls, ctx } = recording();
  Object.assign(ctx, { cwd: repo, sessionManager: { getSessionId: () => "S" } });
  ctx.ui.notify = (message) => calls.push({ call: "notify", message });
  const ticks = [];
  const realInterval = globalThis.setInterval;
  globalThis.setInterval = (tick) => ticks.push(tick) && { unref() {} };
  try {
    for (const start of starts) start({}, ctx);
  } finally {
    globalThis.setInterval = realInterval;
  }
  return {
    calls,
    tick: async () => { for (const tick of ticks) await tick(); },
    command: () => commands.get("zloop-panel").handler("", ctx),
    shortcut: () => shortcuts.get("ctrl+alt+w").handler(ctx),
    // A fullscreen click on line `y` of the card last drawn, as Pi routes it to the widget's component.
    click: (y) => widgets(calls).at(-1).content({ requestRender() {} }, THEME)
      .handleMouse?.({ type: "click", button: "left", x: 2, y, screenX: 2, screenY: 40 + y, width: 80, height: 9,
                       shift: false, alt: false, ctrl: false, clickCount: 1 }),
  };
}
// The rows the card shows now: the last widget drawn, or none once it was cleared.
const shown = (calls) => {
  const last = widgets(calls).at(-1);
  return last?.content ? rendered(last, 80) : [];
};
const notices = (calls) => calls.filter((entry) => entry.call === "notify").map((entry) => entry.message);

// The panel starts open: a live workstream's rows draw with no command. /zloop-panel closes it at once and says so, and
// later ticks neither read nor draw. Run again, it opens the panel, says so, and the next tick draws the rows.
{
  const session = started();
  const before = reads();
  await session.tick();
  assert.deepEqual(shown(session.calls), ["demo · dispatch"]);
  assert.equal(reads(), before + 1);
  await session.command();
  assert.deepEqual(shown(session.calls), []);
  assert.deepEqual(notices(session.calls), ["zloop panel closed."]);
  await session.tick();
  assert.deepEqual(shown(session.calls), []);
  assert.equal(reads(), before + 1, "a closed panel reads nothing");
  await session.command();
  assert.deepEqual(notices(session.calls), ["zloop panel closed.", "zloop panel opened."]);
  await session.tick();
  assert.deepEqual(shown(session.calls), ["demo · dispatch"]);
}

// A read already under way when the panel closes draws nothing once it lands.
{
  const session = started();
  const reading = session.tick();
  await session.command();
  await reading;
  assert.deepEqual(shown(session.calls), []);
}

// ZLOOP_PANEL=off starts the panel closed; /zloop-panel opens it.
{
  const before = process.env.ZLOOP_PANEL;
  process.env.ZLOOP_PANEL = "off";
  let session;
  try {
    session = started();
  } finally {
    if (before === undefined) delete process.env.ZLOOP_PANEL;
    else process.env.ZLOOP_PANEL = before;
  }
  await session.tick();
  assert.deepEqual(shown(session.calls), []);
  await session.command();
  assert.deepEqual(notices(session.calls), ["zloop panel opened."]);
  await session.tick();
  assert.deepEqual(shown(session.calls), ["demo · dispatch"]);
}

// A second fixture: five live workers this session registered, so the card shows three and `+2 more`.
const many = mkdtempSync(join(tmpdir(), "pi-panel-many-"));
process.on("exit", () => rmSync(many, { recursive: true, force: true }));
mkdirSync(join(many, ".scratch/many/reports"), { recursive: true });
execFileSync("python3", ["-c", `
import json, sqlite3, sys, time
db = sqlite3.connect(sys.argv[1])
db.execute('CREATE TABLE attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, attempt TEXT NOT NULL, kind TEXT NOT NULL, '
           'payload TEXT NOT NULL, acknowledged INTEGER NOT NULL)')
for n in range(1, 6):
    attempt = f'{n}/audit-1'
    data = {'attempt': attempt, 'assignment': f'many/0{n}', 'agent': 'reviewer', 'job': 'audit',
            'requested': {'model': 'sonnet'}, 'session': 'S', 'sessions': ['S']}
    db.execute('INSERT INTO attempts VALUES (?, ?)', (attempt, json.dumps(data)))
    db.execute('INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 1)',
               (attempt, 'register', json.dumps({'recorded_at': time.time() - 60 * n - 5})))
db.commit()
`, join(many, ".scratch/many/reports/workers.sqlite")]);
const ROW = (n) => `▶ reviewer · many/0${n} · sonnet · quiet ${n}m`;
const COLLAPSED = ["many", ROW(1), ROW(2), ROW(3), "+2 more · ctrl+alt+w", "done: audit 0"];
const EXPANDED = ["many", ROW(1), ROW(2), ROW(3), ROW(4), ROW(5), "show fewer · ctrl+alt+w", "done: audit 0"];

// The `+N more` line names its key; Ctrl+Alt+W shows the rows it stands for in its place, then `show fewer`, and
// pressed again hides them.
{
  const session = started(many);
  await session.tick();
  assert.deepEqual(shown(session.calls), COLLAPSED);
  await session.shortcut();
  assert.deepEqual(shown(session.calls), EXPANDED);
  await session.tick();
  assert.deepEqual(shown(session.calls), EXPANDED, "a later read keeps the choice");
  await session.shortcut();
  assert.deepEqual(shown(session.calls), COLLAPSED);
}

// In fullscreen a click on `+N more` expands and a click on `show fewer` collapses; a click on another line is left
// to Pi.
{
  const session = started(many);
  await session.tick();
  assert.equal(session.click(1), undefined);
  assert.deepEqual(shown(session.calls), COLLAPSED);
  assert.deepEqual(session.click(4), { handled: true });
  assert.deepEqual(shown(session.calls), EXPANDED);
  assert.deepEqual(session.click(6), { handled: true });
  assert.deepEqual(shown(session.calls), COLLAPSED);
}

// With nothing hidden there is no expander, whatever the key did; a closed panel draws nothing on the key.
{
  const session = started();
  await session.tick();
  await session.shortcut();
  assert.deepEqual(shown(session.calls), ["demo · dispatch"]);
  const closed = started(many);
  await closed.tick();
  await closed.command();
  await closed.shortcut();
  assert.deepEqual(shown(closed.calls), []);
}

console.log("pi panel: 15 cases passed");
