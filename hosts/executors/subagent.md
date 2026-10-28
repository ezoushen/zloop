# Sub-agent executor

`{"type":"subagent"}` selects the default worker. Omitted `config` and
`"config": {}` are equivalent; provide `config.agent` only for a named delegate.

Use the current host's delegation tool and lifecycle from its adapter. With empty
`executor_config`, use its normal profile for the job's agent. A configured `agent` selects
that exact native profile/agent instead. Verify it exists and can fulfill the
assignment before starting; do not substitute a generic worker on failure. Report
availability failures to the coordinator for [pool recovery](../../runtime/workers/recovery.md).

Supply the shared assignment, resolved model policy and settings. A named agent
may forward to another runtime. Inspect its installed entry point to establish
the actual worker runtime and model controls; `host` does not establish
the destination. Read the destination settings guidance when applicable. For
`inherit`, preserve destination settings and send no overrides. For `override`,
apply `settings.model` and `settings.effort` (the normalized thinking label) to
the actual worker. Changing a forwarding agent's model is not sufficient.

For default native workers, reuse the host's settings mapping and generated
profiles where needed. For named agents, do not generate a default zloop
profile that would replace the chosen agent. Use its supported controls, preserving
its tools and restrictions, or report that the requested settings cannot be applied.
Settings expressed only as prose are not runtime overrides.

Inspect the installed bridge contract before launching. For example, a Claude
plugin's forwarding delegate is an Agent selection, not a skill. Its supported model/effort
flags belong to the forwarded request; its wrapper must only forward and return
the result. Supply a fresh, explicitly scoped read-only request for review work.
Pi's external profiles are also named sub-agent selections, but may suppress
destination configuration or omit model controls; do not claim inheritance or
override support from their names. Missing capabilities block the assignment.

Count forwarding agents and their workers within available capacity. Record the
native run ID, assignment ID and requested/confirmed settings. Follow native
notifications, wait/status and result collection. Empty or truncated output is
incomplete. Reconcile the same run after interruption before launching a replacement.
Persist read-only worker reports in the parent and apply the shared acceptance gates.
