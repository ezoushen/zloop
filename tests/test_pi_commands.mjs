import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
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

// A word no literal names fills the hint's next open placeholder, ruling out the branches that placeholder excludes;
// a flag's value is never read as a mode; a key= argument already given is not offered again.
assert.equal(await labels("autopilot", "build a greeting tool "), null);
assert.equal(await labels("brief", "my-effort "), null);
assert.equal(await labels("remediate", "tautological-tests src/ "), null);
assert.deepEqual(await labels("remediate", "make tests clearer "), ["--base", "--project"]);
assert.deepEqual(await labels("setup", "--config-profile calibration "), ["show", "set", "reset", "calibrate",
  "calibration", "check", "retro", "replay", "--scope", "--route", "runtime=claude", "runtime=codex", "runtime=pi",
  "runtime=opencode", "--candidate", "thinking="]);
assert.equal(await labels("setup", "thinking=high th"), null);
// A mode word comes before the free text after it in the hint; flags may come anywhere.
assert.equal(await labels("review", "main w"), null);
assert.deepEqual(await labels("review", "w"), ["walkthrough"]);
// A quoted argument is one complete value: the next word fills the next placeholder.
assert.equal(await labels("remediate", '"make tests clearer" tests/ '), null);
assert.deepEqual(await labels("remediate", '"make tests clearer" '), ["--base", "--project"]);
// A word is a literal only where the hint allows that literal: elsewhere it is free text.
assert.deepEqual(await labels("review", "draft --comments "), ["draft", "confirm", "post"]);
assert.deepEqual(await labels("setup", "why calibration "), ["--scope", "--route", "--config-profile", "runtime=claude",
  "runtime=codex", "runtime=pi", "runtime=opencode", "--candidate", "thinking="]);

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

// The frontmatter is read as YAML writes it: CRLF line ends, single or double quotes, a folded or literal block.
const fixture = mkdtempSync(join(tmpdir(), "zloop pi commands "));
try {
  mkdirSync(join(fixture, "hosts/pi"), { recursive: true });
  copyFileSync(bootstrap, join(fixture, "hosts/pi/bootstrap.ts"));
  const skill = (name, text) => {
    mkdirSync(join(fixture, "skills", name), { recursive: true });
    writeFileSync(join(fixture, "skills", name, "SKILL.md"), text);
  };
  skill("crlf", '---\r\nname: crlf\r\ndescription: d\r\nargument-hint: "[--yes]"\r\n---\r\nbody\r\n');
  skill("single", "---\nname: single\ndescription: d\nargument-hint: '[--yes]'\n---\n");
  skill("folded", "---\nname: folded\ndescription: d\nargument-hint: >-\n  [--yes]\n  [--no]\n---\n");
  skill("literal", "---\nname: literal\ndescription: d\nargument-hint: |\n  [--yes]\n---\n");
  const found = new Map();
  (await import(join(fixture, "hosts/pi/bootstrap.ts"))).default(
    { on() {}, registerTool() {}, registerCommand: (name, options) => found.set(name, options), sendUserMessage() {} });
  const hinted = async (name) => (await found.get(`zloop:${name}`).getArgumentCompletions(""))?.map((item) => item.label);
  assert.deepEqual(await hinted("crlf"), ["--yes"]);
  assert.deepEqual(await hinted("single"), ["--yes"]);
  assert.deepEqual(await hinted("folded"), ["--yes", "--no"]);
  assert.deepEqual(await hinted("literal"), ["--yes"]);
} finally {
  rmSync(fixture, { recursive: true, force: true });
}

console.log("Pi zloop commands: registered, hand off as /skill:, complete the hinted flags and modes: ok");
