# Workstream spend per accepted ticket

Use this read-only helper to compare workstreams by what they cost per accepted ticket
and to see where the coordinator's tokens went. It reads a workstream's lifecycle state
and the native transcripts that state names, needs only Python's standard library,
and emits JSON on stdout. It makes no model calls and changes nothing.

```sh
python3 /path/to/zloop/scripts/workstream_spend.py \
  --state /absolute/project/.scratch/<slug>/reports/workers.sqlite \
  --host /absolute/coordinator-transcript.jsonl \
  --since 2026-10-14T01:00:00+00:00 --until 2026-10-14T09:00:00+00:00
```

Workers come from the state: each registered attempt's `runtime` and
`native_session` find its Claude, Codex or Pi transcript, read whole. A Claude
worker's own Agent-tool children and every page of a Codex rollout count toward
that worker. The host does not register, so name its transcript with `--host`
(repeatable; the format is detected). `--host` requires `--since` and `--until`,
because a coordinator session often spans more than one workstream. Start the window
at the workstream's planning, not its first registration, to include preparation.
`--claude-root`, `--codex-root` and `--pi-root` override the default transcript
directories.

## What it reports

- `accepted_tickets`: assignments of an `acceptance: true` job (`ticket`) with an attempt recorded as
  [`accepted`](collect.md#accept-and-record-outcomes) and no `revert` attributed to it.
  A worker's `completed` result is its own claim; acceptance is the orchestrator's.
- `total` and `per_accepted_ticket`: worker and host tokens together, divided by the
  accepted tickets. With none, the ratio is `null` rather than a guess.
- `workers`: totals `by_assignment` and `by_job`. Failed attempts, repairs, reviews
  and E2E runs stay in the total; they are part of what an accepted ticket cost.
- `host`: totals `by_trigger` (`user`, `wake` for a lifecycle receipt or task
  notification, `yield-hook`) and `by_activity`, the first tool call of each response:
  `lifecycle:<action>`, `check`, `routing`, `herdr:<group> <command>`, `git:<subcommand>`,
  `read`, `edit`, `agent`, `command:<program>`, `tool:<name>`, or `reply` when the
  response called no tool. Groups are sorted by output tokens.
- `coverage.missing`: attempts whose transcript was not found, whose runtime has
  no reader (OpenCode), or whose Codex rollout has compressed (`.jsonl.zst`) pages,
  which are not read. Those tokens are absent, not estimated.
- `coverage.unreadable_lines`: damaged lines skipped, such as the partly written
  last line of a live transcript. Rerun after the writer settles.

Every figure counts `responses`, fresh `input`, `cache_write`, `cache_read` and
`output` separately; thinking is part of output.

## Interpretation

- Tokens are not a bill or a subscription allowance; the token kinds are priced
  differently. Compare workstreams kind by kind, with the same review standard, and
  alongside the quality guards: escaped defects, review defects, repair rounds,
  reverts, stalls and full gates. A cheaper workstream that accepts less or escapes more
  defects did less, not better.
- A response's activity says what it decided to do next, not everything it read.
  Its input is mostly the context it carried, so many small `read` or `herdr`
  responses in a long session can cost more than one large `edit`.
- Claude streams one response as several blocks; each response ID is counted once
  with its largest usage. Codex repeats token counts; only a growing total counts.
  Pi records usage on each assistant entry, including abandoned branches.
- Codex code mode runs commands from JavaScript; the first `exec_command` it issues
  names the activity, and other calls appear as `tool:<name>`, such as
  `tool:write_stdin` for polling a running command.
- A worker session reused by later attempts of the same assignment is counted once,
  under the first attempt's assignment and job.
