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
const beneath = (on: any, stdout?: string) => {
  on("session.start", () => ({ cwd: "/work" }));
  on("session.id", () => ({ value: "session-1" }));
  on("session.cwd", () => ({ value: "/work" }));
  on("process.run", () => ({ value: { exitCode: stdout ? 0 : 2, stdout: stdout ?? "", stderr: "boom" } }));
  // The engine's own drawing of the band, above which this plugin's hook runs.
  on("ui.render", { component: "AbovePrompt" }, () => ({ type: "Text", props: {}, children: ["engine band"] }));
  mock.clock(on);
};

const started = async ($: any) => $.session.start({ surface: "terminal", isInteractive: true, cwd: "/work" });
const band = ($: any) => $.ui.mount({ plugin: "zloop", surface: "terminal", component: "AbovePrompt", props: {} });

describe("zloop panel", () => {
  test("renders nothing before a snapshot is read", async ($, on) => {
    beneath(on);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
  });

  test("draws the snapshot rows in the band", async ($, on) => {
    beneath(on, snapshot());
    await started($);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeDefined();
    expect(await ui.find({ type: "Text", text: /quiet 1m/ })).toBeDefined();
    expect(await ui.find({ type: "Text", text: /2 ready/ })).toBeDefined();
  });

  test("renders nothing when the effort is not live, even with rows still in the snapshot", async ($, on) => {
    beneath(on, snapshot({ active: false, strip: null }));
    await started($);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
    expect(await ui.find({ type: "Text", text: /2 ready/ })).toBeUndefined();
  });

  test("renders nothing when the fact source fails", async ($, on) => {
    beneath(on);
    await started($);
    const ui = await band($);
    expect(await ui.find({ type: "Text", text: /demo · dispatch/ })).toBeUndefined();
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
});
