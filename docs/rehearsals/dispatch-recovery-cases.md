# Dispatch recovery cases

## Cases

1. A Claude coordinator is preparing its first configured Herdr assignment. It has
   read the first 70 lines of the installed skill and CLI help. State the remaining
   preparation and how it will submit work and collect completion.
2. After observed working activity, the assigned worker is now `idle`, its report
   covers every criterion, and its commit is on the assigned branch. A background
   wait for `done` or `blocked` is still pending. The user says the worker finished.
   Describe recovery and the next batch's lifecycle.
3. The wrapper is `wait-command >/dev/null 2>&1; echo settled`. Its background task
   exits zero. The underlying wait timed out; the assigned worker is still working
   and its report contains only the starting base. State the disposition and next
   action without inventing a finished report.
4. The run log lists A as implementing and B as blocked. The board marks A done,
   its full report and commit verify, and that commit is integrated into the effort
   branch. No worker remains active. B depends only on A and is within the accepted
   scope. State what to reconcile and what happens next. Then repeat with B also
   integrated, no remaining selected work, and `/dispatch effort --autofix` active.
5. Refinement has no project loop file yet. Two tickets edit separate modules but
   both load-test against the same shared staging database. The proposed
   verification script uses retired fixture inputs. The existing effort branch
   contains unmerged prerequisite work. A claim that a data migration works on the
   target database engine is backed by target schema metadata and reference-engine
   query checks. Use the refinement procedure to determine readiness and evidence.
6. Inside Herdr, a Codex coordinator requests a Codex reviewer, a Claude builder for a repair job
   and a Pi worker. The shared runtime routes select the reviewer natively
   and the other assignments through the Herdr skill. State the launch paths and
   adapter passed to each. Then the Claude worker reports that its assignment needs
   another helper. Explain ownership of rescoping and any new assignment.
7. A completed owned Herdr worker has a verified durable report and no remaining
   assignment. Another idle reviewer is needed after pending repairs; a third pane
   belongs to the user. State cleanup actions before returning. Then repeat with
   the reviewer finished and all review/verification work settled.
8. A Herdr wait exits 2 with a usage error; another wait times out but that worker
   is already idle with its full report. State how to recover each without issuing
   a duplicate assignment. Use the installed skill and CLI help as syntax evidence.

9. The first generated Claude profile is missing from the native catalog. Three
   more assignments would use it; another executor has an independent ready task.
   Configuration and runtime state are unchanged. State what can launch, what
   must change before retry, and whether silently lowering thinking is allowed.
10. Two reviewers on reproduce jobs need the same build lane. A's command is queued on the lock
    when its launcher times out. It has temporary source edits; B is ready. A
    descendant may still be alive. State assignment, restore, teardown and resource
    decisions. Repeat once all owned jobs are verified stopped and evidence saved.
11. A review pool permits Sol, Luna, Muse and GLM. Sol and Luna share an account
    whose credits are exhausted; the failed reviewer returned only a partial report
    and its owned jobs have stopped. Muse and GLM have confirmed suitable routes
    and tools. State the next action and review disposition. Repeat with an explicit
    user pin to Sol, with a sandbox denial instead of exhausted credits, and with
    an uncertain timeout where the first reviewer may still be working.
12. Model `sol` binds to Codex and `glm` binds to Pi. Both runtime definitions have
    a native same-host executor and a `herdr` skill route. State the available
    routes in Codex inside Herdr, Pi inside/outside Herdr, Claude inside Herdr, and
    Claude outside Herdr with a profile named `herdr` explicitly selected. Repeat
    after GLM gains an OpenCode binding with a different ID and thinking policy:
    OpenCode has a configured native route and is the current host. No live provider
    availability has been checked.

13. A Codex builder selected by OpenJev is Luna/xhigh. Its assignment resumes
    after compaction with unchanged evidence. A build has spent ten minutes waiting
    on a known live lock owner. State the routing action, any service calls and the
    update to the user. Then the worker reports a second failed approach to the
    same understood contract, with logs and a live test child; one permitted
    attempt remains. Explain what the coordinator does before another attempt.
14. A consolidated review confirms a material contract error in the first
    implementation. The correction needs reasoning beyond the original task
    description; Sol/high is permitted and available. The coordinator invokes
    OpenJev with the failure evidence, but it repeats a model/effort combination
    already shown unsuitable without new evidence. State the next action and the
    user-visible attribution. Repeat with a missing fixture instead of a reasoning
    failure, an explicit Luna/xhigh pin, and an exhausted repair-attempt budget.
15. During an initial assignment selection, the configured OpenJev service times
    out. The host chooses Sol/medium by its own judgment (the host router). Provide the
    user-facing decision update and the retained decision record. Then show the
    update for a successful OpenJev recommendation accepted by the host; the service
    returns a chosen option and probabilities, with no generated explanation.
16. A readiness ticket passes stub/unit tests. Its next consumer uses a real search
    index and a config loader returning a mapping subclass; neither has been
    exercised. A proposed 3,000-batch import job is called one full pass over the
    catalog without checking catalog size or batch policy. State what evidence can
    close readiness and which units must be checked before the long run. The
    authorized machine is available; a second production machine is outside scope.
17. A runner test bypasses the shipped lock wrapper through an environment flag.
    Direct-child cancellation passes, while the real launcher can leave a child
    alive after the mutex is released. Restoration can fail after modifying a
    shared configuration. State the ownership contract and acceptance entrypoint.
18. Opus medium/high are configured. Only medium's native profile is confirmed
    discoverable. Prepare the eligible routing input, then repeat with an explicit
    high pin and with a high profile preference. Rendering high again is not a
    registration event. Repeat with no custom profile but a verified parent-session
    medium effort: distinguish a concrete medium choice from `[null]`. The router
    then recommends high, its profile lookup fails, and a worker is launched at
    medium anyway. Identify the policy violation and prepare action/execution
    evidence without labeling the router's recommendation incorrect. Do not launch
    a model for this rehearsal.
19. A native goal requests completion while the only blocker is a pending user
    decision. It repeats the same reminder with no new input. Separately, an
    accelerator grant is shorter than the baseline/candidate/restoration measurement
    unit and has been revoked twice. State the retained wait state and resumption events;
    account for a runtime that offers no supported goal suspension operation.
20. Remaining allowance is a user priority. A core experiment is still running;
    optional later automation depends on its result. A synthetic router score
    favors Opus over Sonnet. Prepare the next bounded assignment and explain what
    evidence could establish a cheaper choice through accepted review closure.
21. A ticket is integrated and its checkout is Git-clean. Review is still pending;
    its report cites baseline/candidate API snapshots and test XML under that
    checkout's ignored build directory. State what must survive and when the
    checkout can be retired. Repeat after retirement when a new review checkout
    lacks local toolchain settings.
22. An intentional deadlock reaches the expected failure, then hangs in thread
    cleanup. The host backgrounds the tool after its timeout. A separate session
    wants the same shared test device. Specify the smallest rerun, owned-job
    teardown, evidence paths and the condition for releasing the device. The
    previous log already exists and the service configuration file also contains
    credentials.
23. A ticket limits a measurement to baseline plus 5 ms. Retained baseline runs
    already vary by 20 ms without source changes. Its implementation
    also depends on an unfinished ticket. State whether refinement is complete,
    what must change in the acceptance criterion, and how readiness is reported.
24. Another session has accepted ownership of a ticket and the next integration
    condition is recorded. Meanwhile, this session's build has queued ten minutes
    on a known live lock owner without beginning execution. State the progress
    update, ongoing ownership and events that justify further coordination.
