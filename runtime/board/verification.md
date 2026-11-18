# Reusable feature verification

Use the project's existing driver first. When repeated verification requires
rediscovering setup or writing disposable scripts, record a feature contract beside
that driver: launch, doctor, drive, evidence and cleanup. Identify the observable
outcome, fixtures, invalid-run conditions and artifacts that must survive cleanup.
No new setup file is required for a project that already supplies this contract.

The optional `scripts/verify_features.py` runner executes a JSON feature map. Each
entry has a unique safe `name`, positive `timeout`, five literal argv arrays named
`launch`, `doctor`, `drive`, `evidence`, `cleanup`, and `required_artifacts` paths
relative to retained evidence. It substitutes `{workspace}`, `{evidence}`,
`{plugin_root}`, `{python}` and `{feature}` inside individual arguments, never
through a shell. Each trial gets an isolated disposable workspace and new evidence
directory. Existing project boot/device/HTTP/UI drivers remain the commands.

The evidence command independently judges post-state; a successful drive exit does
not establish correct behavior. Setup/doctor/driver failures are blocked runs;
an evidence assertion failure is a failed feature. Exit 125 is an infrastructure
gap. Cleanup runs after failure too, including an oracle change. Existing cleanup
file arguments are fingerprinted before launch; files prepared by setup are added
before drive. Relative paths resolve in the trial workspace. Setup must prepare
teardown code before drive. If captured cleanup inputs or the framework runtime
changed, teardown is untrusted: the runner blocks
cleanup and records required operator reconciliation of the owned resources.
Missing retained artifacts or changed protected inputs cannot pass, and every stage
uses the existing bounded check runner.

`protected_inputs` names immutable oracle/fixture paths. Keep the oracle outside
the driver's workspace and preserve its source and hash. The runner also checks
runtime and driver input hashes, so editing the check cannot manufacture a pass.
Python/JSON file arguments are pinned automatically; list other driver and grader
implementation files in `protected_inputs`.
It does not replace native filesystem restrictions; verify actual permissions when
the evaluation depends on them. Retain only the input/output files needed to
inspect or replay the claim, including failed trials.

Zloop's pilot map, `benchmarks/workflows/features.json`, covers source drift, false
user-pause inference, a collected result during an explicit pause, stale reports,
next-phase continuation and observational measurement. The paused-completion case
uses a persisted completed-worker fixture, not a concurrent live child. The scripted
mode verifies the driver and grader infrastructure:

```sh
python3 <plugin_root>/scripts/verify_features.py <plugin_root>/benchmarks/workflows/features.json \
  --evidence <main-checkout>/.scratch/<slug>/evidence/scripted
```

For a host/model behavioral evaluation, provide a JSON argv file with an actual
agent driver. It replaces only `drive`, receiving `{task}` and `{workspace}`:

```json
["codex", "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check",
 "-C", "{workspace}", "{task}"]
```

Run with `--driver <argv.json> --trials 2`, optionally selecting `--feature <name>`.
Use the installed host's supported invocation and permissions. Pin the loaded
contracts/build and actual host/model/settings in the retained native evidence;
the argv alone records requested settings. A provider or setup failure is not a
model-quality verdict. Inspect native activity alongside the independent fixture
outcome, and report every trial, not just the eventual pass.

The pilot tasks are a bounded regression/capability sample. They do not prove a
complete product implementation-to-merge run, crash recovery on every host, or
universal instruction adherence. Add a case when an observed failure justifies it,
prove its grader rejects a broken variant, and separate product regression from
verification drift before changing the map.
