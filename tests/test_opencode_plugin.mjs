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
const path = `${bin}:${process.env.PATH}`;
process.env.PATH = path;

const until = async (check, what) => {
  for (const due = Date.now() + 15000; Date.now() < due; await new Promise((done) => setTimeout(done, 20))) {
    if (check()) return;
  }
  assert.fail(`timed out waiting for ${what}`);
};

// A checkout whose launch log holds one launch, that no attempt registered, by each session in `gaps`: real
// yield-gate gaps for those sessions and none for any other. `ses_end` always has one.
function checkout(gaps) {
  const repo = mkdtempSync(join(tmpdir(), "zloop-oc-repo-"));
  execFileSync("git", ["init", "-q", repo]);
  mkdirSync(join(repo, ".scratch"));
  writeFileSync(join(repo, ".scratch/herdr-launches.jsonl"), [...gaps, "ses_end"].map((session) => JSON.stringify({
    time: "2026-10-27T00:00:00Z", agent: `w-${session}`, pane: `p-${session}`, coordinator: { pane: null, session },
  }) + "\n").join(""));
  return repo;
}

// A stand-in for OpenCode's plugin context: events pushed by the test, sessions with a directory and an optional parent.
async function load(gaps, parents = {}, admission = 0) {
  const repo = checkout(gaps);
  const subscribers = new Set();
  const sent = [];
  let admitted = 0;
  const hooks = {};
  const ctx = {
    location: { directory: repo },
    event: {
      // Every subscriber sees every event of the server, whatever its location.
      subscribe: ({ signal } = {}) => ({
        async *[Symbol.asyncIterator]() {
          const subscriber = { queue: [], wake: () => {} };
          subscribers.add(subscriber);
          while (!signal?.aborted) {
            if (subscriber.queue.length) yield subscriber.queue.shift();
            else await new Promise((done) => { subscriber.wake = done; });
          }
        },
      }),
    },
    session: {
      get: async ({ sessionID }) => ({ id: sessionID, location: { directory: repo }, parentID: parents[sessionID] }),
      // OpenCode runs its prompt hooks before it admits the message, and stamps the message when it does.
      prompt: async (input) => {
        sent.push(input);
        await new Promise((done) => setTimeout(done, admission));
        admitted += 1;
        return { id: `msg_${sent.length}`, time: { created: Date.now() }, delivery: "steer" };
      },
    },
    tool: { hook: async (name, callback) => { hooks[name] = callback; return { dispose: async () => {} }; } },
  };
  const { default: definition } = await import(`${plugin}?${Math.random()}`);
  const cleanup = await definition.setup(ctx);
  // OpenCode stamps each event with the time it was published.
  const push = (outcome, sessionID, created = Date.now()) => {
    for (const subscriber of subscribers) {
      subscriber.queue.push({ type: `session.execution.${outcome}`, created, data: { sessionID } });
      subscriber.wake();
    }
  };
  const holds = (sessionID) => sent.filter((input) => input.sessionID === sessionID).length;
  // Events are handled in order, so once `ses_end`'s hold arrives every earlier event has been handled.
  const settle = async () => {
    const before = holds("ses_end");
    push("succeeded", "ses_end");
    await until(() => holds("ses_end") > before && admitted === sent.length, "the closing hold");
    push("succeeded", "ses_end");  // Its own held turn ends, so the next settle is checked again.
  };
  // OpenCode sets the plugin up once for each directory it serves, all on one server.
  const elsewhere = async () => definition.setup({ ...ctx, location: { directory: checkout([]) } });
  return { repo, sent, hooks, push, holds, settle, cleanup, elsewhere };
}

{
  // A root session that ends its turn with a gap is prompted once, with the check's reason, in the same session.
  const host = await load(["ses_a"]);
  host.push("succeeded", "ses_a");
  await host.settle();
  assert.equal(host.holds("ses_a"), 1);
  assert.equal(host.sent[0].sessionID, "ses_a");
  assert.match(host.sent[0].text,
    /^Stopping now leaves these gaps:\n- .*herdr-launches\.jsonl: w-ses_a \(pane p-ses_a\): launched without a registered attempt\n/);

  // The turn the hold started ends unchecked, so a gap the agent cannot clear never loops; the turn after is checked.
  host.push("succeeded", "ses_a");
  await host.settle();
  assert.equal(host.holds("ses_a"), 1, "the held turn's own end is not held again");
  host.push("succeeded", "ses_a");
  await host.settle();
  assert.equal(host.holds("ses_a"), 2, "the next turn is checked");
  await host.cleanup();
}

{
  // A server that serves several directories holds a stop once: only the plugin set up for the session's directory
  // answers for it.
  const host = await load(["ses_m"]);
  const other = await host.elsewhere();
  host.push("succeeded", "ses_m");
  await host.settle();
  assert.equal(host.holds("ses_m"), 1);
  await other();
  await host.cleanup();
}

