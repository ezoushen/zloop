import { describe, expect, mock, test } from "claude-code/testing";

// The pane draws the fact source's rows while this session's workstream is live, opens and closes with it, and leaves
// the band above the prompt to the engine.
const PANE = "zloop-panel";

const snapshot = (extra: Record<string, unknown> = {}) => ({
  active: true,
  workstream: "demo",
  phase: "dispatch",
  draft: false,
  rows: [
    { kind: "headline", text: "demo · dispatch" },
    { kind: "run", text: "▶ builder · zloop-panel/02 · sonnet · quiet 1m" },
    { kind: "counts", text: "1 done · 0 blocked · 2 ready · 1 live" },
  ],
  strip: "demo · dispatch · 1 running",
  ...extra,
});
const QUIET = { active: false, workstream: null, phase: null, draft: false, rows: [], strip: null };

// What the engine answers beneath the session: a clock, the fact source's output (changed by `world.facts`), the
// panes it keeps open and the status line pinned.
const beneath = (on: any, facts?: Record<string, unknown> | string, stored: Record<string, unknown> = {}) => {
  const world = {
    facts,
    panes: [] as { id: string; title: string; isPlaced: boolean }[],
    opened: [] as string[],
    pinned: [] as (string | undefined)[],
    isPlaced: true,
    // Set, the first look at the panes after an open waits here until the test releases it.
    hold: undefined as undefined | { reached: () => void; released: Promise<void> },
  };
  on("session.start", () => ({ cwd: "/work" }));
  on("session.id", () => ({ value: "session-1" }));
  on("session.cwd", () => ({ value: "/work" }));
  on("command.register", (_$: any, e: any) => ({ value: { command: e.name } }));
  on("process.run", () => {
    const stdout = typeof world.facts === "string" ? world.facts : world.facts ? JSON.stringify(world.facts) : "";
    return { value: { exitCode: world.facts ? 0 : 2, stdout, stderr: "boom",
                      isStdoutTruncated: false, isStderrTruncated: false } };
  });
  on("ui.open", (_$: any, e: any) => {
    world.opened.push(e.id);
    world.panes = world.panes.filter((pane) => pane.id !== e.id);
    world.panes.push({ id: e.id, title: e.title ?? e.id, isPlaced: world.isPlaced });
    return world.isPlaced ? { value: { isPlaced: true } } : { value: { isPlaced: false, reason: "below 144 columns" } };
  });
  on("ui.close", (_$: any, e: any) => {
    world.panes = world.panes.filter((pane) => pane.id !== e.id);
    return { value: undefined };
  });
  on("ui.panes", async () => {
    const value = world.panes.map((pane) => ({ ...pane, isShown: pane.isPlaced, isFocused: false }));
    const hold = world.hold;
    if (hold && world.opened.length) {
      world.hold = undefined;
      hold.reached();
      await hold.released;
    }
    return { value };
  });
  on("ui.status", (_$: any, e: any) => {
    world.pinned.push(e.text);
    return { value: undefined };
  });
  // What an earlier load of the plugin left in the session's state, read until the plugin writes that key again; the
  // kit's own store answers the rest.
  on("state.get", (_$: any, e: any, next: any) =>
    e.plugin === "zloop" && e.key in stored ? { value: { value: stored[e.key], version: 0 } } : next(e));
  on("state.set", (_$: any, e: any, next: any) => {
    if (e.plugin === "zloop") delete stored[e.key];
    return next(e);
  });
  // The engine's own drawings, beneath this plugin's hooks.
  on("ui.render", { component: "AbovePrompt" }, () => ({ type: "Text", props: {}, children: ["engine band"] }));
  const clock = mock.clock(on);
  return { world, clock };
};

const started = async ($: any) => $.session.start({ surface: "terminal", isInteractive: true, cwd: "/work" });
// The pane's own props as the engine hands them: docked beside the transcript, eighty cells across.
const PANE_PROPS = { title: "zloop", isFocused: false, bodyColumns: 80, placement: "dock",
                     scroll: { offset: 0, bodyRows: 20 }, view: {} };
const pane = ($: any, props: Record<string, unknown> = {}) =>
  $.ui.mount({ plugin: "zloop", surface: "terminal", component: "Pane", requestId: PANE,
               props: { ...PANE_PROPS, ...props } });
