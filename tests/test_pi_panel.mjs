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
// Pi takes a factory that builds the component for the active terminal and theme.
const rendered = (call, width) => call.content({}, {}).render(width);

// Rows draw below the editor, in the snapshot's order, through the factory form that re-renders on a resize.
{
  const rows = [{ kind: "headline", text: "demo · dispatch" }, { kind: "run", text: "▶ builder · 02 · sonnet · quiet 1m" }];
  const { calls, ctx } = recording();
  await panel.panelTick(ctx, snapshot(rows));
  const [widget] = widgets(calls);
  assert.equal(widget.key, "zloop");
  assert.equal(widget.options.placement, "belowEditor");
  assert.deepEqual(rendered(widget, 80), rows.map((row) => row.text));
  assert.deepEqual(statuses(calls).map((status) => status.text), ["demo · dispatch · 1 running"]);
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

console.log("pi panel: 6 cases passed");
