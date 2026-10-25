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

// Running one types its skill command, which Pi expands as the skill; one typed while Pi works waits its turn.
await commands.get("zloop:autopilot").handler("--take-over --auto-merge", {});
await commands.get("zloop:brief").handler("", {});
assert.deepEqual(sent, [
  ["/skill:autopilot --take-over --auto-merge", { deliverAs: "followUp", expandPromptTemplates: true }],
  ["/skill:brief", { deliverAs: "followUp", expandPromptTemplates: true }],
]);

// Typing arguments offers the flags and modes the skill's argument-hint declares, matching what is typed; picking one
// replaces the arguments typed so far. Each item shows the whole hint beside it.
const complete = (name, typed) => commands.get(`zloop:${name}`).getArgumentCompletions(typed);
const AUTOPILOT = "<goal> | --take-over [--auto-merge] | --pause";
assert.deepEqual(await complete("autopilot", ""), [
  { value: "--take-over ", label: "--take-over", description: AUTOPILOT },
  { value: "--pause ", label: "--pause", description: AUTOPILOT },
]);
assert.deepEqual(await complete("autopilot", "--t"), [
  { value: "--take-over ", label: "--take-over", description: AUTOPILOT },
]);
assert.equal(await complete("autopilot", "--x"), null);

// A value comes right after its flag or mode: only the values the hint lists, or nothing when the user types it.
const labels = async (name, typed) => (await complete(name, typed))?.map((item) => item.label) ?? null;
assert.deepEqual(await complete("review", "PR-12 --comments "), ["draft", "confirm", "post"].map((label) => ({
  value: `PR-12 --comments ${label} `, label,
  description: "[walkthrough] <branch|PR-number|PR-URL> [--autofix] [--comments draft|confirm|post] [--base <ref>]",
})));
assert.equal(await labels("review", "--base "), null);
assert.deepEqual(await labels("setup", "calibration "), ["status"]);

// Nothing typed is offered again, nor another choice for an argument already given, nor a branch the hint's spaced
// | rules out once its alternative is typed; a flag written after another's value comes once that flag is typed.
assert.deepEqual(await labels("autopilot", "--take-over "), ["--auto-merge"]);
assert.equal(await labels("autopilot", "--take-over --auto-merge "), null);
assert.equal(await labels("autopilot", "--pause "), null);
assert.deepEqual(await labels("remediate", ""), ["tautological-tests", "misleading-comments", "--base", "--project"]);
assert.deepEqual(await labels("remediate", "tautological-tests "), ["--base", "--project"]);
assert.deepEqual(await labels("remediate", "--project "), ["tautological-tests", "misleading-comments"]);
assert.deepEqual(await labels("remediate", "--base main "), ["tautological-tests", "misleading-comments", "--head"]);

// Every hint lists only real flags and modes as bare words, placeholders in <>, so a blank argument offers exactly
// these and never a word of prose.
const START = {
  align: ["--interactive"], autopilot: ["--take-over", "--pause"], brief: ["map"], diagnose: null,
  dispatch: ["--autofix"], explain: null, huh: null, pr: null, prototype: null, refine: null,
  remediate: ["tautological-tests", "misleading-comments", "--base", "--project"], research: null,
  review: ["walkthrough", "--autofix", "--comments", "--base"],
  setup: ["show", "set", "reset", "calibrate", "calibration", "check", "retro", "replay", "--scope", "--route",
    "--config-profile", "runtime=claude", "runtime=codex", "runtime=pi", "runtime=opencode", "--candidate",
    "thinking="],
  tdd: null,
};
for (const [name, expected] of Object.entries(START)) assert.deepEqual(await labels(name, ""), expected, name);

console.log("Pi zloop commands: registered, hand off as /skill:, complete the hinted flags and modes: ok");
