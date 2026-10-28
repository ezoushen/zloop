# Shadow pilots

Opt-in measurements that never change the host's action. Measure them with the [shadow evidence](shadow-evidence.md) tools.

Contents:
- [Shadow auto-accept](#shadow-auto-accept)
- [Failure-cause shadow pilot](#failure-cause-shadow-pilot)
- [Wake-triage shadow pilot](#wake-triage-shadow-pilot)

## Shadow auto-accept

To evaluate a confidence threshold for worker selection without changing behavior,
add `"shadow_auto_accept": {"min_confidence": <0-1>}` to `decisions`. Each
journaled worker-selection recommendation then records whether it met the
threshold. The flag is stored only in the journal and `inspect` output; resolver and
checkpoint results omit it so the host's acceptance stays independent. Compare it
with the recorded host disposition and verified outcome before proposing real
auto-acceptance, which remains an owner decision. Threshold agreement with the host
is not correctness; local provider scores are uncalibrated.

## Failure-cause shadow pilot

This opt-in pilot measures whether System One can classify failed checks before
any workflow depends on it. Enable it by adding `failure-cause` to
`decisions.enabled`. Run required gates through
`run_check.py` with `--classify-failure`, and a failed
check is classified automatically. For a failure outside `run_check.py`, run:

```bash
<plugin_root>/.venv/bin/python -B <plugin_root>/scripts/decisions.py classify-failure \
  --repo <canonical-project> --summary <absolute-run-check-summary.json> \
  --journal <absolute-session-decisions.jsonl> --assignment-id <assignment/attempt/check>
```

Only a failed summary (nonzero `exit_code`) is accepted. The helper builds the
request itself from the summary fields and the first 2,000 and last 6,000 bytes of
the retained log, framed as untrusted output. It chooses among `environment`,
`missing-context`, `implementation-defect`, `flaky`, `hang` and `check-defect`, or
abstains, and journals the exchange. Output contains only `decision_id`, any error
and warnings; the label stays hidden so the host's diagnosis is not anchored. A
backend failure reports `status: unavailable` with its error, and rerunning the
same assignment ID reuses the first record (`reused: true`) without inference. Do
not inspect the journal's choice before assessing. A disabled family returns
`disabled` without contacting the backend or writing. It is not a phase checkpoint
family.

Diagnose and recover exactly as without the pilot. Once the cause is established
from evidence, record the label and who established it:

```bash
<plugin_root>/.venv/bin/python -B <plugin_root>/scripts/decision_journal.py assessment \
  --journal <absolute-session-decisions.jsonl> --decision-id <id> --actor "Claude host" \
  --source objective|independent|host --expected <label> \
  --evidence <retained-diagnosis-reference> --reason "<how the cause was established>"
```

Prefer `objective` labels, established by later evidence rather than judgment: a
rerun that passes unchanged (`flaky`), a restored dependency that fixes it
(`environment`), a code fix that turns it green (`implementation-defect`).
`independent` means a reviewer other than the diagnosing host assigned it from the
evidence. `host` labels are the host's own diagnosis; they count, but agreement with
them is not correctness, consistent with the [retrospective](retro.md#retrospective)
rule. A later assessment supersedes an earlier one, so an independent audit can
replace a host label.

Repeat `--expected` when several labels apply. `--category` describes why the
classifier was wrong and defaults to `unknown`; use `context` when the deciding
evidence was not in the excerpt sent. The command prints its completion handoff;
`inspect` then reports `correct` per decision, and a missing assessment appears in
`missing_evidence`. A backend error reports `correct: null`, not a wrong answer; an
abstention counts as wrong unless `abstain` is an expected label. Promotion beyond
shadow mode needs a separate owner decision backed by the
[shadow evidence](shadow-evidence.md).

## Wake-triage shadow pilot

This opt-in pilot measures whether System One can read a worker's state from its
output, the step the host performs when inspecting a worker after a wait wakes,
times out or stalls. Enable it by adding `wake-triage` to `decisions.enabled`.
A notified lifecycle wait armed with `capture` classifies each wake itself, after
delivering its receipt, and a completed collection straight after a settled wake
records the objective `done` label (lifecycle waits and
collection).
For other wakes, save the worker's pane or log capture to a file (or, under
Herdr, name the file for the helper to write) before inspecting it and run:

```bash
<plugin_root>/.venv/bin/python -B <plugin_root>/scripts/decisions.py classify-wake \
  --repo <canonical-project> --capture <absolute-capture.txt> [--idle-seconds <n>] \
  [--runtime-status idle|working|blocked|done|unknown] [--foreground=<command-line>] \
  [--herdr-pane <pane-id>] \
  --journal <absolute-session-decisions.jsonl> --assignment-id <assignment/attempt/wake-n>
```

Under Herdr, pass `--herdr-pane <pane-id>` instead of `--runtime-status` and
`--foreground`. The helper reads the pane itself: for an agent pane it uses the
agent status; for a shell pane it uses the foreground process. When inference is
needed, it saves the pane output to `--capture` (`visible` for agents,
`recent-unwrapped` for shells) as assessment evidence. A Herdr error exits 2 without
inference. A disabled family makes no Herdr calls.

Otherwise pass what the runtime already knows; code evidence outranks the classifier.
`--runtime-status` is the agent status the runtime reports, such as Herdr's
`agent_status`. A definite status (`idle`, `working`, `blocked`, `done`) returns
`settled` with that status, without calling the backend or writing the journal;
`unknown` is sent to the classifier as a fact. Omit it for a plain shell pane.
`settled` only echoes the runtime's status: it is not a label and not evidence of
completion, and there is nothing to assess. Under Herdr the pilot therefore measures
shell panes and agents whose status is `unknown`.
`--foreground` is a shell pane's foreground command line, or the shell's own name
when the shell is back at its prompt; omit it for agent panes, whose foreground is
always the agent (Herdr: `herdr pane process-info`). Bind it with
`=`, because login shells report a leading dash such as `-zsh`. The worker controls
its command line, so it is sent labelled untrusted, as one line of at most 200
characters.

The helper sends the last 8,000 bytes of the capture, framed as untrusted output,
plus the optional seconds since the last observed output. It chooses among
`working`, `done`, `needs-input`, `error` and `stalled`, or abstains. As with
failure cause, output omits the label, a disabled family neither calls the backend
nor writes, and the host continues the worker lifecycle
unchanged. After reconciling the worker, for any result other than `settled`, record the state actually found with the
`assessment` command above; for an automatic wake, its `decision_id` is in the
receipt's `triage` field. The worker's later observable state is the objective
source: a retained report for `done`, an answered prompt for `needs-input`, a
provider error for `error`, resumed output for `working`. A `done` label never
establishes completion: the lifecycle's report, revision and acceptance checks
still decide.
