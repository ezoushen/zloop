import { execFile } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const root = resolve(dirname(realpathSync(fileURLToPath(import.meta.url))), "../..");

// The yield gate's check for one session: its gaps and the hold's message, as the Stop hooks word it.
async function check(cwd, session) {
  const { stdout } = await run("python3", [`${root}/hooks/yield_gate.py`, "check", cwd, session], { timeout: 20000 });
  return JSON.parse(stdout);
}

// OpenCode has no hook that can refuse a stop, so the gate acts once a root session's turn has succeeded: on a gap it
// sends the hold's message as a new prompt in that session, at most once per stop.
export default {
  id: "zloop",
  setup: async (ctx) => {
    const stop = new AbortController();
    // Sessions whose current turn a hold started: that turn ends unchecked, so a gap the agent cannot clear never loops.
    const held = new Set();
    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: stop.signal })) {
        if (event.type !== "session.execution.succeeded") continue;
        const sessionID = event.data.sessionID;
        if (held.delete(sessionID) || process.env.ZLOOP_YIELD_HOOK === "off") continue;
        let gate;
        try {
          const info = await ctx.session.get({ sessionID });
          // A subagent's child session is not held for its parent's gaps; the parent's own turn end answers for them.
          if (info.parentID) continue;
          gate = await check(info.location.directory, sessionID);
        } catch (error) {  // A broken check never passes silently.
          gate = {
            missing: [`could not check workers: ${error.message.trim()}`],
            reason: `Stopping now leaves these gaps:\n- could not check workers: ${error.message.trim()}\n`
              + "A check that could not run clears when it runs again: retry once it can, or run with "
              + "`ZLOOP_YIELD_HOOK=off` for this stop.",
          };
        }
        if (!gate.missing.length) continue;
        try {
          await ctx.session.prompt({ sessionID, text: gate.reason });
          held.add(sessionID);
        } catch {
          // The session or server is gone: there is no turn left to hold.
        }
      }
    })();

    // Log each Herdr agent launch for the gate before the shell tool runs it, as the Bash PreToolUse hook does on Claude
    // and Codex.
    const launches = await ctx.tool.hook("execute.before", async ({ tool, sessionID, input }) => {
      const command = input?.command;
      if (tool !== "shell" || !/herdr[\s\S]*agent\s+start/.test(command)) return;
      try {
        const info = await ctx.session.get({ sessionID });
        const logger = run("python3", [`${root}/hooks/herdr_launch.py`], { timeout: 10000 });
        logger.child.stdin.on("error", () => {});  // A logger that exits early must not take the server down.
        logger.child.stdin.end(JSON.stringify({ tool_input: { command }, cwd: info.location.directory, session_id: sessionID }));
        await logger;
      } catch {
        // The launch goes unlogged; the stop check still lists live agents nobody registered.
      }
    });
    return async () => {
      stop.abort();
      await launches.dispose();
    };
  },
};
