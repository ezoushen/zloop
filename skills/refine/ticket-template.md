# Local Markdown ticket template

```markdown
---
ticket: <slug>/NN
status: ready
blocked_by: []
touches: [<area>]
devices: []
# needs: the capabilities its Verify commands use, from network, docker, setuid, repo-write, dev-fd
needs: []
model: standard
worker:
---
# NN: <title, a behaviour not a component>

**Story:** As a <actor>, I want <capability>, so that <benefit>.

**Delivers:** the end-to-end behaviour this ticket makes work, from the actor's side. Brief: `.scratch/<slug>/alignment.md` · <outcome and approach it realises>.

**Blocked by:** <NN: title>, or "None (can start immediately)".

**Seams:** <the public interfaces its tests reach: existing and higher ones first, as few as will do>.

## Acceptance criteria
- [ ] Given <state>, when <action>, then <observable outcome>.
      Verify (code|agent|user): <command, test name or user check>
- [ ] …

## Comments
```
