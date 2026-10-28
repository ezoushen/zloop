# Automatic thinking adjustments

Prefer `thinking: {"default":"medium","allowed":["medium","high"]}` for bounded
choices. This form grants adjustment within the set and cannot be combined with
`allow_thinking_override`. The default must belong to the set; a single value locks
thinking. See [selection](../routing/choose.md) for joint model/effort decisions and examples.

For string thinking settings, `models.allow_thinking_override` defaults to `false`.
In override mode, set it to `true` to let the orchestrator adjust thinking, for example raising
medium to high for a complex repair. It grants no permission to change the model,
executor or saved configuration. To retain destination defaults while allowing
adjustments, use `{"mode":"override","allow_thinking_override":true}` without
model/thinking fields. Strict inherit mode continues to send no overrides.

Resolve an agent-chosen adjustment with `--route --adaptive-thinking <label>`.
The resolver rejects it unless the selected agent opts in, and enforces any allowed
set. Reserve `--thinking`
and complete `--route-choice` overrides for explicit user choices; never use them
to bypass this permission. A user-specified thinking level takes precedence over
adaptation. With a string setting it works even when automatic adjustment is
disabled; with bounded thinking it must belong to the allowed set.

Choose a supported level based on the assignment's complexity and record the
reason, baseline (or inherited), and effective setting in its existing report.
Labels and their ordering belong to the destination runtime; do not guess a
portable ranking. The resolver returns the permission, `thinking_baseline`, any
`thinking_allowed`, and
effective `settings.effort`; adapters apply that result through their existing
controls. The permission allows either direction, but does not require a change.
It applies to new assignments; do not restart a live worker to alter thinking.
Resolution validates configuration, while executor preflight verifies that the
chosen runtime/model supports the requested level.

`thinking` is a runtime label, not a portable numerical budget. The resolver
normalizes it to internal `settings.effort` for existing adapters. The destination
must support the requested model/label pair; do not substitute a prompt instruction
or approximate label. Settings are requested, not confirmed, until native metadata
establishes them. Do not infer the destination from model names or skill prose alone:
inspect the installed invocation contract. Catalog `target_runtime` is the requested
destination; verify the bridge actually uses it. Named bridges, skills and programs
retain `native_dispatch: false` rather than claiming native execution. Complete instances can
still delegate without a catalog binding when their installed contract owns routing.

`native_dispatch: true` uses the existing host worker and permits native profile
rendering. Named sub-agents retain their selected profile and its own controls;
Bash/Skill workers use their executor's controls. Never also spawn a native worker
or re-resolve saved preferences after selecting an executor.
