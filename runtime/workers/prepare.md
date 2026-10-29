# Prepare a worker attempt

Use this protocol for delegated work, review included, and on resume or compaction.
The orchestrator owns its state; workers return evidence through their
assigned channel. Keep one absolute `workers.sqlite` path per orchestration run in
the main checkout's retained reports directory, `.scratch/<slug>/reports/workers.sqlite`;
the yield hooks read only that location. Use the maintained
`scripts/worker_lifecycle.py`; do not copy it into the project. Read-only workers
only return evidence. If the orchestrator itself cannot write
coordination state, preserve the gap and use supported native waits and reports.

The hooks also append each `herdr agent start` a session runs, including behind `timeout`,
`env` or a shell's `-c` string, to the main checkout's `.scratch/herdr-launches.jsonl`
(time, agent name, pane, the session's Herdr pane and host session, and the pane's cwd
when Herdr reports it). A launch is named at stop, even after its pane closed, until an
attempt names its pane or agent and, as [wait](wait.md) says, comes from its orchestrator:
registration records the registering Herdr pane and host session. A launch inside a
heredoc or backticks is not logged, and a pane or name from a loop variable is logged as
typed; the live-agent check still names such a worker.
A launch or live agent that needs no attempt (it failed, its pane was closed at a trust
dialog, a probe) is accounted for by running the command the stop message prints,
`python3 <plugin root>/hooks/herdr_launch.py dismiss --pane <pane> --session <host session>
--reason "<why>"` (or `--name <agent>`), in the checkout. The reason is required, and so is
`--session` outside Herdr; the dismissal covers only this orchestrator's launches logged
before it.

