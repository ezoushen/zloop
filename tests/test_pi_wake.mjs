import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// The zloop_wake tool is how a Pi run schedules its own resume: a timer that types a command back into the session.
const bootstrap = resolve(dirname(fileURLToPath(import.meta.url)), "../hosts/pi/bootstrap.ts");
const COMMAND = "/skill:autopilot --take-over";
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

const load = async () => {
  const sent = [];
  const tools = new Map();
  const pi = { on() {}, registerCommand() {}, registerTool: (tool) => tools.set(tool.name, tool), sendUserMessage: (...args) => sent.push(args) };
  (await import(`${bootstrap}?${Math.random()}`)).default(pi);
  return { sent, pi, tool: tools.get("zloop_wake") };
};
const text = (result) => result.content.map((part) => part.text).join("");
const inMs = (ms) => new Date(Date.now() + ms).toISOString();

const { sent, tool } = await load();
assert.ok(tool, "the extension registers zloop_wake");

// At the time given, the command is typed back into the session, expanded as a skill command, queued when busy.
const scheduled = await tool.execute("call-1", { at: inMs(500), command: COMMAND });
assert.match(text(scheduled), /timer \S+/);
await sleep(100);
assert.equal(sent.length, 0, "nothing is sent before its time");
await sleep(800);
assert.deepEqual(sent, [[COMMAND, { deliverAs: "followUp", expandPromptTemplates: true }]]);

// A cancelled timer never fires, and cancelling says which timer it dropped.
const second = await tool.execute("call-2", { at: inMs(80), command: COMMAND });
const id = text(second).match(/timer (\S+)/)[1];
assert.match(text(await tool.execute("call-3", { cancel: id })), new RegExp(`cancelled timer ${id}`));
await sleep(250);
assert.equal(sent.length, 1);
await assert.rejects(tool.execute("call-4", { cancel: id }), /no timer/);

// A time that cannot be honoured is refused, not clamped: past, unparsable, beyond setTimeout's range, no command.
await assert.rejects(tool.execute("call-5", { at: inMs(-1000), command: COMMAND }), /past/);
await assert.rejects(tool.execute("call-6", { at: "tomorrow-ish", command: COMMAND }), /ISO 8601/);
await assert.rejects(tool.execute("call-7", { at: inMs(30 * 24 * 3600 * 1000), command: COMMAND }), /24 days/);
await assert.rejects(tool.execute("call-8", { at: inMs(1000), command: "  " }), /command/);

// A timer that fires after Pi replaced this extension (/reload, a new session) finds its API invalidated and throws;
// that must not become an uncaught exception that takes the process down.
const stale = await load();
stale.pi.sendUserMessage = () => { throw new Error("SDK stale extension after /reload"); };
const crashes = [];
const record = (error) => crashes.push(error);
process.on("uncaughtException", record);
await stale.tool.execute("call-9", { at: inMs(40), command: COMMAND });
await sleep(150);
process.off("uncaughtException", record);
assert.deepEqual(crashes.map(String), []);

// A pending timer never holds the process open: a headless run that ends exits at once.
const child = spawnSync(process.execPath, ["--input-type=module", "-e", `
  const tools = new Map();
  (await import(${JSON.stringify(`file://${bootstrap}`)})).default({ on() {}, registerCommand() {}, registerTool: (t) => tools.set(t.name, t) });
  await tools.get("zloop_wake").execute("x", { at: new Date(Date.now() + 3600e3).toISOString(), command: ${JSON.stringify(COMMAND)} });
`], { timeout: 10000 });
assert.equal(child.status, 0, String(child.stderr));
console.log("Pi zloop_wake: schedules, cancels, refuses bad times, never holds the process: ok");
