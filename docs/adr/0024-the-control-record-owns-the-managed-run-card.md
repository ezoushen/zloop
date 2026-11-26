# The control record owns the managed run card

The control record commits authority before publishing the Markdown so a crash between writes leaves one decision that can be retried. Readers use that decision when the view is absent or matches the exact view recorded for replacement by an unfinished publication. Recovering any other edit requires explicit reconciliation against the hash of the observed bytes, preserving the displaced view for inspection.
