# Effort levels scale thoroughness above a fixed floor

Review, dispatch and autopilot take an effort level from `low` to `max`; without one they run as they did before levels existed (review `high`, dispatch `medium`), never at the level used last. A level adds or removes only work beyond the engineering standard's floor and the project's required checks, never those. We chose a fixed default over Claude Code's `/code-review`, which reuses the level typed last, so a run's thoroughness never depends on an earlier invocation.
