---
name: writing-agent-docs
description: Checks and guides changes to the agent-facing Markdown and the module architecture in this repository (skills, agents, jobs, runtime docs, hosts, scripts and hooks). Use when adding, splitting, moving or editing these docs, their links or the scripts' imports, and before committing such changes.
---

# Writing agent docs

Agents load these files at run time, so their size, links and wording are behavior.
Run the checker on every change, then apply the [practices](practices.md) to what it reports.

Contents:
- [Run the checker](#run-the-checker)
- [Fix what it reports](#fix-what-it-reports)
- [Extend the architecture](#extend-the-architecture)

## Run the checker

```sh
python3 .agents/skills/writing-agent-docs/check_docs.py --changed main   # while changing docs
python3 .agents/skills/writing-agent-docs/check_docs.py                  # whole repository
```

Errors exit 1, invalid input (config, base or repository) exits 2; warnings are
smells to judge. `--changed <base>` limits per-file rules to files changed since that
base, plus untracked files, and flags references such as `the example above` on
added lines; renamed files keep their old lines. Links, anchors, orphans and code paths are always checked repo-wide,
because moving a file breaks the files that link it. `--json` prints findings for
scripts. Three hooks run it, so the architecture rules hold all the time: in Claude
Code, [edit_check.py](edit_check.py) runs after every Edit or Write
(`.claude/settings.json`) and reports the architecture errors the tree now has; the
pre-commit hook runs it with `--changed HEAD`; the pre-push hook runs the
whole-repository check on each pushed commit, as does `tests/test_cross_host.py`.
Other hosts rely on the Git hooks: enable them once per clone with
`git config core.hooksPath .githooks`.

[config.json](config.json) says which files are agent docs, which files' links
count, where code names doc paths, the byte budget, the recorded debt and the `glossary` whose
`_Avoid_` phrases agent docs must not use.
[architecture.json](architecture.json) holds the module architecture: ordered levels
(top may depend on lower ones and on itself, never on a sibling or a higher level),
which files each module owns, the first-read budget and the debt for that contract.
Debt entries only shrink: one entry covers every link between its two files, a violation
between any other pair is an error, and a fixed one is `stale-debt`.
A doc, or a script or hook under `owned_code`, that no module owns is `unmapped`, so a new
folder needs a module before its files pass.

The same levels apply to the imports of `scripts/` and `hooks/`, including imports
inside functions (`import-direction`, also in `tests/test_import_direction.py`). A doc
path named in text counts like a link, and one named in code counts from the code's
module, except for a module injected by path (`unlinked`). To prove a restructuring lost nothing
run `check_fold.py --base <commit>`: it reports every body line (link targets ignored)
and every skill, agent, job, kind, decision family, hook event and script command that
differs from the base; list deliberate edits in a JSON file passed with `--allow`.

## Fix what it reports

| Code | Meaning | Fix |
|---|---|---|
| `size` | Doc over 12 KiB, README and `docs/` included | Split it by reader, not by line count |
| `debt` | Over budget or missing contents, recorded in config | Shrink it when you touch it; lower the recorded size when the message says so; never raise it to make room |
| `stale-debt` | A recorded debt no longer applies | Remove the entry |
| `contents` | Over 100 lines with 3+ sections, no exact `Contents:` list | Add or update the list right after the title |
| `skill-lines` | `SKILL.md` body over 500 lines | Move detail into files it links |
| `frontmatter` | Name or description outside the skill format | Fix the field |
| `link`, `anchor` | Target file or heading missing | Retarget to the file and section that hold the text |
| `orphan` | No doc or code links it | Link it where a reader needs it, or delete it |
| `code-path` | Code names a doc that does not exist | Update the path in the code |
| `direction` | A link, or a doc path named in text or code, goes to a higher level, a sibling module or another project skill | Move the shared text into a lower module its users can link, or invert the link |
| `import-direction` | A script or hook imports, or names the path of, one in a higher or sibling module | Move the shared code into a lower module |
| `cycle` | Modules link each other in a loop | Break the loop at the link `direction` also reports |
| `first-read` | Bootstrap, host session, skill and the docs its `First read:` paragraph links, over the budget | Move detail out of those docs or the bootstrap |
| `first-read-line` | A `SKILL.md` has no line starting `First read:` | Add a paragraph starting `First read:` that links the docs the skill reads before acting |
| `entry-doc` | A skill's folder is not its own module, or its `First read:` paragraph links a `<skill>.md` instead of the skill holding its procedure | Put the procedure in its `SKILL.md` and map `skills/<skill>/` as a module; a doc agents need without a trigger belongs in `runtime/` or `standards/`, not in a wrapper skill |
| `host-shape` | A folder under `hosts/` that does not hold exactly `session.md` and `workers.md` | Move other text to the runtime doc that owns it |
| `shared-section` | A section, other than a file's title or only section, linked from 3+ files in other modules | Make it its own file, or link the whole file |
| `unmapped` | No module in architecture.json owns the doc | Add its path to the owning module |
| `unowned` | An agent doc links or names a Markdown file no module owns, such as one in `docs/` | Move that file into the module that owns its subject |
| `positional` | `above` or `below` that may point at moved text | Link or name the target instead |
| `stale-name` | Text names a `.md` file that exists nowhere | Link the doc that now holds it, or add a generic project name to `known_names` |
| `avoided-word` | An agent doc uses a phrase the glossary lists under `_Avoid_`, as a whole phrase in any case, plural or line wrap, in frontmatter and link titles too | Write the term the error names; quote a phrase you must name in backticks or a fenced block (a link target is not a use, a link title is). A phrase that is also a glossary term (`effort`) is not matched alone: the glossary lists the longer phrase to avoid (`effort branch`) |
| `dated`, `backslash`, `person` | Time-bound wording, Windows path, first/second-person description | Reword |
| `description-budget` | Skill descriptions over the local budget in config | Shorten descriptions |

## Extend the architecture

Layers, top first: this repository's project skills (`.agents/skills/`), hooks and
`bootstrap.md` (injected by path, so skills name it rather than link it); skills
(`skills/<skill>/`, in their own order);
`agents/`; `standards/`; `hosts/`; `runtime/` modules. Skills are what the user types or the
agent loads; the layers under them are internal, and nothing below the skills links one. A doc
or script depends on its own module or a lower level, never on a sibling or a higher level: a
skill may read a lower skill's procedure, and a project skill links no other skill. No two modules depend on each other,
directly or through others (`cycle`). **Strict:** these direction rules, the first-read budget, the host
shape, shared sections and unmapped docs are errors with no recorded debt. **Room:** a new module
joins `architecture.json` in a level of its own or beside siblings that need not link
each other, so the order grows without loosening a rule. Run the checker and
`tests/run_all.py` after each addition.

- **Add a host:** create `hosts/<host>/` holding exactly `session.md` (read at session
  start) and `workers.md` (read when launching); add the name to `HOSTS` in
  `scripts/agent_models.py`, register its session hint in the bootstrap or hook, and
  never link the host files from shared docs (the `hosts` module is `unlinked`).
- **Add an executor:** write `hosts/executors/<type>.md` and list it in that folder's
  README; shared docs name an executor only through the configuration.
- **Add an agent:** write `agents/<name>.md` with permissions, principles ending in
  `shown by:` and core skills; the agent holds only what every one of its jobs shares.
- **Add a job:** write `skills/<command>/jobs/<job>.md` naming its agent, inputs, finish
  line and return; the name is unique across commands. Detail only some jobs need
  belongs here, not in the agent.
- **Add a kind:** write `standards/kinds/<kind>.md` and link it from the agent or
  job that applies it.
- **Add a standard:** write `standards/<name>.md`, mapped to the `standards` module,
  and link it from the agents and commands that follow it.
- **Add a runtime module:** text several commands or agents share and none owns goes in
  `runtime/<module>/`, named with a noun (`board`, `interview`, `checks`), mapped in
  `architecture.json` at a level below every module that reads it; a procedure commands
  share is also named in the pipeline map (`skills/brief/pipeline.md`).
- **Add a command:** create `skills/<command>/SKILL.md` holding its procedure, after a
  `First read:` paragraph naming the bootstrap and host session part, with its other
  docs and `jobs/` beside it. Map `skills/<command>/**/*.md` as a module at its level
  (`entry-doc`), name it in the pipeline map and add it to the README's command table.
  A skill the agent loads itself by its trigger does the same and sets
  `user-invocable: false`; add one only when its trigger must fire outside the commands
  (as `housekeeping` does). A skill both the user types and the agent loads by its trigger
  (`diagnose`, `research`, `prototype`, `explain`, `tdd`) sets neither that nor
  `disable-model-invocation`, and `allow_implicit_invocation: true` in `agents/openai.yaml`;
  its description is its trigger. Text agents read by link, or a runtime doc needs, is a
  `runtime/` module or a standard, named in an agent's `core_skills` when every job needs it;
  a procedure the user may also run alone is a skill at a lower level than those that read it.
  A skill that launches nothing (`huh`, `explain`) skips the bootstrap; `huh`, which only
  re-pitches the conversation, also skips the procedure size floor; keep its trigger short.
