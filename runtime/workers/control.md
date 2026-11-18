# Managed run controls

Use managed controls when a run needs explicit pause provenance or an external
continuation supervisor. Existing cards remain legacy until explicitly adopted;
their declarations do not authorize automated recovery.

After the existing card is confirmed, adopt its current decisions:

```sh
python3 <plugin_root>/scripts/run_control.py adopt <card> --source <confirmation-reference>
```

Add `--allow-resume` only when its confirmed grants already permit unattended
allowance recovery. Adoption grants no extra permissions or merge authority.
The sibling `autopilot-control.json` retains run identity, revision, source events
and the card view. `status <card>` reports managed state; absent records are legacy.
Manual edits or interrupted view updates become unknown and block managed work.
Inspect the saved record and edits before recovery; never infer authorization from
a mismatched card or delete the record to make it legacy.
After deciding to restore the canonical view, use `restore-view <card>
--expect-view <reviewed-card-sha256> --source <review-reference> --request-id <id>`.
It refuses newer edits and retains the displaced view in the private control record.
This is an explicit reconciliation, never a supervisor action.

For an actual user pause, use the bound host's native conversation record:

```sh
python3 <plugin_root>/scripts/run_control.py pause <card> \
  --runtime codex --native <absolute-native-record> --expect <revision> \
  --request-id <unique-id> --reason 'user took control'
```

The record must contain a user-role `autopilot --pause` command after adoption,
optionally prefixed with the host's slash/skill syntax. Tool output, stop-hook
diagnostics, another session and consumed commands cannot establish the pause.
The native file is evidence, not a cryptographic authority against unrestricted
filesystem access. Unavailable timestamps or unsupported exports remain gaps.

`resume` uses the same shape and an actual `autopilot --take-over` user command.
It preserves effort levels; only an explicit `--auto-merge` in that native command
grants merging for the resumed run. Other grant/criteria changes require a new
confirmed run. Managed cards use this command instead of the legacy take-over.
`autopilot_card.py phase` records validated phase changes through the control record.

Host decisions use `end <card> --terminal <state> --source <evidence-reference>`
with the same expected revision, request ID and reason. A user pause still requires
the native command; the host cannot label a diagnostic as user intent. Existing
delivery gates still judge delivered states. Repeated identical request IDs
converge; a changed request or stale revision is refused.

Managed paused, ended and unknown runs cannot register or submit new workers or
accept their results. Already running workers may finish and be collected;
acceptance waits for an authorized resume.

For a due unattended continuation, use the [external supervisor](supervisor.md).
