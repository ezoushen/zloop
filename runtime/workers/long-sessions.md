# Orchestrator sessions

Read after a compaction or resume, and when a session runs long.

## Long sessions

Correct procedure must survive compaction:
- **User corrections to how you work.** Routing, reporting, retention, permissions
  and principles are recorded when the user makes them, in the ledger's
  `## Standing directives` section or the host's project
  memory. A compaction summary is not a
  durable store.
- **After a compaction or resume,** re-read the bootstrap and the host session part
  before the next worker launch or phase checkpoint. Reconcile the dispatch report
  before answering status questions.
- **The host model's context window has to fit the work.** Orchestrate with a
  model of at least about 200k tokens of context.
  Compact before the limit. If a host-model error drops a user request, tell the user which request
  did not run; don't let it vanish.
