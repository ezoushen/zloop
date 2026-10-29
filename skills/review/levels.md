# Review effort levels

A review runs at one effort level: the thoroughness of the review, not the model's
reasoning effort. [`--effort`](SKILL.md) selects it, a missing level is `high`, and the
shared procedure reads this table when it plans reviewers.

| Level | Reviewers | Findings reported | Verification | Extra pass |
|---|---|---|---|---|
| low | one reviewer pass, both axes | only findings the reviewer proved itself, at most 4, surest first, and how many candidates it left out | the reviewer's own executed reproduction or traced failure path; assigns no reproduce job | none |
| medium | one reviewer pass, both axes | every finding, each with the reviewer's own verdict: CONFIRMED with its evidence, else UNVERIFIED | the reviewer's own; assigns no reproduce job | none |
| high (default) | one exhaustive pass, both axes | every finding | disputed or hard findings go to a reproduce job | none |
| xhigh | one reviewer per angle | every finding | every finding reproduced, one finding per job: CONFIRMED and UNVERIFIED kept, FALSE-POSITIVE dropped with its verdict recorded | one sweep reviewer given the verified list; its new findings are reproduced too |
| max (only when the user asks) | one reviewer per angle and module | every finding | as xhigh | as xhigh |

Repair and clean rules hold at every level: only CONFIRMED findings are repaired and an
UNVERIFIED one keeps its usual handling. `review_status: clean` at `low` means its reported
findings are closed, and the report names what the level left out.

## Angles

A per-angle review splits by this fixed list, one reviewer each: **Standards** and
**Spec**, the [method](method.md)'s two axes, then the [layers](layers.md)' cross-cutting
lenses **Security**, **Testability**, **Integrity** and **Maintainability**. Six angles,
countable; none is added, split or merged by a reviewer. A lens angle (Security,
Testability, Integrity, Maintainability) applies both axes through that lens. The
documentation gate and the tautological-test and comment checks belong to the Standards
angle, and the sweep re-checks them with the verified list. The sweep looks for what the
angles missed. `max` splits each angle by module. A module is one of the project's areas the changed
files fall in: its area or `touches` convention, else top-level directories.

## The floor every level keeps

No level relaxes the [engineering judgment](../../standards/engineering.md) floor or an
accepted [ADR](../../runtime/domain/adr.md): a reviewer other than the author gives a
verdict per finding, every acceptance criterion maps to evidence that reviewer observed,
and the review says what it did not run. Verification at `low` and `medium`, the
per-angle split at `high` and the per-module split at `xhigh` are what a level leaves
out; the [review report](report.md) records them beside the level it ran at.
