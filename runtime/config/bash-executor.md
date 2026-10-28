# Bash executor

The configured program implements the assignment/result contract below. An
arbitrary CLI is not automatically compatible: use a small wrapper only when its
native interface needs translation. Verify that the program understands the
requested workspace, access limits and model policy before assigning work. No
shell template expansion is performed. To run a Bash script, configure
`argv: ["bash", "/absolute/path/to/script.sh"]`. Arguments are literal; relative
paths in argv are relative to the assigned worker worktree.

Save the resolver JSON and assignment JSON in the existing workstream state, then use
the Python environment described in [executor configuration](executors.md#manual-setup-and-inspection):

```sh
"<python>" "<plugin_root>/scripts/bash_executor.py" --execution "<resolved.json>" --assignment "<assignment.json>"
```

The assignment requires nonempty `assignment_id`, absolute existing `worktree`,
`agent_file` and `job_file`, pinned `head`, and `prompt`. Include the usual scope, review base,
permissions and evidence requirements as additional assignment fields. The helper
runs argv in that worktree and supplies this JSON on stdin:

```json
{
  "assignment": {"assignment_id": "review-1", "worktree": "/repo", "agent_file": "/plugin/agents/reviewer.md", "job_file": "/plugin/skills/review/jobs/diff-review.md", "head": "pinned-sha", "prompt": "Review the assigned scope"},
  "models": {"mode": "override", "model": "requested-id", "thinking": "high"}
}
```

The program must validate and honor the model policy before launching its worker;
unknown model/thinking support returns a blocked outcome. Inherit preserves the
destination's native settings; override changes only supplied fields. It must
retain the assignment's permissions using its runtime controls. The helper does
not sandbox a program or prove it applied settings. Use stderr for diagnostics.

Wait for the actual worker, then return one JSON object on stdout:

```json
{"assignment_id":"review-1","status":"completed","report":"Findings and evidence...","run_id":"native-worker-id"}
```

Status is `completed`, `blocked`, `failed` or `unknown`; every outcome needs a
nonempty report explaining results or the blocker. Include revision evidence and
confirmed settings when available; otherwise keep settings unverified. An exit
status of zero validates transport only. Nonzero exit, missing/mismatched identity
or malformed output is an execution failure, never a clean review.

Use the host's background process/session lifecycle for long-running helpers and
record its handle with the assignment. A tool timeout does not mean the process
stopped. Reconcile that process and any worker handle before retrying. A wrapper
launching a separate worker must retain its handle for recovery and cleanup;
cancelling the wrapper alone is not proof that the worker stopped. Core applies
the normal acceptance gates after collection and owns persistence of reports.