{
  // A held turn that fails or is interrupted also ends the hold: the user's next turn is checked.
  const host = await load(["ses_f", "ses_i"]);
  host.push("succeeded", "ses_f");
  host.push("succeeded", "ses_i");
  await host.settle();
  host.push("failed", "ses_f");
  host.push("interrupted", "ses_i");
  host.push("succeeded", "ses_f");
  host.push("succeeded", "ses_i");
  await host.settle();
  assert.deepEqual([host.holds("ses_f"), host.holds("ses_i")], [2, 2]);
  await host.cleanup();
}

{
  // A success published before the hold was sent (a turn that ended while the check ran) neither holds again nor
  // stands in for the end of the turn the hold started.
  const host = await load(["ses_q"]);
  const earlier = Date.now();
  host.push("succeeded", "ses_q", earlier);
  await host.settle();
  host.push("succeeded", "ses_q", earlier + 1);  // Queued behind the first, older than the hold.
  host.push("succeeded", "ses_q");  // The held turn's end.
  await host.settle();
  assert.equal(host.holds("ses_q"), 1);
  host.push("succeeded", "ses_q");
  await host.settle();
  assert.equal(host.holds("ses_q"), 2, "the next turn is checked");
  await host.cleanup();
}

{
  // A turn that ends while the hold is being admitted is older than the hold's own turn and does not end the hold.
  const host = await load(["ses_r"], {}, 300);
  host.push("succeeded", "ses_r");
  await until(() => host.holds("ses_r") === 1, "the hold to be sent");
  host.push("succeeded", "ses_r");  // Published before the hold is admitted.
  await host.settle();
  host.push("succeeded", "ses_r");  // The held turn's end.
  await host.settle();
  assert.equal(host.holds("ses_r"), 1);
  await host.cleanup();
}

{
  // Not held: a subagent's child session (its parent answers for the gaps), a turn that failed or was interrupted, and a
  // session with no gap.
  const host = await load(["ses_child", "ses_f", "ses_i"], { ses_child: "ses_parent" });
  host.push("succeeded", "ses_child");
  host.push("failed", "ses_f");
  host.push("interrupted", "ses_i");
  host.push("succeeded", "ses_clean");
  await host.settle();
  assert.deepEqual(host.sent.map((input) => input.sessionID), ["ses_end"]);
  await host.cleanup();
}

{
  // A check that cannot run holds, naming why, and never passes silently; the next turn is checked as usual.
  const host = await load([]);
  process.env.PATH = bin;  // No python3.
  host.push("succeeded", "ses_clean");
  await until(() => host.holds("ses_clean") === 1, "the failed check's hold");
  process.env.PATH = path;
  assert.match(host.sent[0].text, /^Stopping now leaves these gaps:\n- could not check workers: .*python3/);
  host.push("succeeded", "ses_clean");  // The end of the turn that hold started.
  host.push("succeeded", "ses_clean");
  await host.settle();
  assert.equal(host.holds("ses_clean"), 1, "a check that runs again clears");
  await host.cleanup();
}

{
  // An agent's shell call (OpenCode 2 names the tool `shell`) that starts a Herdr agent is logged for the gate before it
  // runs, under this session, however the shell command is written.
  const host = await load([]);
  const logged = () => readFileSync(join(host.repo, ".scratch/herdr-launches.jsonl"), "utf8").trim().split("\n")
    .map((line) => JSON.parse(line));
  const before = logged().length;
  const call = (command) => ({ tool: "shell", sessionID: "ses_log", agent: "build", messageID: "msg_1", id: "call_1",
    input: { command, description: "start a worker" } });
  await host.hooks["execute.before"](call("herdr agent start w9 --cwd .worktrees/x -- opencode"));
  await host.hooks["execute.before"](call("herdr agent \\\n  start w11 --cwd .worktrees/y -- opencode"));
  assert.deepEqual(logged().slice(before).map((record) => ({ ...record, time: undefined })), [
    { time: undefined, agent: "w9", pane: null, coordinator: { pane: null, session: "ses_log" } },
    { time: undefined, agent: "w11", pane: null, coordinator: { pane: null, session: "ses_log" } },
  ]);

  // Another tool's input that mentions such a command launches nothing and is not logged.
  await host.hooks["execute.before"]({ ...call("herdr agent start w12"), tool: "task" });
  assert.equal(logged().length, before + 2);

  // Logging that fails never blocks the call.
  process.env.PATH = bin;
  await host.hooks["execute.before"](call("herdr agent start w10"));
  process.env.PATH = path;
  assert.equal(logged().length, before + 2);
  await host.cleanup();
}

{
  // ZLOOP_YIELD_HOOK=off turns the gate off, as on the other hosts.
  process.env.ZLOOP_YIELD_HOOK = "off";
  const host = await load(["ses_a"]);
  host.push("succeeded", "ses_a");
  await new Promise((done) => setTimeout(done, 1500));
  assert.deepEqual(host.sent, []);
  await host.cleanup();
  delete process.env.ZLOOP_YIELD_HOOK;
}
console.log("OpenCode yield gate plugin: ok");
