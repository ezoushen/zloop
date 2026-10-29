import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// The OpenCode plugin runs the real yield-gate check and launch log against a temporary checkout; only OpenCode's
// plugin context and the herdr binary are stand-ins.
const plugin = resolve(dirname(fileURLToPath(import.meta.url)), "../hosts/opencode/plugin.js");
for (const name of ["HERDR_PANE_ID", "HERDR_WORKSPACE_ID", "HERDR_ENV", "ZLOOP_WORKER", "ZLOOP_YIELD_HOOK"]) {
  delete process.env[name];
}
// Every fixture lives under one folder, removed when the run exits, pass or fail.
const temp = mkdtempSync(join(tmpdir(), "zloop-oc-"));
process.on("exit", () => rmSync(temp, { recursive: true, force: true }));
const bin = mkdtempSync(join(temp, "bin-"));
writeFileSync(join(bin, "herdr"), "#!/bin/sh\nexit 1\n");
chmodSync(join(bin, "herdr"), 0o755);
// python3 as the plugin finds it: it records each run's arguments, and waits `ZLOOP_TEST_SLOW` seconds when set.
const wrap = mkdtempSync(join(temp, "wrap-"));
const runs = join(wrap, "runs.log");
const python = execFileSync("sh", ["-c", "command -v python3"], { encoding: "utf8" }).trim();
writeFileSync(join(wrap, "python3"), `#!/bin/sh\necho "$*" >> '${runs}'\nsleep "\${ZLOOP_TEST_SLOW:-0}"\nexec '${python}' "$@"\n`);
chmodSync(join(wrap, "python3"), 0o755);
const checked = (sessionID) => readFileSync(runs, "utf8").split("\n").some((run) => run.endsWith(` ${sessionID}`));
const path = `${bin}:${wrap}:${process.env.PATH}`;
process.env.PATH = path;

const until = async (check, what) => {
  for (const due = Date.now() + 15000; Date.now() < due; await new Promise((done) => setTimeout(done, 20))) {
    if (check()) return;
  }
  assert.fail(`timed out waiting for ${what}`);
};

// A checkout whose launch log holds one launch, that no attempt registered, by each session in `gaps`: real
// yield-gate gaps for those sessions and none for any other. `end` always has one.
function checkout(gaps, end = "ses_end") {
  const repo = mkdtempSync(join(temp, "repo-"));
  execFileSync("git", ["init", "-q", repo]);
  mkdirSync(join(repo, ".scratch"));
  writeFileSync(join(repo, ".scratch/herdr-launches.jsonl"), [...gaps, end].map((session) => JSON.stringify({
    time: "2026-10-27T00:00:00Z", agent: `w-${session}`, pane: `p-${session}`, orchestrator: { pane: null, session },
  }) + "\n").join(""));
  return repo;
}

