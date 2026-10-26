# Workers

Rules for the orchestrator and its workers, in the order a worker runs.

| File | Read |
|---|---|
| [launch.md](launch.md) | before every launch: resolve the executor and build the assignment |
| [before-launch.md](before-launch.md) | with launch.md: deployment, executors, routing, helper workers, profiles and thinking |
| [skills.md](skills.md) | while building an assignment: which skills the worker gets |
| [thinking.md](thinking.md) | when a resolved agent allows the orchestrator to adjust thinking |
| [focus.md](focus.md) | throughout an assignment: priorities and blocked continuation |
| [return.md](return.md) | before a worker returns or checkpoints |
| [resources.md](../resources/resources.md) | the runner contract checks for owned resources |
| [capacity.md](../resources/capacity.md) | before admitting a batch: the shared capacity budget |
| [lifetime.md](../resources/lifetime.md) | reserving a resource or starting an owned job |
| [prepare.md](prepare.md) | to register an attempt and verify its settings before productive work |
| [wait.md](wait.md) | to arm a wait, handle each wake and pass the yield gate |
| [collect.md](collect.md) | to collect a result, accept or decline it and record its outcome |
| [recovery.md](recovery.md) | after an availability failure, a blocker or a failed check |
| [escalation.md](escalation.md) | before retrying a blocked or failed worker |
| [retire.md](../resources/retire.md) | to close a worker after its final use |
| [long-sessions.md](long-sessions.md) | after a compaction or resume, and when a session runs long |
| [design-hold.md](design-hold.md) | when a worker may need to break or reinterpret the agreement |
| [legacy-preferences.md](legacy-preferences.md) | when an unconfigured native worker has older models.json preferences |
| [panel](../panel/panel.md) | the card a host draws from an effort's card, attempts and tickets |
