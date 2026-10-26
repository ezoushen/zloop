import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// The OpenCode plugin runs the real yield-gate check and launch log against a temporary checkout; only OpenCode's
// plugin context and the herdr binary are stand-ins.
const plugin = resolve(dirname(fileURLToPath(import.meta.url)), "../hosts/opencode/plugin.js");
for (const name of ["HERDR_PANE_ID", "HERDR_WORKSPACE_ID", "HERDR_ENV", "ZLOOP_WORKER", "ZLOOP_YIELD_HOOK"]) {
  delete process.env[name];
}
const bin = mkdtempSync(join(tmpdir(), "zloop-oc-bin-"));
writeFileSync(join(bin, "herdr"), "#!/bin/sh\nexit 1\n");
chmodSync(join(bin, "herdr"), 0o755);
process.env.PATH = `${bin}:${process.env.PATH}`;

const until = async (check, what) => {
  for (const due = Date.now() + 15000; Date.now() < due; await new Promise((done) => setTimeout(done, 20))) {
    if (check()) return;
  }
  assert.fail(`timed out waiting for ${what}`);
};

// A checkout whose launch log holds a launch by each of `ses_gap`, `ses_gap2`, `ses_child` and `ses_last` that no attempt
// registered: real yield-gate gaps for those sessions and none for any other.
function checkout() {
  const repo = mkdtempSync(join(tmpdir(), "zloop-oc-repo-"));
  execFileSync("git", ["init", "-q", repo]);
  mkdirSync(join(repo, ".scratch"));
  const launch = (agent, session) => JSON.stringify({
    time: "2026-10-27T00:00:00Z", agent, pane: `p-${agent}`, coordinator: { pane: null, session },
  }) + "\n";
  writeFileSync(join(repo, ".scratch/herdr-launches.jsonl"), launch("w1", "ses_gap") + launch("w2", "ses_gap2") + launch("w3", "ses_child")
    + launch("w4", "ses_last"));
  return repo;
}
// Events are handled in order, so once a later session's hold arrives, every earlier event has been handled.
const holdsFor = (host, sessionID) => host.sent.filter((input) => input.sessionID === sessionID).length;

// A stand-in for OpenCode's plugin context: events pushed by the test, sessions with a directory and an optional parent.
async function load(repo, sessions) {
  const queue = [];
  let wake = () => {};
  const sent = [];
  const hooks = {};
  const ctx = {
    location: { directory: repo },
    event: {
      subscribe: ({ signal } = {}) => ({
        async *[Symbol.asyncIterator]() {
          while (!signal?.aborted) {
            if (queue.length) yield queue.shift();
            else await new Promise((done) => { wake = done; });
          }
        },
      }),
    },
    session: {
      get: async ({ sessionID }) => ({ id: sessionID, location: { directory: repo }, ...sessions[sessionID] }),
      prompt: async (input) => { sent.push(input); return { id: `msg_${sent.length}`, delivery: "steer" }; },
    },
    tool: { hook: async (name, callback) => { hooks[name] = callback; return { dispose: async () => {} }; } },
  };
  const { default: definition } = await import(`${plugin}?${Math.random()}`);
  const cleanup = await definition.setup(ctx);
  const push = (type, sessionID) => { queue.push({ type, data: { sessionID } }); wake(); };
  return { sent, hooks, push, cleanup };
}

const repo = checkout();
const host = await load(repo, { ses_gap: {}, ses_gap2: {}, ses_clean: {}, ses_child: { parentID: "ses_gap" }, ses_last: {} });

// A root session that ends its turn with a gap is prompted once, with the check's reason, in the same session.
host.push("session.execution.succeeded", "ses_gap");
await until(() => host.sent.length === 1, "the hold");
assert.equal(host.sent[0].sessionID, "ses_gap");
assert.match(host.sent[0].text, /^Stopping now leaves these gaps:\n- .*herdr-launches\.jsonl: w1 \(pane p-w1\): launched without a registered attempt\n/);

// The turn the hold started ends unchecked, so a gap the agent cannot clear never loops; the turn after is checked.
host.push("session.execution.succeeded", "ses_gap");
host.push("session.execution.succeeded", "ses_gap2");
await until(() => holdsFor(host, "ses_gap2") === 1, "the second session's hold");
assert.equal(holdsFor(host, "ses_gap"), 1, "the held turn's own end is not held again");
host.push("session.execution.succeeded", "ses_gap");
await until(() => holdsFor(host, "ses_gap") === 2, "the next turn's hold");

// Not held: a subagent's child session (its parent answers for the gaps), a turn that failed or was interrupted, and a
// session with no gap.
host.push("session.execution.succeeded", "ses_child");
host.push("session.execution.failed", "ses_gap");
host.push("session.execution.interrupted", "ses_gap");
host.push("session.execution.succeeded", "ses_clean");
host.push("session.execution.succeeded", "ses_last");
await until(() => holdsFor(host, "ses_last") === 1, "the last session's hold");
assert.deepEqual(host.sent.map((input) => input.sessionID), ["ses_gap", "ses_gap2", "ses_gap", "ses_last"]);
// A check that cannot run holds, naming why, and never passes silently; the next turn is checked as usual.
const path = process.env.PATH;
process.env.PATH = bin;  // No python3.
host.push("session.execution.succeeded", "ses_clean");
await until(() => holdsFor(host, "ses_clean") === 1, "the failed check's hold");
process.env.PATH = path;
assert.match(host.sent.at(-1).text, /^Stopping now leaves these gaps:\n- could not check workers: .*python3/);
host.push("session.execution.succeeded", "ses_clean");  // The end of the turn that hold started.
host.push("session.execution.succeeded", "ses_gap2");  // Likewise for ses_gap2's earlier hold.
host.push("session.execution.succeeded", "ses_gap2");
await until(() => holdsFor(host, "ses_gap2") === 2, "a hold after the failed check");
await host.cleanup?.();

// An agent's shell call (OpenCode 2 names the tool `shell`) that starts a Herdr agent is logged for the gate before it
// runs, under this session.
const log = join(repo, ".scratch/herdr-launches.jsonl");
const logged = () => readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line));
const before = logged().length;
const call = (sessionID, command) => ({ tool: "shell", sessionID, agent: "build", messageID: "msg_1", id: "call_1",
  input: { command, description: "start a worker" } });
await host.hooks["execute.before"](call("ses_log", "herdr agent start w9 --cwd .worktrees/x -- opencode"));
assert.equal(logged().length, before + 1);
assert.deepEqual({ ...logged().at(-1), time: undefined },
  { time: undefined, agent: "w9", pane: null, coordinator: { pane: null, session: "ses_log" } });

// Logging that fails never blocks the call.
process.env.PATH = bin;
await host.hooks["execute.before"](call("ses_log", "herdr agent start w10"));
process.env.PATH = path;
assert.equal(logged().length, before + 1);

// ZLOOP_YIELD_HOOK=off turns the gate off, as on the other hosts.
process.env.ZLOOP_YIELD_HOOK = "off";
const off = await load(repo, { ses_gap: {} });
off.push("session.execution.succeeded", "ses_gap");
await new Promise((done) => setTimeout(done, 1500));
assert.deepEqual(off.sent, []);
await off.cleanup?.();
delete process.env.ZLOOP_YIELD_HOOK;
console.log("OpenCode yield gate plugin: ok");
