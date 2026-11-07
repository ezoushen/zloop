# Installation

Requirements: Python **3.9+**, Git, Bash, and a Unix platform with `fcntl` (macOS/Linux).
The plugin needs no other skill package: its methods ship in this tree.

This is the maintained source tree. Package-manager cache copies are deployments;
make changes here. Development symlinks expose those changes without maintaining
separate copies of skills or scripts. Avoid enabling two zloop releases in the
same session.

## Claude Code

To try the source without replacing the installed plugin:

```sh
claude --plugin-dir /absolute/path/to/zloop
```

Development installation: `zloop@zloop-dev` is enabled, with
`~/.claude/plugins/cache/zloop-dev/zloop/0.3.0` symlinked to this
workspace. Edit here, then run `/reload-plugins` in an existing Claude session to
refresh the hook inventory and skill instructions; new sessions read the current
files. No reinstall is needed. A marketplace update to another version can select a
new cache directory; relink that entry when changing the installed version. After
changing the Claude manifest, check `claude plugin list` for loading errors before
reloading zloop.

## Codex

Codex's local catalog is `.agents/plugins/marketplace.json`:

```sh
codex plugin marketplace add /absolute/path/to/zloop
codex plugin add zloop@zloop-dev
```

The installer copies the marketplace folder into
`~/.codex/plugins/cache/zloop-dev/zloop/0.3.0`, ignored state included, and that copy
is what Codex loads: the SessionStart, PreToolUse and Stop hooks of
`hosts/codex/hooks.json`. Keep it a real copy. Codex fingerprints the installed folder to
trust its hooks and refuses a symlinked entry (`Hook failed: unsupported plugin entry
type: .../agents`), which disables every zloop hook. A hook's trust covers that
fingerprint, so a copy changed by hand reads `modified` and its hooks need review before
they run.

After each change to this checkout, run `codex plugin add zloop@zloop-dev` again and
start a new Codex session; existing conversations keep the hooks and instructions they
loaded. Reinstalling from this checkout kept the hooks already approved trusted; review
any native hook-trust prompt that appears. Check installation with
`codex plugin list --marketplace zloop-dev --json` and the
[native readers](development.md#native-checks). Also check runtime `skills/list` and
`hooks/list`: a listed installation alone does not prove that its skills or hook were
loaded. Its required presentation reference is packaged with the plugin.

## OpenCode

There is no zloop OpenCode bootstrap extension
([OpenCode settings](../hosts/opencode/session.md)); OpenCode loads the shared skills
through its native discovery and the yield gate's plugin from its plugins folder, so the
development installation is two links:

```sh
mkdir -p ~/.config/opencode/skills ~/.config/opencode/plugins
ln -s /absolute/path/to/zloop/skills ~/.config/opencode/skills/zloop
ln -s /absolute/path/to/zloop/hosts/opencode/plugin.js ~/.config/opencode/plugins/zloop.js
```

Keep an existing matching symlink; do not overwrite a different installation. Link
`skills/`, not the repository root, for the reason the Pi section gives: a directory
holding `SKILL.md` is one skill, and a grouping directory supplies only the ID, so the
commands stay flat under `zloop`. A top-level `*.md` at a skill source root would
itself become a skill, so the link must stay restricted to `skills/`. Retarget this
verified development symlink only when the checkout moves.

No reinstall and no service restart are needed: OpenCode watches its skill and
configuration sources, so edits here appear in a running session at its next model
step. Run `opencode reload` when a new link or skill directory does not appear, and
reserve `opencode service restart` for service troubleshooting. Confirm registration
with `opencode api skill.list`, which lists each skill with its `autoinvoke` flag: the
six model-invocable ids (diagnose, explain, housekeeping, prototype, research, tdd) and
the ten commands, loadable only by explicit id (`@align` and the like), should be
present. The native check is
`ZLOOP_NATIVE=1 .venv/bin/python -B tests/test_cross_host.py NativeHosts`.

The server log shows `loading plugin` for `zloop.js` once the plugin link is read. This
covers model settings, skill discovery and the yield gate; OpenCode still has no native
dispatch lifecycle ([workers](../hosts/opencode/workers.md)).

## Pi

Development installation uses two symlinks to this source tree:

```sh
mkdir -p ~/.pi/agent/skills ~/.pi/agent/extensions
ln -s /absolute/path/to/zloop/skills ~/.pi/agent/skills/zloop
ln -s /absolute/path/to/zloop/hosts/pi/bootstrap.ts ~/.pi/agent/extensions/zloop.ts
```

Keep an existing matching symlink; do not overwrite a different installation. For
the earlier link to this repository's root, retarget only that verified development
symlink to `skills/`. Pi discovers skills recursively but treats a folder holding
`SKILL.md` as one skill, so a command's other docs and jobs never load as skills;
restricting the link prevents optional add-on skills elsewhere in the repository from
loading. Re-point an extension symlink that does not target `hosts/pi/bootstrap.ts`.

Run `/reload` in Pi, then `/zloop:dispatch <scope> --autofix` or
`/zloop:review walkthrough <target>`. The extension supplies the bootstrap and Pi's
session part before each turn, registers the `zloop_wake` tool, and registers
`/zloop:<name>` for each command, completing its flags and modes and sending
`/skill:<name> <args>`. With explicit
extension loading disabled, supply `host=pi` when invoking a phase instead.
Planning/review can run without delegation where the shared procedure allows it;
dispatch requires the installed `subagent` tool (pi-subagents). See the
[Pi session part](../hosts/pi/session.md).

## Runtime references

[OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins),
[Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Claude plugin reference](https://code.claude.com/docs/en/plugins-reference),
[Claude hooks](https://code.claude.com/docs/en/hooks).
