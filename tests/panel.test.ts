import { describe, expect, mock, test } from "claude-code/testing";

// The band draws the fact source's rows while the effort is live, and leaves the engine's own band alone otherwise.
const snapshot = (extra: Record<string, unknown> = {}) => JSON.stringify({
  active: true,
  effort: "demo",
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

// What the engine answers beneath the session: a started session, a clock, and the fact source's output.
const beneath = (on: any, stdout?: string, pinned?: (string | undefined)[]) => {
  on("session.start", () => ({ cwd: "/work" }));
  on("session.id", () => ({ value: "session-1" }));
  on("session.cwd", () => ({ value: "/work" }));
  on("process.run", () => ({ value: { exitCode: stdout ? 0 : 2, stdout: stdout ?? "", stderr: "boom" } }));
  on("ui.status", (_$: any, e: any) => {
    pinned?.push(e.text);
    return { value: undefined };
  });
  // The engine's own drawing of the band, above which this plugin's hook runs.
  on("ui.render", { component: "AbovePrompt" }, () => ({ type: "Text", props: {}, children: ["engine band"] }));
  mock.clock(on);
};

const started = async ($: any) => $.session.start({ surface: "terminal", isInteractive: true, cwd: "/work" });
// The band's own props: a full-width, uncollapsed band that holds a dozen rows, as the engine hands them.
const BAND_PROPS = { hasSurvey: false, bodyColumns: 80, maxRows: 12 };
const band = ($: any, props: Record<string, unknown> = {}) =>
  $.ui.mount({ plugin: "zloop", surface: "terminal", component: "AbovePrompt", props: { ...BAND_PROPS, ...props } });

describe("zloop panel", () => {
  test("renders nothing before a snapshot is read", async ($, on) => {
    beneath(on);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
    expect(await ui.find({ type: "Text", text: /engine band/ })).toBeDefined();
  });

  test("draws the snapshot rows in the band", async ($, on) => {
    beneath(on, snapshot());
    await started($);
    const ui = await band($);
    const texts = (await ui.findAll({ type: "Text" })).map((node: any) => node.text);
    // Every row draws whole at eighty columns, in the snapshot's order.
    expect(texts).toEqual([
      "demo · dispatch",
      "▶ builder · zloop-panel/02 · sonnet · quiet 1m",
      "1 done · 0 blocked · 2 ready · 1 live",
    ]);
  });

  test("renders nothing when the effort is not live, even with rows still in the snapshot", async ($, on) => {
    beneath(on, snapshot({ active: false, strip: null }));
    await started($);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
    expect(await ui.find({ type: "Text", text: /2 ready/ })).toBeUndefined();
    expect(await ui.find({ type: "Text", text: /engine band/ })).toBeDefined();
  });

  test("renders nothing when the fact source fails", async ($, on) => {
    beneath(on);
    await started($);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
    expect(await ui.find({ type: "Text", text: /engine band/ })).toBeDefined();
  });

  test("renders nothing when the fact source prints no JSON", async ($, on) => {
    on("session.start", () => ({ cwd: "/work" }));
    on("session.id", () => ({ value: "session-1" }));
    on("session.cwd", () => ({ value: "/work" }));
    on("process.run", () => ({ value: { exitCode: 0, stdout: "not json", stderr: "" } }));
    on("ui.render", { component: "AbovePrompt" }, () => ({ type: "Text", props: {}, children: ["engine band"] }));
    mock.clock(on);
    await started($);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /not json/ })).toBeUndefined();
    expect(await ui.find({ type: "Text", text: /engine band/ })).toBeDefined();
  });
  test("keeps its status line quiet while the band draws", async ($, on) => {
    const pinned: (string | undefined)[] = [];
    beneath(on, snapshot(), pinned);
    await started($);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeDefined();
    expect(pinned).toContain(undefined);
    expect(pinned).not.toContain("demo · dispatch · 1 running");
  });

  test("pins the strip when a survey holds the band", async ($, on) => {
    const pinned: (string | undefined)[] = [];
    beneath(on, snapshot(), pinned);
    await started($);
    const ui = await band($, { hasSurvey: true, bodyColumns: 80, maxRows: 10 });
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
    expect(await ui.find({ type: "Text", text: /engine band/ })).toBeDefined();
    expect(pinned).toContain("demo · dispatch · 1 running");
  });

  test("pins the strip when the rows do not fit", async ($, on) => {
    const pinned: (string | undefined)[] = [];
    beneath(on, snapshot(), pinned);
    await started($);
    const ui = await band($, { hasSurvey: false, bodyColumns: 80, maxRows: 1 });
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
    expect(pinned).toContain("demo · dispatch · 1 running");
  });
  test("cuts rows to the band's columns, wide characters included", async ($, on) => {
    // A table that over-counts every letter would pass a "<= width" check, so the fitting and astral cases are exact.
    const fitted = ["abc ✅", "🚀🚀🚀🚀"];
    beneath(on, snapshot({ rows: fitted.map((text) => ({ kind: "run", text })) }));
    await started($);
    const wide = await band($, { bodyColumns: 8, maxRows: 12 });
    expect((await wide.findAll({ type: "Text" })).map((node: any) => node.text)).toEqual(fitted);

    const overflow = await band($, { bodyColumns: 5, maxRows: 12 });
    const cut = (await overflow.findAll({ type: "Text" })).map((node: any) => node.text);
    expect(cut[0].endsWith("…")).toBe(true);
    expect(cut[1].endsWith("…")).toBe(true);
    const cells = [...cut[1]].reduce((total, character) => total + (character === "🚀" ? 2 : 1), 0);
    expect(cells).toBeLessThanOrEqual(5);
  });
});