Launch every worker with `ZLOOP_WORKER=<slug>/<assignment>` in its environment (in
Herdr, the Herdr guide's tab recipe sets it). For the Herdr
checks a session is a worker exactly when this marker is set: the stop check still holds it
for its own launches, but not for live agents beside it. A session without it is a
orchestrator, wherever it runs and whichever pane it is in.

The helper records attempts and durable receipts. Its `submit` captures
session-bound native settings before invoking a productive prompt command. It
neither launches workers, parses assignment output nor approves a design.
Its wait can deliver durable receipts through a declared native parent callback;
it cannot wake a sleeping host. `collect` requires retirement or explicit retention; `retire`
uses Herdr's native lifecycle interface and confirms the bound pane is absent.
Native tools own those operations. Their callbacks are useful delivery signals;
the retained state remains the collection source after a callback or context is lost.
The wait wrapper needs a POSIX host with `ps` and process-group controls; without
them use the native wait/collection path.

After preparing an attempt, [wait](wait.md), [collect](collect.md) and [retire](../resources/retire.md).

1. Resolve settings from the **calling** host and save the resolver JSON. A Pi
   orchestrator remains `--host pi` when its selected destination is Codex.
   Map the resolved controls to the installed executor's actual model and effort
   controls. A model name or “think hard” in the prompt cannot set them.
2. Create a fresh worker/conversation for a new ticket or agent. Obtain its native
   session identity as well as the transport handle. A fresh pane or a worker's
   claim of fresh context does not establish a new conversation. Give each worker a
   tab or window of its own, as the host's guidance describes, never a split of the
   orchestrator's. Register before submitting productive work, using the following
   input shape:

   ```json
   {
     "kind": "register", "attempt": "ticket-12/implementation-1",
     "assignment": "ticket-12", "repo": "/absolute/main-checkout",
     "worktree": "/absolute/ticket-worktree", "head": "pinned-input-revision",
     "execution": "/absolute/reports/resolved-execution.json", "runtime": "claude",
     "worker": "returned-transport-handle", "native_session": "native-conversation-id",
     "owned": true, "packet": "/absolute/reports/ticket-12-assignment.txt"
   }
   ```

   Pass the event file with `record --state /absolute/reports/workers.sqlite
   --input /absolute/register.json`. Assignment IDs identify a ticket and bounded
   job scope; attempt IDs identify retries. The execution snapshot must carry
   `agent` and `job`, and supplies the requested settings. `packet` is the exact
   assignment text sent to the worker; registration refuses it when it lacks
   `adapter`, `agent_file`, `job_file`, `plugin_root`, `repo` or any of the job's
   inputs as `name: value`. A repair attempt adds `repairs`, naming the collected
   attempt whose work it fixes. When `wake-triage` is enabled, add `journal`, the
   absolute session decisions journal. A worker started through Herdr adds
   `"herdr": {"session": "<herdr session>"}` (`herdr session list` names it), unless
   its `worker` is a `session/pane/name` handle; registration reads its pane, tab,
   workspace and name from Herdr, waiting up to 5 s per call, refuses a session,
   `tab_id`, `pane_id`, name, `runtime` or `native_session` that Herdr or the handle
   contradicts, and refuses a tab the worker shares or the orchestrator's.

   When the snapshot applied a decision-model recommendation, the helper records
   that decision for you: its action at registration, its execution at verified
   startup and its outcome at collection. A `journal` field in a `record` or
   `collect` result reports a capture gap; without that field nothing was linked,
   so record an override yourself. An unbound forwarding executor needs an explicitly verified
   destination; a known destination must agree with the resolver.
3. Name attempts in packets. Collection needs its worker's reply;
   Codex native children prove parentage through rollout metadata.
   Use `submit` for transport commands. Native children use `worker_lifecycle.py record
   --state ... --input /absolute/startup.json` with `kind: startup`, `attempt` and
   `native`: the record path named by the host adapter. Optional `model_alias_evidence`
   names an installed binding. Pass `native` again to `collect`; refresh an OpenCode export.

   **Startup order.** Start the worker and obtain its session id (probe for a fixed reply
   and no tools if needed). Register with the assignment as `packet`, then `submit` it.
   A native child already has its assignment; record its startup once its record is available.
4. For supported native formats, invoke productive work through `submit --state
   ... --attempt ... --input /absolute/submit.json`, using this input shape:

   ```json
   {
     "native": "/absolute/native-session-evidence",
     "argv": ["herdr", "agent", "prompt", "worker", "assignment", "--wait",
              "--until", "working", "--until", "blocked", "--timeout", "45000"],
     "timeout": 60
   }
   ```

   A `timeout` over 60 needs `notify`: the helper refuses a longer wait before invoking
   the worker, since nothing would wake the host. `notify.argv` is any literal argv with
   one whole `{receipt}` argument and a `timeout` of at most 45. With no named parent agent
   (an unnamed orchestrator pane, or a subagent whose prompt would reach the user's
   session), use a command that only records the receipt, such as `["echo", "{receipt}"]`;
   it wakes nobody, so read the receipt with `status`.

   `native` is the registered session's Claude/Pi JSONL, Codex rollout JSONL, or
   a current OpenCode export JSON file (`opencode session export <native session>` in OpenCode 2,
   `opencode export` in OpenCode 1). Obtain it from the installed native
   interface. The helper retains settings and source hashes without copying conversation contents.
   Verify source provenance and exclusive ownership of native controls; refresh an
   OpenCode export before submission. A source snapshot cannot intercept subsequent
   manual changes or an in-memory Pi branch switch that has not been persisted.

   Optional `model_alias_evidence` carries explicit host-verified installed binding
   references. It affects only model identity; effort must still match. The helper
   does not authenticate the semantic truth of these host assertions.
   The submit `argv` returns once the worker shows activity, as Herdr's
   `--wait --until working` does. Exit 0 records `submitted` with the wait log as evidence;
   reconcile it, then wait for completion with `watch`. Any other exit leaves
   the attempt unsubmitted: inspect the worker and record `submitted` only with
   evidence of activity. Reconciliation never authorizes resubmitting the
   same attempt; inspect the native worker and use `watch` for continued monitoring.
   Further same-assignment work requires a collected, retained previous attempt.

   For native children, check `gate --state ... --attempt ... --stage work`
   before productive continuation. Record `submitted` when the native interface
   confirms acceptance of a separate assignment prompt.
   After a stalled or uncertain submission, inspect the same worker before retrying.

Same-assignment repair or recheck may retain context only after collecting the old
result, acknowledging its receipts and recording `retain` with a reason and next
use. Register the new attempt and verify its settings again. Reusing a native
session across tickets, repositories or agents is rejected even if its pane changed.
A reused worker also cannot take a job whose `independent_of` lists a job it already
did: a worker that did a diff-review cannot then take a reproduce job.
Pass a compact handoff: accepted decisions, pinned base/head, worktree, source/test
entry points, applicable evidence, unresolved risks and next action. Cache savings
do not justify carrying an unrelated conversation into a review.
