# OpenCode workers run inside an OS sandbox

An OpenCode worker's server runs under macOS `sandbox-exec` with a profile from `scripts/seatbelt.py`: it may write only to the route's paths (temporary files, the reports folder and, for a builder, its worktree and git's commit machinery), the worker's private folder and the user's OpenCode data folder, and it may not read the user's ssh, aws, gnupg, gcloud or netrc credentials. Where seatbelt's probe cannot prove the sandbox is enforced, the launch is refused. OpenCode's own permission rules cannot contain a worker: an allowed shell writes wherever the user can, whatever `external_directory` says, and `ask` stalls an unattended worker.

## Considered Options

- Narrowing OpenCode's `external_directory` rules only: an allowed shell still writes anywhere.
- Asking before shell commands and outside writes: an unattended worker stalls on the first one.
- Launching uncontained where no sandbox is available: the worker could write anywhere the user can.

## Consequences

OpenCode workers launch only on macOS until another platform has a sandbox whose enforcement can be proven.
