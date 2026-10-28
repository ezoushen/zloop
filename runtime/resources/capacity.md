# Shared capacity

Before a batch and each worker launch, reconcile active assignments through native
executor status and the project's existing shared dispatch logs or coordinator. Include
other sessions and projects using a verified common service/account, plus helper
workers. A loop's `--max-live` is only its local admission
limit; it does not grant that many additional account slots. Apply any established
account ceiling as well as executor and exclusive-resource limits. Use the existing
claim/reservation mechanism and verify ownership before launching; do not build a
second scheduler or assume independent accounts merely because hosts differ.

Keep known reservations until actual work ends or ownership is reconciled. Unknown
account use is a capacity uncertainty for dependent fan-out, not proof that all
work must stop; continue independent authorized work and establish the available
budget with the current owner. Without a shared mechanism, report the coordination
limit and avoid competing batches against the known account. zloop's local
board cannot enforce an account-wide ceiling across unrelated processes.

On throttling or quota errors, apply availability recovery
to the evidenced service/account. Do not restart every interrupted worker at once
or treat an error response as proof that its existing job has stopped. Reconcile
live handles and retained reports before replacement, and retain the failure and
retry condition in the existing assignment log.
