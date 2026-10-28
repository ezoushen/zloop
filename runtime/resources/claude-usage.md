# Recorded Claude usage

Use this read-only helper for a usage retrospective or an assignment's closure
evidence. It reads local Claude transcripts, needs only Python's standard library,
and emits JSON on stdout. It does not call a model or change jobs, hooks or account
configuration. Save output in the existing effort report directory when needed.

```sh
python3 /path/to/zloop/scripts/claude_usage.py \
  --since 2026-09-27T18:00:00+00:00 \
  --until 2026-09-28T01:00:00+00:00
```

Use explicit timezones and a fixed exclusive cutoff, particularly across midnight.
`--root /absolute/transcript-directory` overrides `~/.claude/projects`.
Families are sorted by output tokens; `hours` uses the start timestamp's offset.
Input, cache creation, cache reads, output and the thinking subset stay separate.

To attribute one parent and its native workers, add `--session <parent-UUID>`.
External SDK/hook sessions are separate transcript families. After confirming their
working directories, include exact paths with repeated `--related-worktree` options:

```sh
python3 /path/to/zloop/scripts/claude_usage.py \
  --since 2026-09-27T18:00:00+00:00 --until 2026-09-28T01:00:00+00:00 \
  --session <parent-UUID> --related-worktree /absolute/project/.worktrees/ticket
```

`total` counts the union once; `native_family` and `worktree_associated` are disjoint.
A cwd match establishes association only. Confirm the prompt/entrypoint separately
before calling a session a security review; neither relationship establishes which
account paid for it. Required security checks remain enabled.

## Interpretation and data quality

- Streaming assistant blocks share a response ID. The helper counts each ID once
  globally, retains the observed snapshot with greatest output and assigns it to
  its earliest timestamp. Updates at or after the cutoff are excluded. A response
  begun before the window stays outside it even if another block arrives inside.
- Each file is frozen at its byte length when opened. Selected source prefixes
  retain byte lengths and SHA-256 hashes for reproduction; the scan is not an
  atomic snapshot of all running sessions. No transcript bodies are exported.
- Inspect `quality` before comparing totals. Parse errors, missing usage/IDs,
  invalid timestamps/usage, decreasing counters and conflicting IDs are surfaced.
  Response counters cover the window across scanned families; parse/timestamp errors
  can also come from older history read for deduplication. A partially written tail
  can be retried after the writer settles.
  Cross-file duplicates need an attribution check even though totals deduplicate.
- `responses` measures recorded model responses, not HTTP attempts or user turns.
  Unlogged calls and other machines are outside coverage. Cached tokens have
  different accounting from fresh input; thinking is already included in output.
- `subscription_credits` is unavailable. Do not convert these counts to a weekly
  allowance percentage or a bill. Compare accepted completion, repairs and time
  alongside usage, and keep requested versus observed worker settings in the
  routing report. A long GPU wait alone does not establish model consumption.
