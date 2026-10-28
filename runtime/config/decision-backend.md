# Decision backend

Use `executors.json` at user, project or session scope. The complete `decisions`
object replaces the lower scope. Copy the effective object before a partial edit.
Use a separate baseline path for a project/session override so calibrating that
scope does not replace the reference used by other configurations.
The worker `router` remains independent: a skill/profile router keeps its behavior
even if recovery uses the decision backend.

```json
{
  "router": {"type": "decision-model"},
  "decisions": {
    "backend": {
      "protocol": "systemone",
      "endpoint": "http://127.0.0.1:1241/v1/systemone",
      "model": "local/OpenJev-27B-Splash"
    },
    "enabled": ["worker-selection", "recovery"],
    "calibration": {
      "suite": "/absolute/path/to/decisions.json",
      "baseline": "/absolute/path/to/decisions-baseline.json",
      "check_every": 50,
      "max_accuracy_drop": 0.1
    }
  }
}
```

Backend `timeout_seconds` defaults to 20, at most 60. Optional `revision` records a
known deployment/weights revision; change it when redeploying behind a stable model
alias. It is local metadata, not a remotely verified weights hash. Optional
`instruction` adds a shared decision preference. Worker-specific task fit remains
in the catalog and assignment. Backend protocol and model identity are separate:
System One is currently supported; Jev is not a package or service dependency.
Other backends can implement the same internal choice contract.

An inline decision-model router (protocol/endpoint/model on the router itself) is
supported for worker selection only when `decisions` is absent. For shared decisions, move its protocol/endpoint/
model to `decisions.backend` and retain `router: {"type":"decision-model"}`.
An explicit `decisions.enabled: []` disables backend decisions, including legacy
worker calls; selection returns to the host. Omission of a family leaves it with
the existing host workflow. No calibration path is a credential store.
