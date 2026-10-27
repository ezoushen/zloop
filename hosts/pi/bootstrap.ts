import { execFile, execFileSync } from "node:child_process";
import { readdirSync, readFileSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const SECTION = "zloop_harness";
// Pi's bash has no default timeout, so one forgotten search of / can hold a session for hours. Claude Code and
// OpenCode stop a foreground command after 2 minutes; this gives Pi the same bound unless
// ZLOOP_BASH_TIMEOUT=off.
const BASH_TIMEOUT_SECONDS = 120;
const bashBounded = () => process.env.ZLOOP_BASH_TIMEOUT !== "off";

// setTimeout rejects a delay above 2^31 - 1 ms, about 24.8 days.
const WAKE_MAX_MS = 2 ** 31 - 1;

// The panel: this session's effort drawn below the editor, from the rows its fact source reports.
const PANEL_KEY = "zloop";
const PANEL_INTERVAL_MS = 2000;
const execFileAsync = promisify(execFile);

/** A character the terminal draws in two cells: East Asian wide and fullwidth blocks, and emoji presentation. */
const WIDE = /[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\u{1F000}-\u{1FAFF}\u{20000}-\u{2FFFD}]/u;
/** Characters that take no cell of their own: variation selectors and zero-width marks. */
const ZERO = /[\u200B-\u200F\u2060\uFE0E\uFE0F]/u;

/** The cells `character` occupies. */
const cellsOf = (character) => {
  if (ZERO.test(character)) return 0;
  return WIDE.test(character) ? 2 : 1;
};

/** `text` cut to `width` terminal cells, ended with an ellipsis when anything was dropped. */
export function truncateCells(text, width) {
  if (width <= 0) return "";
  let out = "";
  let used = 0;
  for (const character of text) {
    const cells = cellsOf(character);
    if (used + cells > width) {
      while (used > width - 1 && out) {
        const dropped = [...out].pop();
        out = out.slice(0, -dropped.length);
        used -= cellsOf(dropped);
      }
      return `${out}…`;
    }
    out += character;
    used += cells;
  }
  return out;
}

/** The rows a snapshot draws; a live effort's rows, else nothing. */
export function panelRows(found) {
  if (!found || !found.active || !Array.isArray(found.rows)) return [];
  return found.rows.filter((row) => row && typeof row.text === "string" && row.text);
}

/** The theme tone each row kind takes; a run row keeps the default text style. */
const TONES = {
  headline: { token: "accent", bold: true },
  wait: { token: "warning" },
  blocker: { token: "error" },
  next: { token: "muted" },
  counts: { token: "dim" },
  more: { token: "dim" },
};

/** `text` in its kind's tone: styled after truncation, so styling never changes the cells it occupies. */
function toned(theme, kind, text) {
  const tone = TONES[kind];
  if (!tone) return text;
  return tone.bold ? theme.style(text, { fg: tone.token, bold: true }) : theme.fg(tone.token, text);
}

/** What the last tick drew and on which context, so an unchanged snapshot repaints nothing. */
let drawnPanel = null;
let drawnCtx = null;

/**
 * Draw one snapshot: the rows below the editor while the effort is live, else neither surface. The card carries every
 * fact, so the status line stays with the engine. A snapshot that could not be read clears both. A stale context drops
 * the tick and clears the repaint memory; the timer keeps the context it was started with, so a replaced session ends
 * this panel until its own session start arms a new one.
 */
export async function panelTick(ctx, found) {
  try {
    const rows = panelRows(found);
    const drawn = JSON.stringify(rows.map((row) => row.text));
    if (ctx === drawnCtx && drawn === drawnPanel) return;
    drawnCtx = ctx;
    drawnPanel = drawn;
    if (rows.length === 0) {
      ctx.ui.setWidget(PANEL_KEY, undefined);
      ctx.ui.setStatus(PANEL_KEY, undefined);
      return;
    }
    ctx.ui.setWidget(PANEL_KEY, (_tui, theme) => ({
      render(width) {
        return rows.map((row) => toned(theme, row.kind, truncateCells(row.text, width)));
      },
      invalidate() {},
    }), { placement: "belowEditor" });
  } catch {
    // The context belongs to a replaced session; the next tick draws on the fresh one.
    drawnCtx = null;
    drawnPanel = null;
  }
}

/** Read this session's snapshot: the parsed answer, or null when the fact source fails or prints something else. */
async function readPanelSnapshot(root, repo, session) {
  try {
    const { stdout } = await execFileAsync("python3",
      ["-B", join(root, "scripts", "panel_state.py"), "--repo", repo, "--session-id", session, "--json"],
      { encoding: "utf8", timeout: 8000 });
    return JSON.parse(stdout);
  } catch {
    return null;
  }
}

// The top-level scalar fields of a SKILL.md's frontmatter: unquoted, and a > or | block's indented lines joined.
function frontmatter(path) {
  const block = readFileSync(path, "utf8").replace(/\r\n/g, "\n").match(/^---\n([\s\S]*?)\n---/);
  const fields = {};
  let open = null;
  for (const line of block ? block[1].split("\n") : []) {
    const field = line.match(/^([a-z-]+):\s*(.*)$/);
    if (field) {
      open = /^[>|][+-]?$/.test(field[2]) ? field[1] : null;
      fields[field[1]] = open ? "" : field[2].replace(/^"(.*)"$|^'(.*)'$/, "$1$2");
    } else if (open && /^\s/.test(line)) {
      fields[open] = `${fields[open]} ${line.trim()}`.trim();
    }
  }
  return fields;
}

// An argument-hint as slots, one per argument the hint writes: the literal words it may be (a placeholder in <> is
// typed by the user, not completed; one in quotes takes only a quoted word), the alternatives it sits in (branches
// split by a spaced |), and its anchor: the flag or mode it is a value of when it directly follows one, or the one it
// may only come after when a placeholder stands between them. Text after " — " is a comment.
function hintSlots(hint) {
  const tokens = hint.split(" — ")[0].match(/\[|\]|(?:[^\s[\]<]|<[^>]*>)+/g) ?? [];
  const slots = [];
  let at = 0;
  let groups = 0;
  const slot = (word, path) => {
    const made = { literals: [], placeholder: false, path };
    let key = "";
    for (const part of word.match(/(?:[^|<]|<[^>]*>)+/g)) {
      if (part.startsWith("<") || /^"<[^>]*>"$/.test(part)) {
        made.placeholder = true;
        made.quoted = part.startsWith('"');
        continue;
      }
      const literal = part.includes("<") ? part.slice(0, part.indexOf("<")) : part;
      key = literal.match(/^[^=]+=/)?.[0] ?? key;
      made.literals.push(literal.includes("=") ? literal : key + literal);
    }
    slots.push(made);
    return made;
  };
  const leading = (item) => item.slot ? [item.slot] : item.branches.flatMap((branch) => branch[0] ? leading(branch[0]) : []);
  const sequence = (path) => {
    const items = [];
    while (at < tokens.length && tokens[at] !== "]" && tokens[at] !== "|") {
      const token = tokens[at++];
      items.push(token === "[" ? { branches: branches(path) } : { slot: slot(token, path) });
      if (token === "[") at++;  // its "]"
    }
    items.forEach((item, index) => {
      const before = items[index - 1]?.slot;
      if (before?.literals.length && !before.placeholder) {
        for (const follower of leading(item)) Object.assign(follower, { after: before, optional: !item.slot });
      } else if (before?.placeholder && !before.literals.length && items[index - 2]?.slot?.literals.length) {
        for (const follower of leading(item)) follower.afterSome = items[index - 2].slot;
      }
    });
    return items;
  };
  const branches = (path) => {
    const id = groups++;
    const found = [];
    do found.push(sequence([...path, [id, found.length]])); while (tokens[at] === "|" && ++at);
    return found;
  };
  branches([]);
  return slots;
}

// Completions for the arguments typed so far: the words the slots allow next, each replacing what is typed.
function completeHint(hint, slots, typed) {
  // Words as a shell splits them: a quoted argument is one word, and the last word is still being typed.
  const words = typed.match(/"[^"]*"?|'[^']*'?|\S+/g) ?? [];
  const partial = !typed || /\s$/.test(typed) ? "" : words.pop();
  // A slot already given, or one in another branch of alternatives a given slot sits in, is not offered.
  const given = [];
  const excluded = (candidate) => given.some((other) => other === candidate || other.path.some(([group, branch]) =>
    candidate.path.some(([candidateGroup, candidateBranch]) => group === candidateGroup && branch !== candidateBranch)));
  // A positional slot, a mode word or free text rather than a flag, a key= argument or a value, keeps the hint's order.
  const positional = (candidate) => !candidate.after && !candidate.afterSome
    && (candidate.placeholder || candidate.literals.some((each) => !each.startsWith("-") && !each.includes("=")));
  // Whether the hint allows a slot at this point: not given or ruled out, right after its flag or mode if it is a
  // value, after the flag it needs if a placeholder stands between them, and a mode before any later positional one.
  let last = null;
  const allowed = (candidate) => !excluded(candidate) && (!candidate.after || candidate.after === last)
    && (!candidate.afterSome || given.includes(candidate.afterSome))
    && (!positional(candidate) || given.every((other) => !positional(other)
      || slots.indexOf(other) < slots.indexOf(candidate)));
  // Read the typed words in order: a literal the hint allows here (a key= one with its value) gives its slot; the word
  // after a flag that takes a typed value is that value; any other word fills the hint's next open placeholder, and
  // the words after it continue that free text until a literal; a quoted word is a whole value.
  let filling = false;
  for (const word of words) {
    const quoted = /^(["']).*\1$/s.test(word);
    const value = last && slots.find((candidate) => candidate.after === last && !candidate.optional
      && candidate.placeholder && !candidate.literals.length);
    const literal = !value && !quoted && slots.find((candidate) => allowed(candidate) && candidate.literals.some((each) =>
      each === word || (each.endsWith("=") && word.startsWith(each))));
    const placeholder = !value && !literal && !filling && slots.find((candidate) => candidate.placeholder
      && !candidate.after && !candidate.afterSome && !excluded(candidate) && (quoted || !candidate.quoted));
    const slot = value || literal || placeholder;
    if (slot) given.push(slot);
    filling = !value && !literal && !quoted;
    last = literal || null;
  }
  // Next comes the value of the last flag or mode when it requires one, else its optional values and the rest.
  const followers = last ? slots.filter((candidate) => candidate.after === last) : [];
  const next = followers.some((follower) => !follower.optional) ? followers
    : [...followers, ...slots.filter((candidate) => !candidate.after)];
  const before = typed.slice(0, typed.length - partial.length);
  const items = next.filter(allowed).flatMap((candidate) => candidate.literals)
    .filter((literal) => literal.startsWith(partial))
    .map((literal) => ({ value: `${before}${literal}${literal.endsWith("=") ? "" : " "}`, label: literal, description: hint }));
  return items.length ? items : null;
}

// The skills a user types, as Pi commands: each a skill's name and its frontmatter.
function userSkills(root) {
  return readdirSync(join(root, "skills"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => frontmatter(join(root, "skills", entry.name, "SKILL.md")))
    .filter((fields) => fields.name && fields["user-invocable"] !== "false");
}

export default function (pi) {
  const root = resolve(dirname(realpathSync(fileURLToPath(import.meta.url))), "../..");
  pi.on("before_agent_start", (event) => {
    const herdr = process.env.HERDR_ENV === "1"
      ? ` Herdr environment detected: before delegation also read ${root}/hosts/herdr.md for routing, waits and cleanup.`
      : "";
    const hint = `zloop harness: pi. When using a zloop skill, first read ${root}/bootstrap.md and ${root}/hosts/pi/session.md. Plugin root: ${root}. Load only this host session part; pass its absolute path and the agent and job files to workers.${bashBounded() ? ` Shell commands given no timeout stop after ${BASH_TIMEOUT_SECONDS} s: pass a timeout for a longer foreground command, or run it in the background.` : ""}${herdr}`;
    // Returning systemPrompt forces the prompt for this run only, so runs that
    // start without a typed prompt revert to the structured one and every switch
    // invalidates the server's prefix cache. A section is diffed by Pi instead.
    const sections = event.systemPromptOptions?.sections;
    if (sections) {
      sections[SECTION] = hint;
      return undefined;
    }
    return { systemPrompt: `${event.systemPrompt}\n\n${hint}` };
  });

  // Claude Code names a skill the user types /zloop:<skill>; Pi gets the same command, which types the skill command
  // Pi expands. Extension commands run even while Pi works, so the skill then waits for the current run.
  for (const skill of userSkills(root)) {
    const hint = skill["argument-hint"] ?? "";
    const slots = hintSlots(hint);
    pi.registerCommand(`zloop:${skill.name}`, {
      description: skill.description,
      getArgumentCompletions: (typed) => completeHint(hint, slots, typed),
      handler: async (args) => {
        pi.sendUserMessage(`/skill:${skill.name}${args.trim() ? ` ${args.trim()}` : ""}`,
          { deliverAs: "followUp", expandPromptTemplates: true });
      },
    });
  }

  // Pi has no scheduler of its own. A run that must continue later, such as autopilot after an allowance pause, sets
  // a timer that types its resume command back into this session; the timer lasts as long as the Pi process does.
  const wakes = new Map();
  let wakeSerial = 0;
  pi.registerTool({
    name: "zloop_wake",
    label: "Wake",
    description: "Type a command back into this session at a later time, for example `/skill:autopilot --take-over`, "
      + "or cancel an earlier timer. The timer is lost if Pi exits first.",
    parameters: {
      type: "object",
      properties: {
        at: { type: "string", description: "When to run the command, as an ISO 8601 time with its offset." },
        command: { type: "string", description: "The text to send, such as a `/skill:` command." },
        cancel: { type: "string", description: "The id of a timer to cancel instead of setting one." },
      },
    },
    async execute(_toolCallId, params) {
      const reply = (text) => ({ content: [{ type: "text", text }], details: {} });
      if (params.cancel !== undefined) {
        const timer = wakes.get(params.cancel);
        if (!timer) throw new Error(`no timer ${params.cancel}`);
        clearTimeout(timer);
        wakes.delete(params.cancel);
        return reply(`cancelled timer ${params.cancel}`);
      }
      const command = typeof params.command === "string" ? params.command.trim() : "";
      if (!command) throw new Error("zloop_wake needs a command to send");
      const due = Date.parse(params.at);
      if (Number.isNaN(due)) throw new Error("`at` must be an ISO 8601 time with its offset");
      const delay = due - Date.now();
      if (delay <= 0) throw new Error("`at` is in the past");
      if (delay > WAKE_MAX_MS) throw new Error("`at` is more than 24 days away");
      const id = String(++wakeSerial);
      // unref: a headless run that ends must not wait hours for its timer.
      wakes.set(id, setTimeout(() => {
        wakes.delete(id);
        try {
          pi.sendUserMessage(command, { deliverAs: "followUp", expandPromptTemplates: true });
        } catch {
          // Pi replaced this extension (/reload, a new session) and invalidated its API: the timer's session is gone.
        }
      }, delay).unref());
      return reply(`timer ${id} will send \`${command}\` at ${new Date(due).toISOString()}`);
    },
  });

  // Keep working while a lifecycle worker could finish without waking this host, a worker this session launched is
  // unregistered, or an effort it coordinates is undelivered under autofix: at most once per run, so a gap the host
  // cannot fix never loops.
  let continued = false;
  pi.on("agent_settled", () => {
    continued = false;
  });
  pi.on("agent_before_settle", (event, ctx) => {
    if (continued || event.outcome !== "completed" || process.env.ZLOOP_YIELD_HOOK === "off") return undefined;
    // pi-subagents hosts background children, which load this extension, in a runner it marks PI_SUBAGENT_CHILD=1;
    // a child must not stop for its coordinator's gaps. (A /fork sets the header's parentSession too: no signal.)
    if (process.env.PI_SUBAGENT_CHILD === "1") return undefined;
    let missing, session, content;
    try {
      session = ctx.sessionManager.getSessionId();
      const output = execFileSync("python3", [`${root}/hooks/yield_gate.py`, "check", ctx.cwd, session],
        { encoding: "utf8", timeout: 20000 });
      ({ missing, reason: content } = JSON.parse(output));  // The check words the hold as the Stop hooks do.
    } catch (error) {
      missing = [`could not check workers: ${error.message.trim()}`];  // A broken check never passes silently.
    }
    if (!missing.length) return undefined;
    continued = true;
    content ??= "Stopping now leaves these gaps:\n"
      + missing.map((gap) => `- ${gap}\n`).join("")
      + "Register each launched worker, or dismiss a launch that needs none with "
      + `\`python3 ${root}/hooks/herdr_launch.py dismiss --pane <pane> --session ${session} --reason <why>\` `
      + `(${root}/runtime/workers/prepare.md). Arm a notified wait, recover the receipt or collect the result `
      + "(for a worker that is gone, a failed or cancelled result whose report says what was lost) "
      + `before stopping (${root}/runtime/workers/wait.md).`;
    return { entries: [{ type: "custom_message", customType: "zloop_yield_gate", content, display: true }], continue: true };
  });

  // First, so every later handler and the tool_result see the bounded input.
  pi.on("tool_call", (event) => {
    if (bashBounded() && event.toolName === "bash" && event.input && event.input.timeout === undefined) {
      event.input.timeout = BASH_TIMEOUT_SECONDS;
    }
    return undefined;
  });

  // Loop guard: a call that returned the same result three times in a row is blocked when repeated,
  // and a second blocked repeat stops the turn. Polling whose output changes is never blocked.
  const REPEATS = 3;
  let last = { call: "", output: "", streak: 0, blocked: 0 };
  const guarded = new Set();
  pi.on("tool_call", (event) => {
    if (process.env.ZLOOP_LOOP_GUARD === "off") return undefined;
    const call = `${event.toolName}\u0000${JSON.stringify(event.input)}`;
    if (call !== last.call || last.streak < REPEATS) return undefined;
    last.blocked += 1;
    guarded.add(event.toolCallId);
    return {
      block: true,
      terminate: last.blocked > 1,
      reason: `This exact ${event.toolName} call returned the same result ${last.streak} times in a row. `
        + "Repeating it will not change anything: use the output you have, try a different approach, "
        + "or report that you are blocked and why.",
    };
  });
  pi.on("tool_result", (event) => {
    if (guarded.delete(event.toolCallId)) return undefined;
    const call = `${event.toolName}\u0000${JSON.stringify(event.input)}`;
    const output = JSON.stringify([event.content, event.isError]);
    last = call === last.call && output === last.output
      ? { ...last, streak: last.streak + 1 }
      : { call, output, streak: 1, blocked: 0 };
    return undefined;
  });

  // Log each Herdr agent launch for the yield gate, as the Bash PreToolUse hook does on Claude and Codex.
  // A failing tool_call handler blocks the tool, so logging catches everything and never blocks.
  pi.on("tool_call", (event, ctx) => {
    const command = event.input?.command;
    if (!["bash", "bg_run"].includes(event.toolName) || !/herdr[\s\S]*agent\s+start/.test(command)) return undefined;
    try {
      execFileSync("python3", [`${root}/hooks/herdr_launch.py`], {
        input: JSON.stringify({ tool_input: { command }, cwd: ctx.cwd, session_id: ctx.sessionManager.getSessionId() }),
        timeout: 10000,
      });
    } catch {
      // The launch goes unlogged; the stop check still lists live agents nobody registered.
    }
    return undefined;
  });

  // The panel: the effort's rows under the editor while it is live, refreshed from the fact source.
  let panelTimer;
  pi.on("session_start", (_event, ctx) => {
    if (!ctx.hasUI || panelTimer || process.env.ZLOOP_PANEL === "off") return undefined;
    const session = ctx.sessionManager.getSessionId();
    const repo = ctx.cwd;
    panelTimer = setInterval(() => {
      readPanelSnapshot(root, repo, session).catch(() => null).then((found) => panelTick(ctx, found));
    }, PANEL_INTERVAL_MS);
    panelTimer.unref?.();
    return undefined;
  });
  pi.on("session_shutdown", () => {
    if (panelTimer) clearInterval(panelTimer);
    panelTimer = undefined;
    return undefined;
  });
}
