# Review effort levels

A review runs at one effort level: the thoroughness of the review, not the model's
reasoning effort. [`--effort`](SKILL.md) selects it, a missing level is `high`, and the
shared procedure reads this table when it plans reviewers.

| Level | Reviewers | Findings reported | Verification | Extra pass |
|---|---|---|---|---|
| low | one reviewer pass, both axes | the surest findings only, at most 4, each with an executed reproduction or a traced failure path | none; the reviewer reproduces nothing | none |
| medium | one reviewer pass, both axes | every finding | none; each finding is marked unverified | none |
| high (default) | one exhaustive pass, both axes | every finding | disputed or hard findings go to a reproduce job | none |
| xhigh | one reviewer per angle | every finding | every finding reproduced: CONFIRMED and PLAUSIBLE kept, REFUTED dropped with its verdict recorded | one sweep reviewer given the verified list |
| max (only when the user asks) | one reviewer per angle and module | every finding | as xhigh | as xhigh |

## Angles

A per-angle review splits by this fixed list, one reviewer each: **Standards** and
**Spec**, the [method](method.md)'s two axes, then the [layers](layers.md)' cross-cutting
lenses **Security**, **Testability**, **Integrity** and **Maintainability**. Six angles,
countable; none is added, split or merged by a reviewer. `max` splits each of them by
module.

## The floor every level keeps

No level relaxes the [engineering judgment](../../standards/engineering.md) floor or an
accepted [ADR](../../runtime/domain/adr.md): a reviewer other than the author gives a
verdict per finding, every acceptance criterion maps to evidence that reviewer observed,
and the review says what it did not run. Verification at `low` and `medium`, the
per-angle split at `high` and the per-module split at `xhigh` are what a level leaves
out; the [review report](report.md) records them beside the level it ran at.
