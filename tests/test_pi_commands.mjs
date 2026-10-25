import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Pi gets a /zloop:<skill> command for each skill the user types, as Claude Code's /zloop:<skill>.
const bootstrap = resolve(dirname(fileURLToPath(import.meta.url)), "../hosts/pi/bootstrap.ts");
const sent = [];
const commands = new Map();
const pi = {
  on() {},
  registerTool() {},
  registerCommand: (name, options) => commands.set(name, options),
  sendUserMessage: (...args) => sent.push(args),
};
(await import(bootstrap)).default(pi);

// Every user-typed skill, and not housekeeping, which only the agent loads.
assert.deepEqual([...commands.keys()].sort(), [
  "align", "autopilot", "brief", "diagnose", "dispatch", "explain", "huh", "pr", "prototype", "refine", "remediate",
  "research", "review", "setup", "tdd",
].map((skill) => `zloop:${skill}`).sort());

console.log("Pi zloop commands: registered, hand off as /skill:, complete the hinted flags and modes: ok");
