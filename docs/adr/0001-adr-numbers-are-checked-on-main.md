# ADR numbers are re-checked on main when an effort lands

An agent numbers a new ADR as the highest existing number plus one on its branch; before the
effort's review head is pinned it fetches, merges the verified `main` tip and re-checks the
numbers, renumbering on a collision, and the delivery gate repeats the check. We chose this over
placeholder names renamed at merge (as Rust RFCs do), because parallel efforts rarely add ADRs at
once and a placeholder costs a rename every time.
