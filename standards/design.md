# Module design

One vocabulary for designing code and for deciding where its tests reach it. Use
these words exactly in briefs, tickets, reviews and test names, in any language the
project uses; say seam or interface, not boundary, and module, not component or service.

## Terms

- **Module:** anything with an interface and an implementation, at any scale: a
  function, a type, a package, a service or a slice across tiers.
- **Interface:** everything a caller must know to use the module correctly: its
  operations, inputs and outputs, and also its invariants, ordering constraints, error
  modes, required configuration and performance limits.
- **Implementation:** what is inside the module, behind its interface.
- **Depth:** how much behavior a caller or test can exercise per unit of interface it
  must learn. A deep module hides much behavior behind a small interface; a shallow one
  has an interface nearly as complex as its implementation. Depth is a property of the
  interface, not of how the implementation is split into parts.
- **Seam:** a place where behavior can be changed without editing that place; the
  location where a module's interface lives. Where a seam goes is its own decision,
  separate from what sits behind it.
- **Adapter:** a concrete thing that satisfies an interface at a seam, such as the
  production integration or an in-memory stand-in used by tests. The word names its
  role, not its size.
- **Leverage:** what callers gain from depth: more capability per unit of interface
  learned, one implementation serving many callers and tests.
- **Locality:** what maintainers gain from depth: change, defects, knowledge and
  verification concentrate in one place instead of spreading across callers.

## Rules

- **The deletion test.** Imagine deleting the module. If its complexity vanishes, it
  was a pass-through; if the complexity reappears across its callers, it earns its place.
- **One adapter is a hypothetical seam, two a real one.** Put a seam only where
  something actually varies across it, typically a production and a test adapter;
  a seam with one adapter is only indirection.
- **The interface is the test surface.** Tests cross the same seam as callers and
  assert what is observable there, not internal state; a test that must reach past the
  interface means the module has the wrong shape. A module may keep internal seams for
  its own tests, but does not expose them through its interface for that reason.
- **Shape the interface for its callers.** Prefer fewer operations, simpler inputs and
  more hidden inside. A module that receives its dependencies, and returns results
  instead of only changing shared state, can be tested through its interface alone.
- **Parse outside data once, where it enters.** Input from files, commands, other
  processes or the network is checked at the seam where it enters, into a form that
  cannot hold an illegal value; code inside trusts that form instead of checking it again.
- **Place a seam by what the dependency is.** In-process computation and state need
  no seam: test through the module's interface. A dependency with a local stand-in,
  such as an embedded database, runs in the tests behind an internal seam. An owned
  remote service gets a seam with a production and an in-memory adapter. A third-party
  service gets a seam whose test adapter is a mock.
- **Replace shallow tests, don't layer them.** When shallow modules merge into a deep
  one, write tests at its interface and delete the old tests of the parts.
- **Design it twice** when an interface is in question: three or more independent
  designers, launched in parallel through the orchestrator, each produce a materially
  different interface under a different constraint (the smallest interface, the most
  flexibility, the simplest common call, or ports and adapters across the seam), with
  its operations, a usage sketch, what it hides and its dependency strategy; compare
  them on depth, locality and seam placement and recommend one.
