# Managed run controls

Use managed controls for a verified merge grant, explicit pause provenance or an
external continuation supervisor. Existing cards remain legacy until explicitly adopted;
their declarations do not authorize automated recovery.

After the existing card is confirmed, adopt its current decisions:

```sh
python3 <plugin_root>/scripts/run_control.py adopt <card> --source <confirmation-reference>
```

Add `--allow-resume` only when the card already permits unattended allowance
recovery. Adoption records existing decisions. Open a new managed take-over to
request an automated merge grant. A `waiting-on-user` exit leaves the pending
transition and delivery evidence intact.
Inspect managed state with `run_control.py status <card>`. Before launch and after
completion or resume, follow [dispatch inspection](dispatch.md). Both inspections
use the control record when its Markdown projection is missing or matches the exact
view left by an interrupted publication. Inspection is read-only. Retrying the
control command republishes the Markdown and finishes the original decision.

The control record owns the run's grants and phase. For other mismatches, inspect
the saved record and the observed edits before reconciliation. Keep the control
record in place. Removing it to return the run to legacy mode is prohibited.
After deciding to restore the canonical view, use `restore-view <card>
--expect-view <reviewed-card-sha256> --source <review-reference> --request-id <id>`.
Supply the SHA-256 of the exact bytes reviewed. The command checks that hash before
it replaces the view and retains the displaced bytes in the private control record.
Supervisors must leave divergent views for explicit reconciliation.

For an actual user pause, use the bound host's native conversation record:

```sh
python3 <plugin_root>/scripts/run_control.py pause <card> \
  --runtime codex --native <absolute-native-record> --expect <revision> \
  --request-id <unique-id> --reason 'user took control'
```

Use the actual user command from the bound host's native record. A stop-hook
diagnostic is not user intent. The script checks provenance and ordering; unrestricted
filesystem access still prevents that record being a cryptographic authority.

## Verify human intent

For full-card confirmation or a take-over, run the gate with the intended card
and action. When it returns a scoped request, show the card, action, revision,
any proposed provider sessions and any merge target to the user. Ask them to
send the exact text from the gate, including the `zloop-intent:` line, in the
bound T3 conversation. Retry the same gate in the turn that handles their reply
and continue after the retry succeeds.

The gate verifies the loopback listener against the runtime record in the OS
account's protected T3 directory and reads with the inherited MCP credential.
It follows the T3 messages view across pages for the same active run. It requires
one fresh user item from the web or mobile app and binds its text to the card,
thread, action, revision and target. Copied transcripts and CLI projections
lack this authority. An unavailable binding, transport or complete read reports
`waiting-on-user`. The PR-only relay and confirmation by rule remain available
where the run card permits them.

Full confirmation records the verified conversation in `intent-thread:` and
adoption preserves it. Relay and by-rule setup can record the current T3
conversation for future verification. The `session:` list routes execution.
An older card without a conversation binding can still perform ordinary PR-only
work. A human take-over or grant requires the bound conversation.

For legacy take-over, run `python3 "<plugin_root>/scripts/autopilot_card.py"
take-over <card> [--effort <level>] [--dispatch-effort <level>]
[--review-effort <level>]`. It keeps the card's levels unless a level flag is
given. It moves the terminal state to `history:`, clears `terminal:` and its
previous merge authorization, and retains the phase.

For managed take-over use `resume <card> --expect <revision> --request-id <id>
--reason <reason>`. Add `--merge-pr <number>` to request a grant for an existing PR,
or `--merge-base <planned-base>` from the checked-out workstream branch. For an
existing PR, the challenge includes its current base. The gate verifies required
checks for the scoped base and rechecks the PR's destination before it records the
grant. A verified reply in the bound T3 thread can add a proposed provider session
while retaining the original routing session. Host decisions cannot override a
user pause. `autopilot_card.py phase` records validated phase changes through the
control record.

Host decisions use `end <card> --terminal <state> --source <evidence-reference>`
with the same expected revision, request ID and reason. A user pause still requires
the native command. Use existing delivery gates to establish delivery. A card with
`delivery: pr-only` refuses the `delivered-local` transition.

Submit through `merge <card> --expect <revision> --merge-pr <number>
--head <reviewed-sha> --method <approved-method>`. The verified grant binds the
origin repository, base, and PR or planned workstream branch. The command
rechecks the target and required checks, then verifies the open PR through the
authenticated local T3 host. It records the one-use submission under the control
lock before making one normal T3 merge call with the reviewed SHA and approved
method. The T3 call disables automatic and stacked merges.

The submission records a fresh T3 readback. To record terminal `merged`, read the
current revision and use `end --terminal merged`. The gate reads the PR again and
requires matching repository, number, base, head and merged state. A queue can
leave the PR open. The native call can enforce the head at submission, while the
base is verified by readbacks on either side of the call. On an uncertain result,
use readback to reconcile the existing submission. A new submission requires a
fresh grant.

While paused, collect already running work and preserve its evidence. Productive
continuation and acceptance wait for authorized resume.

For a due unattended continuation, use the [external supervisor](supervisor.md).
