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
Inspect managed state with `run_control.py status <card>`. Before launch and after
completion or resume, follow [dispatch inspection](dispatch.md). Both inspections
use the control record when its Markdown projection is missing or matches the exact
view left by an interrupted publication. Inspection is read-only. Retrying the
control command republishes the Markdown and finishes the original decision.

The control record owns the run's grants and phase. For other mismatches, inspect
the saved record and the observed edits before reconciliation.
After deciding to restore the canonical view, use `restore-view <card>
--expect-view <reviewed-card-sha256> --source <review-reference> --request-id <id>`.
Supply the SHA-256 of the exact bytes reviewed. The command checks that hash before
it replaces the view and retains the displaced bytes in the private control record.

For an actual user pause, use the bound host's native conversation record:

```sh
python3 <plugin_root>/scripts/run_control.py pause <card> \
  --runtime codex --native <absolute-native-record> --expect <revision> \
  --request-id <unique-id> --reason 'user took control'
```

Use the actual user command from the bound host's native record. A stop-hook
diagnostic is not user intent. The script checks provenance and ordering; unrestricted
filesystem access still prevents that record being a cryptographic authority.

`resume` uses the same arguments and an actual `autopilot --take-over` user command.
Host decisions cannot override a user pause. Grant/criteria changes require the
existing explicit authorization path; a receipt cannot grant merging.
`autopilot_card.py phase` records validated phase changes through the control record.

Host decisions use `end <card> --terminal <state> --source <evidence-reference>`
with the same expected revision, request ID and reason. A user pause still requires
the native command; the host cannot label a diagnostic as user intent. Use existing delivery gates to establish delivery; a terminal label alone is insufficient.

While paused, collect already running work and preserve its evidence. Productive
continuation and acceptance wait for authorized resume.

For a due unattended continuation, use the [external supervisor](supervisor.md).
