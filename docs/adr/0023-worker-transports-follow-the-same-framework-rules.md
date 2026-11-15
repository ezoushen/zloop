# Worker transports follow the same framework rules

Every worker transport (Herdr panes, T3 Code threads on this machine or on a linked environment, and any transport added later) registers, submits, waits for, collects and retires a worker by the same framework rules; transports differ only in the means that carry a rule out. T3 support had grown rules of its own beside Herdr's that nobody had chosen, so a difference now stands only where a transport lacks the means (T3 has no stop, so an archived thread is its "gone"), and the code says so where it does.

## Consequences

A change to how one transport carries out a stage starts by reading the other transports' path for that stage, and its review compares the paths, not only the diff.