// A stand-in for OpenCode's plugin context: events pushed by the test, sessions with a directory and an optional parent.
let loads = 0;
async function load(gaps, parents = {}, admission = 0, written = {}) {
  // Each load closes with its own session: the plugin's holds outlive one setup, as they do across OpenCode's.
  const end = `ses_end_${++loads}`;
  const repo = checkout(gaps, end);
  const subscribers = new Set();
  let stopped = 0;
  const sent = [];
  let admitted = 0;
  // Set to refuse, or to admit after another delay, the prompts sent from then on.
  const control = { refuse: false, admission: undefined };
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
          stopped += 1;  // The setup asked for its next event after it was stopped: it is done with the last one.
        },
      }),
    },
    session: {
      get: async ({ sessionID }) => ({
        id: sessionID, location: { directory: (written[sessionID] ?? "{repo}").replace("{repo}", repo) }, parentID: parents[sessionID],
      }),
      // OpenCode runs its prompt hooks before it admits the message, and stamps the message when it does.
      prompt: async (input) => {
        sent.push(input);
        const refused = control.refuse;
        await new Promise((done) => setTimeout(done, control.admission ?? admission));
        admitted += 1;
        if (refused) throw new Error("session gone");
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
  // Events are handled in order, so once `end`'s hold arrives every earlier event has been handled.
  const settle = async () => {
    const before = holds(end);
    push("succeeded", end);
    await until(() => holds(end) > before && admitted === sent.length, "the closing hold");
    push("succeeded", end);  // Its own held turn ends, so the next settle is checked again.
  };
  // OpenCode sets the plugin up once for each directory it serves, all on one server.
  // `slow` delays that setup's session lookups, so the test can order it against the first one.
  const elsewhere = async (directory = checkout([]), slow = () => 0) => definition.setup({
    ...ctx, location: { directory },
    session: { ...ctx.session, get: async (input) => {
      await new Promise((done) => setTimeout(done, typeof slow === "function" ? slow() : slow));
      return ctx.session.get(input);
    } },
  });
  return { repo, end, sent, hooks, push, holds, settle, cleanup, elsewhere, control, stopped: () => stopped };
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
  // A session whose directory is written another way (an imported session keeps its own form) is still the
  // directory's.
  const host = await load(["ses_s", "ses_d"], {}, 0, { ses_s: "{repo}/", ses_d: "{repo}/sub/../" });
  host.push("succeeded", "ses_s");
  host.push("succeeded", "ses_d");
  await host.settle();
  assert.deepEqual([host.holds("ses_s"), host.holds("ses_d")], [1, 1]);
  await host.cleanup();
}

{
  // A session moved to another directory while it is held keeps its hold: the held turn ends unchecked wherever it ends,
  // and the turn after is checked where the session now is.
  const written = {};
  const host = await load(["ses_v"], {}, 0, written);
  const there = checkout(["ses_v"]);
  const other = await host.elsewhere(there);
  host.push("succeeded", "ses_v");
  await host.settle();
  written.ses_v = there;
  host.push("succeeded", "ses_v");  // The held turn's end, now in `there`.
  await host.settle();
  assert.equal(host.holds("ses_v"), 1);
  host.push("succeeded", "ses_v");
  await until(() => host.holds("ses_v") === 2, "the next turn's check where the session now is");
  await other();
  await host.cleanup();
}

{
  // A session that moves while its turn end is checked is held once: the check claims the turn end before it runs.
  const written = {};
  const host = await load(["ses_w"], {}, 0, written);
  const there = checkout(["ses_w"]);
  const other = await host.elsewhere(there, 40);
  host.push("succeeded", "ses_w");
  setTimeout(() => { written.ses_w = there; }, 5);  // Moved after the first setup looked it up, before the second.
  await until(() => host.holds("ses_w") === 1, "the hold");
  await new Promise((done) => setTimeout(done, 1000));
  assert.equal(host.holds("ses_w"), 1);
  await other();
  await host.cleanup();
}

{
  // A newer turn that ends where the session moved, while its older turn is still checked, is checked in its own right.
  const written = {};
  const host = await load([], {}, 0, written);
  const there = checkout(["ses_n"]);
  const other = await host.elsewhere(there);
  host.push("succeeded", "ses_n");  // Checked where it was, with no gap there.
  setTimeout(() => {
    written.ses_n = there;
    host.push("succeeded", "ses_n");
  }, 10);
  await until(() => host.holds("ses_n") === 1, "the newer turn's hold");
  await other();
  await host.cleanup();
}

{
  // A setup that is replaced while its hold is admitted, and whose prompt then fails, leaves its replacement's hold alone.
  const host = await load(["ses_y"]);
  Object.assign(host.control, { refuse: true, admission: 600 });
  host.push("succeeded", "ses_y");
  await until(() => host.holds("ses_y") === 1, "the first hold to be sent");
  await host.cleanup();
  Object.assign(host.control, { refuse: false, admission: undefined });
  const again = await host.elsewhere(host.repo);
  host.push("succeeded", "ses_y");  // A newer turn, held by the replacement.
  await until(() => host.holds("ses_y") === 2, "the replacement's hold");
  await new Promise((done) => setTimeout(done, 700));  // The replaced setup's prompt has failed by now.
  host.push("succeeded", "ses_y");  // The held turn's end.
  await host.settle();
  assert.equal(host.holds("ses_y"), 2);
  await again();
}

{
  // A reload drops the holds of the setup it replaces, whose held turns may end unseen: the session's next turn is
  // checked rather than passed.
  const host = await load(["ses_z"]);
  host.push("succeeded", "ses_z");
  await host.settle();
  await host.cleanup();  // Reloaded before the held turn's end arrives.
  const again = await host.elsewhere(host.repo);
  host.push("succeeded", "ses_z");
  await host.settle();
  assert.equal(host.holds("ses_z"), 2);
  await again();
}

{
  // A setup replaced while its hold is admitted leaves no hold behind: the session's next turn is checked.
  const host = await load(["ses_t"]);
  host.control.admission = 300;
  host.push("succeeded", "ses_t");
  await until(() => host.holds("ses_t") === 1, "the hold to be sent");
  await host.cleanup();  // Replaced while the hold is admitted.
  host.control.admission = undefined;
  await new Promise((done) => setTimeout(done, 400));
  const again = await host.elsewhere(host.repo);
  host.push("succeeded", "ses_t");
  await until(() => host.holds("ses_t") === 2, "the next turn's hold");
  await again();
}

{
  // A setup replaced while its check runs sends nothing: its replacement answers for the session.
  const host = await load(["ses_c"]);
  process.env.ZLOOP_TEST_SLOW = "1";
  host.push("succeeded", "ses_c");
  await until(() => checked("ses_c"), "the check to start");
  await host.cleanup();  // Replaced while the check runs.
  await until(() => host.stopped() === 1, "the replaced setup to finish with the turn end");
  delete process.env.ZLOOP_TEST_SLOW;
  assert.equal(host.holds("ses_c"), 0);
}

{
  // A setup replaced while it looks a session up does not act on it: the held turn's end it saw is its replacement's.
  const host = await load(["ses_k"]);
  await host.cleanup();
  const replaced = await host.elsewhere(host.repo, () => 200);
  let wait = 0;
  const replacement = await host.elsewhere(host.repo, () => wait);
  host.push("succeeded", "ses_k");
  await until(() => host.holds("ses_k") === 1, "the first hold");
  await new Promise((done) => setTimeout(done, 1000));  // Both setups have handled it.
  wait = 400;
  host.push("succeeded", "ses_k");  // The held turn's end.
  setTimeout(() => replaced(), 100);  // Replaced while it looks the session up.
  await host.settle();  // The replacement handles events in order, so it has handled the held turn's end.
  assert.equal(host.holds("ses_k"), 1);
  await replacement();
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
  assert.deepEqual(host.sent.map((input) => input.sessionID), [host.end]);
  assert.equal(checked("ses_child"), false, "a child's turn end runs no check");
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
  await host.hooks["execute.before"](call("herdr agent start w9 --kind opencode --pane w1:p9"));
  await host.hooks["execute.before"](call("herdr agent \\\n  start w11 --kind opencode --pane w1:p11"));
  assert.deepEqual(logged().slice(before).map((record) => ({ ...record, time: undefined })), [
    { time: undefined, agent: "w9", pane: "w1:p9", orchestrator: { pane: null, session: "ses_log" } },
    { time: undefined, agent: "w11", pane: "w1:p11", orchestrator: { pane: null, session: "ses_log" } },
  ]);

  // Another tool's input that mentions such a command launches nothing and is not logged.
  await host.hooks["execute.before"]({ ...call("herdr agent start w12 --kind opencode --pane w1:p12"), tool: "task" });
  assert.equal(logged().length, before + 2);

  // Logging that fails never blocks the call.
  process.env.PATH = bin;
  await host.hooks["execute.before"](call("herdr agent start w10 --kind opencode --pane w1:p10"));
  process.env.PATH = path;
  assert.equal(logged().length, before + 2);
  await host.cleanup();
}

{
  // A subagent's launch is logged under its root session, whose stop answers for it; the subagent is never held.
  const host = await load([], { ses_kid: "ses_top", ses_grandkid: "ses_kid" });
  const call = (sessionID, agent) => host.hooks["execute.before"]({ tool: "shell", sessionID, agent: "general",
    messageID: "msg_1", id: "call_1", input: { command: `herdr agent start ${agent} --kind opencode --pane w1:${agent}` } });
  await call("ses_kid", "w20");
  await call("ses_grandkid", "w21");
  const logged = readFileSync(join(host.repo, ".scratch/herdr-launches.jsonl"), "utf8").trim().split("\n")
    .map((line) => JSON.parse(line)).filter((record) => record.agent?.startsWith("w2"))
    .map((record) => ({ agent: record.agent, session: record.orchestrator.session }));
  assert.deepEqual(logged, [{ agent: "w20", session: "ses_top" }, { agent: "w21", session: "ses_top" }]);
  await host.cleanup();
}

{
  // ZLOOP_YIELD_HOOK=off turns the gate off, as on the other hosts.
  process.env.ZLOOP_YIELD_HOOK = "off";
  const host = await load(["ses_o"]);
  host.push("succeeded", "ses_o");
  await new Promise((done) => setTimeout(done, 1500));
  assert.deepEqual(host.sent, []);
  assert.equal(checked("ses_o"), false, "no check runs");
  await host.cleanup();
  delete process.env.ZLOOP_YIELD_HOOK;
}
console.log("OpenCode yield gate plugin: ok");
