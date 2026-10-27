import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// The Pi panel draws the fact source's rows below the editor while the effort is live, and clears both surfaces when
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
  active: true, effort: "demo", phase: "dispatch", draft: false, strip: "demo · dispatch · 1 running", rows, ...extra,
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
  await panel.panelTick(ctx, snapshot([], { active: false, rows: [], strip: null, effort: "idle" }));
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
  await panel.panelTick(ctx, snapshot([{ kind: "run", text: "⏳⏳⏳⏳⏳ ✅ done" }], { effort: "wide" }));
  const [widget] = widgets(calls);
  const [line] = rendered(widget, 8);
  assert.ok(cells(line) <= 8, `${cells(line)} cells: ${line}`);
  assert.ok(line.endsWith("…"), line);
  // A line that fits is drawn unchanged, so an over-counting table cannot pass.
  const fits = recording();
  await panel.panelTick(fits.ctx, snapshot([{ kind: "run", text: "abc ✅" }], { effort: "fits" }));
  assert.equal(rendered(widgets(fits.calls)[0], 8)[0], "abc ✅");
  // An astral wide character (🚀) takes two cells.
  const astral = recording();
  await panel.panelTick(astral.ctx, snapshot([{ kind: "run", text: "🚀🚀🚀🚀" }], { effort: "astral" }));
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
  await panel.panelTick(ctx, snapshot(rows, { effort: "toned" }));
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
  await panel.panelTick(ctx, snapshot(rows, { effort: "narrow" }));
  const [widget] = widgets(calls);
  const [raw] = widget.content({}, THEME).render(20);
  const words = raw.replace(/<\/?[^>]+>/g, "");
  assert.equal(raw, `<warning>${words}</warning>`, "the mark wraps the cut text, not part of it");
  assert.ok(words.endsWith("…"), `expected an ellipsis, got ${JSON.stringify(words)}`);
  assert.ok(words.length < rows[0].text.length, "the row was actually cut");
  assert.ok(cells(words) <= 20, `expected at most 20 cells, got ${cells(words)}`);
}

console.log("pi panel: 9 cases passed");
