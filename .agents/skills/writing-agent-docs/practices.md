# Practices for agent docs

Contents:
- [Practices](#practices)
- [Sources](#sources)

## Practices

**Size every doc for one read.** A link names a file, and neither host can jump to
an anchor, so a doc should be small enough to read whole. Codex shows tool output
only up to its limit (`tool_output_token_limit`; 10k tokens for Codex models per
the maintainers, 8,000 on this machine) and drops the middle beyond it. Claude Code
shows a shell output over about 30 KB only as a saved file plus a preview, observed
in this repository's transcripts. Measured here before the host split: a quarter
of Codex reads of the large shared docs came back middle-truncated, and Claude agents
mostly read guessed line windows of them. 12 KiB is about 3k tokens, so two or three
docs fit one Codex read.

**Split by reader, then by linked section.** Separate run-time rules from setup,
migration, troubleshooting and experiments. A section that many files link becomes
its own file, so a link names the whole read. Keep entry points to one hop where
possible: skill → bootstrap and host session part → leaf.

**Link the file you mean.** Retarget a link when you move its text, and re-read the
sentence around it: a link that used to name a whole file must now name the leaf
that holds what the sentence promises. When one old link covers two leaves, link
both. Every leaf needs an inbound link from a skill, agent, job, adapter or folder index.

**Move text, don't rewrite it, when restructuring.** Move whole sections and verify
every old body line is still present. Then search the moved text for `above`,
`below`, `this file` and old file names; those break silently.

**Keep an agent file to what all its jobs share.** `agents/<name>.md` holds
permissions, principles and core skills only. Every line must hold for every job of
that agent; a rule that only some jobs need belongs in their
`skills/<command>/jobs/<job>.md`. End each principle with `shown by:` and the
observable evidence that it was followed. State rules directly, without identity
phrasing such as "you are" or "act as an expert". `tests/test_agent_definitions.py`
checks the structure.

**Keep host differences in adapters.** Shared docs state host-neutral rules; how a
host reads files, waits or launches workers belongs in `hosts/<host>/`: `session.md`
(read at session start) and `workers.md` (read when launching).

**Write skill frontmatter to the format.**
- `name`: at most 64 lowercase letters, digits and hyphens, without "anthropic" or "claude".
- `description`: at most 1,024 characters, no XML tags, third person. Say what it
  does and when to use it, front-loading the trigger words.
- Codex fits its initial skill list into 2% of the context window (8,000 characters
  when the window is unknown; `skills.max_context_tokens` raises it to at most
  10,000 tokens) and shortens descriptions first when over. Every installed skill
  shares that list, so keep each description short. `description_budget` in config
  is a local warning threshold, not a host limit.

**Write for a model that already knows the domain.** State only what it would not
know, with one term per concept: instruction-following declines as more
instructions are loaded at once (IFScale). Avoid time-bound wording, and use forward slashes.
Give one default with an escape hatch rather than a menu. Put deterministic checks
in scripts that agents run, not prose they interpret.

**Measure with real use.** Unit tests prove structure, not reading behavior. After
a batch of doc changes, observe how each host actually reads them: truncated
outputs, guessed line ranges, links not followed. Compare with the previous commit
on the same host.

## Sources

- [Anthropic: Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [OpenAI: Build skills for Codex](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI: Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [openai/codex #6426: token-based output truncation](https://github.com/openai/codex/issues/6426)
- [How Many Instructions Can LLMs Follow at Once? (IFScale)](https://arxiv.org/abs/2507.11538)
