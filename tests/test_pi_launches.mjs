import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Pi's extension logs each Herdr agent launch for the yield gate through the real `hooks/herdr_launch.py`, against a
// temporary checkout; only Pi's extension API and the herdr binary are stand-ins.
const bootstrap = resolve(dirname(fileURLToPath(import.meta.url)), "../hosts/pi/bootstrap.ts");
for (const name of ["HERDR_PANE_ID", "HERDR_WORKSPACE_ID", "HERDR_ENV", "ZLOOP_WORKER", "PI_SUBAGENT_CHILD",
  "PI_SUBAGENT_PARENT_SESSION"]) {
  delete process.env[name];
}
const bin = mkdtempSync(join(tmpdir(), "zloop-pi-bin-"));
writeFileSync(join(bin, "herdr"), "#!/bin/sh\nexit 1\n");
chmodSync(join(bin, "herdr"), 0o755);
process.env.PATH = `${bin}:${process.env.PATH}`;

const handlers = [];
const pi = { on: (name, handler) => name === "tool_call" && handlers.push(handler), registerCommand() {}, registerTool() {} };
(await import(bootstrap)).default(pi);
const repo = mkdtempSync(join(tmpdir(), "zloop-pi-repo-"));
execFileSync("git", ["init", "-q", repo]);
const ctx = { cwd: repo, sessionManager: { getSessionId: () => "ses_pi" } };
const call = async (command, toolName = "bash") => {
  for (const handler of handlers) await handler({ type: "tool_call", toolCallId: "c", toolName, input: { command } }, ctx);
};
const logged = () => {
  try {
    return readFileSync(join(repo, ".scratch/herdr-launches.jsonl"), "utf8").trim().split("\n")
      .map((line) => JSON.parse(line)).map(({ agent, coordinator }) => ({ agent, session: coordinator.session }));
  } catch {
    return [];
  }
};

// A launch is logged under the session however the command is written, a line continuation included.
await call("herdr agent start w1 --cwd .worktrees/a -- pi");
await call("herdr agent \\\n  start w2 --cwd .worktrees/b -- pi", "bg_run");
assert.deepEqual(logged(), [{ agent: "w1", session: "ses_pi" }, { agent: "w2", session: "ses_pi" }]);

// A root session keeps its launches even with a parent session left in its environment, which only a child heeds.
process.env.PI_SUBAGENT_PARENT_SESSION = "ses_stale";
await call("herdr agent start w5 --cwd .worktrees/e -- pi");
assert.deepEqual(logged().at(-1), { agent: "w5", session: "ses_pi" });
// A pi-subagents child logs its launch under the session that launched it, whose stop answers for it.
process.env.PI_SUBAGENT_CHILD = "1";
process.env.PI_SUBAGENT_PARENT_SESSION = "ses_parent";
await call("herdr agent start w3 --cwd .worktrees/c -- pi");
// A child with no parent session named logs under its own.
delete process.env.PI_SUBAGENT_PARENT_SESSION;
await call("herdr agent start w4 --cwd .worktrees/d -- pi");
delete process.env.PI_SUBAGENT_CHILD;
assert.deepEqual(logged().slice(3), [{ agent: "w3", session: "ses_parent" }, { agent: "w4", session: "ses_pi" }]);
console.log("Pi launch logging: every launch, a child's under its parent session: ok");
