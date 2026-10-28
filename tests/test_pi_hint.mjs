import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Pi's extension tells each run which host it is in through `before_agent_start`; only Pi's extension API is a stand-in.
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const handlers = [];
const pi = { on: (name, handler) => name === "before_agent_start" && handlers.push(handler), registerCommand() {},
             registerShortcut() {}, registerTool() {} };
(await import(resolve(root, "hosts/pi/bootstrap.ts"))).default(pi);
assert.equal(handlers.length, 1);

const prompted = (await handlers[0]({ systemPrompt: "existing instructions" })).systemPrompt;
assert.ok(prompted.startsWith("existing instructions\n"));
const sections = {};
assert.equal(await handlers[0]({ systemPrompt: "existing instructions", systemPromptOptions: { sections } }), undefined);
for (const hint of [prompted, Object.values(sections).join("")]) {
  assert.ok(hint.includes("zloop host: pi. "), hint);
  assert.ok(hint.includes(`${root}/bootstrap.md`) && hint.includes(`${root}/hosts/pi/session.md`), hint);
}
console.log("Pi startup hint: names the host as `zloop host: pi`: ok");
