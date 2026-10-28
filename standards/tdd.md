# Test-first development

How every behavior change is built: test first, one rule at a time, with tests that
protect the ticket's business rules rather than pile up coverage. Modules, interfaces,
seams and adapters mean what [module design](design.md) defines. No language,
framework or test runner is assumed.

## The loop

- **Red before green.** Write one failing test, run it and see it fail for the
  expected reason, then write only enough code to make it pass. Do not anticipate
  later tests or add speculative features.
- **One slice at a time.** One rule, one test, one minimal implementation per cycle;
  the next test answers what the last cycle taught. Never write all the tests first.
- **Refactor after green.** Once the test passes, refactor the ticket's own code
  within the ticket's scope, keeping the tests green; a wider refactor goes to review.
- **Run what the question needs.** Each cycle runs the tests of the rule it works; the suites
  covering the changed code run before the work is handed back, and the full gate once on
  the integrated head, by whoever integrates (the orchestrator in an effort), not per cycle
  ([check tiers](../runtime/checks/accept.md)).
- **Record the failing run before the passing one:** the command, its exit status and
  the failure it showed, then the passing run of the same command. A test never seen
  failing proves nothing.

## Seams

- **Seams come from the ticket.** Its **Seams** line names the public interfaces its
  tests reach, approved with the ticket.
- Prefer the highest existing seam that exposes the rule, and the fewest seams. Do not
  expose an internal seam through the interface only because a test uses it.
- A seam the ticket does not name goes back to the orchestrator before any test is
  written there.

## What a good test is

- A test checks behavior through the module's public interface, the seam its callers
  use, and survives a refactor of the implementation. Its name says what the behavior
  is, not how the code does it.
- **Mock only at system boundaries:** external services, time and randomness, and
  storage or the file system when no local stand-in exists. Never mock the project's
  own modules or internal collaborators. At a boundary, prefer one specific operation
  per external action over one generic call, so each mock returns one shape and needs
  no conditional logic.

## Effectiveness over volume

- **Each test states the business rule it protects**, in its name or a line beside it.
- **Expected values come from an independent source:** a specified literal, a worked
  example from the ticket or spec, or a separate trusted oracle; never from the code
  under test, a copy of its logic or a value the test configured on a mock.
- **Assert the whole claimed result**, and for an expected failure the reason the rule
  gives, not only that something failed.
- **Break the rule on purpose.** Each new or changed test is shown to fail when its rule
  is deliberately broken in the implementation, at the assertion that claims it; then
  the break is undone and the test passes again. Record both runs.
- **Coverage means rules, not lines.** Every business rule and acceptance criterion of
  the ticket has a test that fails when that rule breaks, including its boundary cases
  from the spec. A line-coverage figure is neither the goal nor proof. Tests of trivia,
  such as pass-through accessors or framework behavior, are not written to raise it.
- **No honest test, no weak one.** When no seam the ticket names can hold an honest test
  of a rule, write none: report the rule untested, name what verifies it instead and
  leave its checkbox open with that reason.

## Anti-patterns

- **Implementation-coupled:** the test mocks internal collaborators, checks private
  parts or verifies through a side channel instead of the interface, so it breaks
  when a refactor keeps the behavior.
- **Tautological:** a plausible defect in the claimed rule leaves it passing. The
  [tautological-tests](kinds/tautological-tests.md) kind lists its shapes and how to
  prove one.
- **Horizontal slicing:** all tests first, then all code. Such tests check the shape
  the author imagined, not the behavior the code turned out to need.
