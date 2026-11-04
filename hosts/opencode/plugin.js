import { execFile } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const root = resolve(dirname(realpathSync(fileURLToPath(import.meta.url))), "../..");

// The yield gate's check for one session: its gaps, whether to hold the stop, and the hold's message, as the Stop hooks
// word it.
async function check(cwd, session) {
  const { stdout } = await run("python3", [`${root}/hooks/yield_gate.py`, "stop", cwd, session], { timeout: 20000 });
  return JSON.parse(stdout);
}

// The events that end a session's turn, one per busy period.
const ENDS = new Set(["session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"]);

// OpenCode has no hook that can refuse a stop, so the gate acts once a root session's turn has succeeded: while the check
// says to hold, it sends the hold's message as a new prompt in that session. The check bounds its own repeats.
export default {
  id: "zloop",
  setup: async (ctx) => {
    const stop = new AbortController();
    // When each held session's hold was admitted, and by which setup: a turn end published before then is an older turn
    // the hold already covers, and the end of the turn it starts is checked like any other. Shared by the plugin's
    // setups for every directory, so a session moved while held keeps its hold.
    const held = (globalThis[Symbol.for("zloop.opencode.held")] ??= new Map());
    // The sessions whose last check could not run: such a check holds once.
    const failed = (globalThis[Symbol.for("zloop.opencode.failed")] ??= new Set());
    const hold = (sessionID, at, claimed) => held.set(sessionID, { at, by: stop, claimed });
    // A setup releases only its own claim: another setup may hold the session by now.
    const release = (sessionID) => held.get(sessionID)?.by === stop && held.delete(sessionID);
    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: stop.signal })) {
        if (!ENDS.has(event.type)) continue;
        const sessionID = event.data.sessionID;
        let info;
        try {
          info = await ctx.session.get({ sessionID });
        } catch {
          continue;  // The session is gone: there is no turn left to hold.
        }
        if (stop.signal.aborted) break;  // Replaced while it looked: the replacement answers from here.
        // OpenCode sets the plugin up once per directory and sends each every session's events: only the session's own
        // directory answers for it.
        if (resolve(info.location.directory) !== resolve(ctx.location.directory)) continue;
        const entry = held.get(sessionID);
        // A turn end newer than the one a setup is still checking is checked in its own right.
        if (entry && !(entry.at === Infinity && (event.created ?? Infinity) > entry.claimed)) {
          if (entry.at === Infinity || (event.created ?? Infinity) < entry.at) {
            if ((event.created ?? Infinity) >= entry.at) held.delete(sessionID);
            continue;
          }
          held.delete(sessionID);  // The held turn's own end.
        }
        // A subagent's child session is not held for its parent's gaps; the parent's own turn end answers for them.
        if (event.type !== "session.execution.succeeded" || info.parentID || process.env.ZLOOP_YIELD_HOOK === "off") {
          continue;
        }
        hold(sessionID, Infinity, event.created);  // Claimed while checked, so a setup it moves to skips this turn end.
        let gate;
        try {
          gate = await check(info.location.directory, sessionID);
          failed.delete(sessionID);
        } catch (error) {  // A broken check never passes silently, but holds once.
          gate = {
            hold: !failed.has(sessionID),
            reason: `Stopping now leaves these gaps:\n- could not check workers: ${error.message.trim()}\n`
              + "A check that could not run clears when it runs again: retry once it can, or run with "
              + "`ZLOOP_YIELD_HOOK=off` for this stop.",
          };
          failed.add(sessionID);
        }
        if (stop.signal.aborted || !gate.hold) {
          release(sessionID);
          continue;
        }
        try {
          const sent = Date.now();
          const message = await ctx.session.prompt({ sessionID, text: gate.reason });
          // A setup replaced meanwhile records nothing: its replacement checks the held turn again.
          if (stop.signal.aborted) release(sessionID);
          else hold(sessionID, message?.time?.created ?? sent);
        } catch {
          release(sessionID);  // The session or server is gone: there is no turn left to hold.
        }
      }
    })();

    // Log each Herdr agent launch for the gate before the shell tool runs it, as the Bash PreToolUse hook does on Claude
    // and Codex.
    const launches = await ctx.tool.hook("execute.before", async ({ tool, sessionID, input }) => {
      const command = input?.command;
      if (tool !== "shell" || !command?.includes("herdr")) return;  // The logger finds each launch, however written.
      try {
        // A subagent is never held, so its launch goes under its root session, whose stop answers for it.
        let info = await ctx.session.get({ sessionID });
        for (const seen = new Set([info.id]); info.parentID && !seen.has(info.parentID); seen.add(info.id)) {
          info = await ctx.session.get({ sessionID: info.parentID });
        }
        const logger = run("python3", [`${root}/hooks/herdr_launch.py`], { timeout: 10000 });
        logger.child.stdin.on("error", () => {});  // A logger that exits early must not take the server down.
        logger.child.stdin.end(JSON.stringify({ tool_input: { command }, cwd: info.location.directory, session_id: info.id }));
        await logger;
      } catch {
        // The launch goes unlogged; the stop check still lists live agents nobody registered.
      }
    });
    return async () => {
      stop.abort();
      // The turns this setup held may end unseen once it is gone, so its replacement checks them again.
      for (const sessionID of held.keys()) release(sessionID);
      await launches.dispose();
    };
  },
};
