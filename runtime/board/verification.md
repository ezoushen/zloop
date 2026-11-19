# Reusable feature verification

Use the project's existing driver first. When repeated verification requires
rediscovering setup or disposable scripts, record a feature contract beside that
driver: launch, doctor, drive, evidence and cleanup. Choose observable outcomes,
fixtures, invalid-run conditions and evidence that must survive cleanup.

Inspect the executable contract without running its commands:

```sh
python3 <plugin_root>/scripts/verify_features.py <feature-map.json> --inspect
```

Use `--evidence <new directory>` to execute it; an existing directory is refused so earlier evidence stays intact. For behavioral evaluation, add `--driver
<argv.json> --trials 2`, optionally `--feature <name>`; the JSON argv driver receives
`{task}` and `{workspace}`. Use the installed host's supported invocation and permissions.
The pilot map is `benchmarks/workflows/features.json`.

The evidence command must independently judge actual post-state. A successful drive
exit cannot establish correct behavior. Keep its oracle outside the driver's
workspace and name immutable grader/fixture files in `protected_inputs`.
Inspection validates the contract's syntax; the run checks cleanup, protected inputs
and retained artifacts. Neither proves the oracle is adequate or native permissions
enforce the intended boundary.

Pin loaded contracts/build and actual host/model/settings in native evidence;
requested argv alone is insufficient. Inspect native activity alongside fixture
outcomes and report every trial, including provider and setup failures. Such failures
are infrastructure gaps, not model-quality verdicts. Retain enough input/output for
another person to inspect or replay the claim.

Pilot cases are bounded capability samples. The paused-completion case uses a saved
completed-worker fixture. Do not generalize them to live concurrency, every host's
crash recovery or a complete implementation-to-merge run. Add a case for an observed
failure, prove its grader rejects a broken variant, and distinguish product regression
from verification drift before changing the map.