const texts = async (ui: any) => (await ui.findAll({ type: "Text" })).map((node: any) => node.text);
// Every line the pane shows, rows and buttons alike, in document order.
const lines = async (ui: any) =>
  (await ui.findAll({})).filter((node: any) => ["Text", "Button"].includes(node.type)).map((node: any) => node.text);
// Five live workers: three drawn, two behind `+2 more`, which the snapshot carries as `more`.
const run = (n: number) => ({ kind: "run", text: `▶ builder · zloop-panel/0${n} · sonnet · quiet ${n}m` });
const capped = () => snapshot({
  rows: [{ kind: "headline", text: "demo · dispatch" }, run(1), run(2), run(3), { kind: "more", text: "+2 more" },
         { kind: "counts", text: "done: ticket 0" }],
  more: [run(4), run(5)],
});
const COLLAPSED = ["demo · dispatch", run(1).text, run(2).text, run(3).text, "+2 more", "done: ticket 0"];
const EXPANDED = ["demo · dispatch", run(1).text, run(2).text, run(3).text, run(4).text, run(5).text, "show fewer",
                  "done: ticket 0"];

describe("zloop panel", () => {
  test("opens no pane while nothing is live, and opens it once this session's workstream goes live", async ($, on) => {
    const { world, clock } = beneath(on, QUIET);
    await started($);
    await clock.advance(2000);
    expect(world.opened).toEqual([]);
    world.facts = snapshot();
    await clock.advance(2000);
    expect(world.panes.map((one) => one.id)).toEqual([PANE]);
  });

  test("closes the pane it opened itself once the workstream goes quiet", async ($, on) => {
    const { world, clock } = beneath(on, snapshot());
    await started($);
    expect(world.panes.map((one) => one.id)).toEqual([PANE]);
    world.facts = QUIET;
    await clock.advance(2000);
    expect(world.panes).toEqual([]);
  });

  test("a pane the person closed stays closed for that workstream, and opens again for the next one", async ($, on) => {
    const { world, clock } = beneath(on, snapshot());
    await started($);
    // The person's close mark: the engine drops the pane.
    world.panes = [];
    await clock.advance(4000);
    expect(world.opened).toEqual([PANE]);
    world.facts = snapshot({ workstream: "other" });
    await clock.advance(2000);
    expect(world.opened).toEqual([PANE, PANE]);
  });

  test("a pane the person closed stays closed for the workstream it showed when closed", async ($, on) => {
    const { world, clock } = beneath(on, snapshot());
    await started($);
    // The pane opened for demo now shows another workstream, which the person closes.
    world.facts = snapshot({ workstream: "other" });
    await clock.advance(2000);
    world.panes = [];
    await clock.advance(4000);
    expect(world.opened).toEqual([PANE]);
  });

  test("a pane opened by /zloop-panel and closed by the person stays closed for the live workstream", async ($, on) => {
    const { world, clock } = beneath(on, QUIET);
    await started($);
    await $.command.run({ command: "zloop-panel" });
    world.facts = snapshot();
    await clock.advance(2000);
    world.panes = [];
    await clock.advance(4000);
    expect(world.opened).toEqual([PANE]);
  });

  test("/zloop-panel seats a pane that waits undrawn instead of closing it", async ($, on) => {
    const { world } = beneath(on, snapshot());
    world.isPlaced = false;
    await started($);
    // The person's own open seats the pane at any width.
    world.isPlaced = true;
    const ran = await $.command.run({ command: "zloop-panel" });
    expect(ran.text).toBe("zloop panel opened.");
    expect(world.panes.map((one) => [one.id, one.isPlaced])).toEqual([[PANE, true]]);
  });

  test("a pane /zloop-panel seats while the panel is opening it stays open once the workstream goes quiet", async ($, on) => {
    const { world, clock } = beneath(on, QUIET);
    world.isPlaced = false;
    await started($);
    world.facts = snapshot();
    // The refresh opens the pane unasked, then waits on its next look while the person runs /zloop-panel.
    let reached!: () => void;
    let release!: () => void;
    const isReached = new Promise<void>((resolve) => { reached = resolve; });
    const released = new Promise<void>((resolve) => { release = resolve; });
    world.hold = { reached, released };
    const ticked = clock.advance(2000);
    await isReached;
    world.isPlaced = true;
    const ran = await $.command.run({ command: "zloop-panel" });
    expect(ran.text).toBe("zloop panel opened.");
    release();
    await ticked;
    world.facts = QUIET;
    await clock.advance(2000);
    expect(world.panes.map((one) => one.id)).toEqual([PANE]);
  });

  test("/zloop-panel opens the pane while nothing is live, and it stays open while quiet", async ($, on) => {
    const { world, clock } = beneath(on, QUIET);
    await started($);
    const ran = await $.command.run({ command: "zloop-panel" });
    expect(ran.text).toBe("zloop panel opened.");
    await clock.advance(4000);
    expect(world.panes.map((one) => one.id)).toEqual([PANE]);
  });

  test("/zloop-panel closes an open pane, and it stays closed for that workstream", async ($, on) => {
    const { world, clock } = beneath(on, snapshot());
    await started($);
    const ran = await $.command.run({ command: "zloop-panel" });
    expect(ran.text).toBe("zloop panel closed.");
    await clock.advance(4000);
    expect(world.panes).toEqual([]);
    expect(world.opened).toEqual([PANE]);
  });

  test("pins the strip in the status line while the pane waits undrawn, and clears it once it draws", async ($, on) => {
    const { world, clock } = beneath(on, snapshot());
    // An unasked pane on a terminal narrower than its floor waits undrawn.
    world.isPlaced = false;
    await started($);
    expect(world.pinned.at(-1)).toBe("demo · dispatch · 1 running");
    // The person widens the terminal and the engine seats the pane.
    world.panes = world.panes.map((one) => ({ ...one, isPlaced: true }));
    await clock.advance(2000);
    expect(world.pinned.at(-1)).toBeUndefined();
  });

  test("keeps the status line clear while the pane draws", async ($, on) => {
    const { world } = beneath(on, snapshot());
    await started($);
    expect(world.pinned).not.toContain("demo · dispatch · 1 running");
  });

  test("draws the snapshot rows in the pane, in the snapshot's order", async ($, on) => {
    beneath(on, snapshot());
    await started($);
    expect(await texts(await pane($))).toEqual([
      "demo · dispatch",
      "▶ builder · zloop-panel/02 · sonnet · quiet 1m",
      "1 done · 0 blocked · 2 ready · 1 live",
    ]);
  });

  test("says so in the pane when this session has no live workstream", async ($, on) => {
    beneath(on, snapshot({ active: false, strip: null }));
    await started($);
    // Rows still in a quiet snapshot are not drawn.
    expect(await texts(await pane($))).toEqual(["No zloop workstream is live in this session."]);
  });

  test("says so in the pane when the fact source fails or prints no JSON", async ($, on) => {
    const { world, clock } = beneath(on);
    await started($);
    const ui = await pane($);
    expect(await texts(ui)).toEqual(["No zloop workstream is live in this session."]);
    await ui.unmount();
    world.facts = "not json";
    await clock.advance(2000);
    expect(await texts(await pane($))).toEqual(["No zloop workstream is live in this session."]);
  });

  // A hot reload of the plugin starts its module over and runs session.start again; the session's state stays.
  test("a pane the person closed stays closed for that workstream after a reload", async ($, on) => {
    const { world } = beneath(on, snapshot(), {
      pane: { shape: "pane-1", value: { wasOpen: false, isUnasked: false, closedFor: "demo" } },
    });
    await started($);
    expect(world.opened).toEqual([]);
  });

  test("a pane the panel opened itself still closes once the workstream goes quiet after a reload", async ($, on) => {
    const { world } = beneath(on, QUIET, {
      pane: { shape: "pane-1", value: { wasOpen: true, isUnasked: true, closedFor: null } },
    });
    // The engine keeps the pane open across the reload.
    world.panes = [{ id: PANE, title: "zloop", isPlaced: true }];
    await started($);
    expect(world.panes).toEqual([]);
  });

  test("after a reload the pane draws the rows last read until the next read lands", async ($, on) => {
    beneath(on, undefined, { snapshot: { shape: "snapshot-2", value: snapshot() } });
    // Drawn before the reloaded module's session.start has read the fact source again.
    expect(await texts(await pane($))).toEqual([
      "demo · dispatch",
      "▶ builder · zloop-panel/02 · sonnet · quiet 1m",
      "1 done · 0 blocked · 2 ready · 1 live",
    ]);
  });

  test("a snapshot stored under the shape before `workstream` is declined, not drawn", async ($, on) => {
    const earlier = { ...snapshot(), workstream: undefined, effort: "demo" };
    beneath(on, undefined, { snapshot: { shape: "snapshot-1", value: earlier } });
    expect(await texts(await pane($))).toEqual(["No zloop workstream is live in this session."]);
  });

  test("leaves the band above the prompt to the engine", async ($, on) => {
    beneath(on, snapshot());
    await started($);
    const band = await $.ui.mount({ plugin: "zloop", surface: "terminal", component: "AbovePrompt",
                                    props: { hasSurvey: false, isWorking: false, bodyColumns: 80, maxRows: 12,
                                             scroll: { offset: 0, bodyRows: 11 }, view: {} } });
    expect(await texts(band)).toEqual(["engine band"]);
  });

  test("cuts rows to the pane's columns, wide characters included", async ($, on) => {
    // A table that over-counts every letter would pass a "<= width" check, so the fitting and astral cases are exact.
    const fitted = ["abc ✅", "🚀🚀🚀🚀"];
    beneath(on, snapshot({ rows: fitted.map((text) => ({ kind: "run", text })) }));
    await started($);
    const wide = await pane($, { bodyColumns: 8 });
    expect(await texts(wide)).toEqual(fitted);
    await wide.unmount();

    const cut = await texts(await pane($, { bodyColumns: 5 }));
    expect(cut[0].endsWith("…")).toBe(true);
    expect(cut[1].endsWith("…")).toBe(true);
    const cells = [...cut[1]].reduce((total, character) => total + (character === "🚀" ? 2 : 1), 0);
    expect(cells).toBeLessThanOrEqual(5);
  });

  test("gives every row its kind's emphasis", async ($, on) => {
    const rows = [
      { kind: "headline", text: "demo · dispatch" },
      { kind: "run", text: "▶ builder · demo/02 · sonnet · quiet 1m" },
      { kind: "wait", text: "result ready · demo/01 · quiet 2m" },
      { kind: "blocker", text: "blocked: demo/04 · no credential" },
      { kind: "next", text: "next: demo/06" },
      { kind: "counts", text: "1 done · 1 blocked · 1 ready · 1 live" },
      { kind: "more", text: "+2 more" },
    ];
    beneath(on, snapshot({ rows }));
    await started($);
    const nodes = await (await pane($)).findAll({ type: "Text" });
    // The words are unchanged; only each kind's own props differ.
    expect(nodes.map((node: any) => node.text)).toEqual(rows.map((row) => row.text));
    expect(nodes[0].props.bold).toBe(true);
    expect(nodes[1].props.color).toBeUndefined();
    expect(nodes[2].props.color).toBe("warning");
    expect(nodes[3].props.color).toBe("error");
    expect(nodes[4].props.color).toBe("subtle");
    expect(nodes[5].props.color).toBe("subtle");
    expect(nodes[6].props.color).toBe("subtle");
  });

  test("+N more is a button showing the rows it stands for in its place, and show fewer hides them", async ($, on) => {
    beneath(on, capped());
    await started($);
    const ui = await pane($);
    expect(await lines(ui)).toEqual(COLLAPSED);
    // A click, Enter under the focus, or `m` while the pane holds the focus presses it.
    expect((await ui.find({ type: "Button", key: "more" }))?.props).toMatchObject({ hotkey: "m", plain: true });
    await ui.press({ key: "more" });
    expect(await lines(ui)).toEqual(EXPANDED);
    await ui.press({ key: "more" });
    expect(await lines(ui)).toEqual(COLLAPSED);
  });

  test("the expansion outlives a reload of the plugin", async ($, on) => {
    beneath(on, capped(), { expanded: { shape: "expanded-1", value: true } });
    await started($);
    expect(await lines(await pane($))).toEqual(EXPANDED);
  });
});
