import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Use the installed SDK without loading the user's extensions or calling a model.
const { DefaultResourceLoader, SettingsManager } = await import(pathToFileURL(process.argv[2]).href);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const temp = mkdtempSync(join(tmpdir(), "zloop pi "));
try {
  const agentDir = join(temp, "agent");
  const extensions = join(agentDir, "extensions");
  const skills = join(agentDir, "skills");
  mkdirSync(extensions, { recursive: true });
  mkdirSync(skills);
  symlinkSync(join(root, "hosts/pi/bootstrap.ts"), join(extensions, "zloop.ts"));
  const packageDir = join(temp, "package");
  const unselectedAddon = join(packageDir, "addons/unselected/skills/probe");
  mkdirSync(unselectedAddon, { recursive: true });
  writeFileSync(join(unselectedAddon, "SKILL.md"),
    "---\nname: unselected-addon-probe\ndescription: Optional addon discovery fixture\n---\nNever auto-load this addon.\n");
  symlinkSync(join(root, "skills"), join(packageDir, "skills"));
  symlinkSync(join(packageDir, "skills"), join(skills, "zloop"));
  const loader = new DefaultResourceLoader({
    cwd: temp, agentDir, settingsManager: SettingsManager.inMemory({}),
    noContextFiles: true, noPromptTemplates: true, noThemes: true,
  });
  for (const pass of ["startup", "reload"]) {
    await loader.reload();
    const loaded = loader.getExtensions();
    assert.deepEqual(loaded.errors, [], pass);
    assert.equal(loaded.extensions.length, 1, pass);
    const extension = loaded.extensions[0];
    assert.deepEqual([...extension.tools.keys()], ["zloop_wake"]);
    // Pi's own loader registers a /zloop:<skill> command for each skill the user types.
    assert.deepEqual([...extension.commands.keys()].sort(), ["align", "autopilot", "brief", "diagnose", "dispatch",
      "explain", "huh", "pr", "prototype", "refine", "remediate", "research", "review", "setup", "tdd"]
      .map((skill) => `zloop:${skill}`).sort(), pass);
    assert.deepEqual([...extension.handlers.keys()],
      ["before_agent_start", "agent_settled", "agent_before_settle", "tool_call", "tool_result"]);
    // Every tool_call handler runs in order on the same input until one blocks, as Pi's runner does.
    const call = async (event, ctx) => {
      let verdict;
      for (const handler of extension.handlers.get("tool_call")) {
        verdict = (await handler(event, ctx)) ?? verdict;
        if (verdict?.block) return verdict;
      }
      return verdict;
    };
    // A foreground bash call without a timeout stops after 120 s; an explicit timeout and other tools are kept.
    const bounded = async (toolName, input) => {
      await call({ type: "tool_call", toolCallId: `${pass}-timeout`, toolName, input }, {});
      return input;
    };
    assert.deepEqual(await bounded("bash", { command: "find / -name x" }), { command: "find / -name x", timeout: 120 });
    assert.deepEqual(await bounded("bash", { command: "make test", timeout: 900 }), { command: "make test", timeout: 900 });
    assert.deepEqual(await bounded("bg_run", { command: "sleep 600" }), { command: "sleep 600" });
    // ZLOOP_BASH_TIMEOUT=off turns the default off, and the hint then promises no bound.
    const previousTimeout = process.env.ZLOOP_BASH_TIMEOUT;
    process.env.ZLOOP_BASH_TIMEOUT = "off";
    try {
      assert.deepEqual(await bounded("bash", { command: "find / -name x" }), { command: "find / -name x" });
      const unbounded = await extension.handlers.get("before_agent_start")[0]({ systemPrompt: "existing" }, {});
      assert.ok(!unbounded.systemPrompt.includes("Shell commands given no timeout"));
    } finally {
      if (previousTimeout === undefined) delete process.env.ZLOOP_BASH_TIMEOUT;
      else process.env.ZLOOP_BASH_TIMEOUT = previousTimeout;
    }
    // Loop guard: the same call returning the same result three times in a row is blocked next time.
    const toolResult = extension.handlers.get("tool_result")[0];
    let id = 0;
    const step = async (command, output) => {
      const toolCallId = `${pass}-${id++}`;
      const input = { command };
      const verdict = await call({ type: "tool_call", toolCallId, toolName: "bash", input }, {});
      await toolResult({ type: "tool_result", toolCallId, toolName: "bash", input,
        content: [{ type: "text", text: verdict?.block ? verdict.reason : output }], isError: !!verdict?.block }, {});
      return verdict;
    };
    for (let i = 0; i < 3; i++) assert.equal(await step("pwd; ls", "/repo"), undefined);
    const blocked = await step("pwd; ls", "/repo");
    assert.equal(blocked.block, true);
    assert.match(blocked.reason, /same result 3 times/);
    assert.notEqual(blocked.terminate, true);
    assert.equal((await step("pwd; ls", "/repo")).terminate, true);
    // A different call resets it, and polling whose output changes is never blocked.
    assert.equal(await step("git status", "clean"), undefined);
    for (let i = 0; i < 5; i++) assert.equal(await step("herdr agent get w", `working ${i}`), undefined);
    const previousGuard = process.env.ZLOOP_LOOP_GUARD;
    process.env.ZLOOP_LOOP_GUARD = "off";
    try {
      for (let i = 0; i < 5; i++) assert.equal(await step("pwd", "/repo"), undefined);
    } finally {
      if (previousGuard === undefined) delete process.env.ZLOOP_LOOP_GUARD;
      else process.env.ZLOOP_LOOP_GUARD = previousGuard;
    }
    await step("reset", "x");
    const handlers = extension.handlers.get("before_agent_start");
    assert.equal(handlers.length, 1);
    const result = await handlers[0]({ systemPrompt: "existing instructions" }, {});
    assert.ok(result.systemPrompt.startsWith("existing instructions\n"));
    assert.ok(result.systemPrompt.includes(`${root}/bootstrap.md`));
    assert.ok(result.systemPrompt.includes(`${root}/hosts/pi/session.md`));
    assert.ok(!result.systemPrompt.includes("/hosts/claude/"));
    assert.ok(!result.systemPrompt.includes("/hosts/codex/"));
    assert.ok(result.systemPrompt.includes("Shell commands given no timeout stop after 120 s"));
    const previousHerdr = process.env.HERDR_ENV;
    try {
      for (const value of ["", "0", "1"]) {
        process.env.HERDR_ENV = value;
        const context = (await handlers[0]({ systemPrompt: "existing instructions" }, {})).systemPrompt;
        assert.equal(context.includes(`${root}/hosts/herdr.md`), value === "1");
        assert.ok(context.includes(`${root}/bootstrap.md`));
        assert.ok(context.includes(`${root}/hosts/pi/session.md`));
        assert.ok(context.startsWith("existing instructions\n"));
      }
    } finally {
      if (previousHerdr === undefined) delete process.env.HERDR_ENV;
      else process.env.HERDR_ENV = previousHerdr;
    }
    // Pi with structured sections: a returned systemPrompt would force the prompt
    // for typed-prompt runs only, so the hint must be a section instead.
    const sections = {};
    const structured = await handlers[0](
      { systemPrompt: "existing instructions", systemPromptOptions: { sections } }, {});
    assert.equal(structured, undefined);
    const section = Object.values(sections).join("");
    assert.ok(section.includes(`${root}/bootstrap.md`));
    assert.ok(section.includes(`${root}/hosts/pi/session.md`));
    assert.ok(!section.includes("existing instructions"));
    assert.ok(!loader.getSkills().skills.some((skill) => skill.name === "unselected-addon-probe"));
    const discovered = loader.getSkills().skills.filter(
      (skill) => realpathSync(skill.filePath).startsWith(`${root}/skills/`),
    );
    assert.deepEqual(discovered.map((skill) => skill.name).sort(), readdirSync(join(root, "skills")).sort());
    // The agent may load housekeeping and the five skills the user can also type; the rest are commands only.
    const agentLoaded = new Set(["housekeeping", "diagnose", "research", "prototype", "explain", "tdd"]);
    for (const skill of discovered) {
      assert.equal(skill.disableModelInvocation, !agentLoaded.has(skill.name), skill.name);
    }
  }
  console.log("Pi native reader: shared skills and invocation policy, symlinked bootstrap, startup/reload: ok");
} finally {
  rmSync(temp, { recursive: true, force: true });
}
