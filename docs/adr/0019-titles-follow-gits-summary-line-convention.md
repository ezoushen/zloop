# Titles follow git's summary-line convention

Ticket titles, commit subjects and PR titles follow git's summary-line convention (imperative mood, 50 characters aimed at and 72 at most, no full stop, lowercase after a Conventional Commits `type: ` prefix), defined once in `standards/titles.md`, and a ticket's branch is named from its title words with no ticket number. We named a standard models already know rather than writing our own rules, adopting only the part that fits titles, as asking for ASD-STE100 by name does for prose; scripts refuse what they can check and leave mood to review, and a project's own documented convention governs what an agent writes there.

## Considered Options

- Chris Beams' capitalised subject: conflicts with the lowercase `type: ` prefix.
- Conventional Branch (`<type>/<description>`): conflicts with `<slug>/<title>` and recommends ticket numbers.
- ASD-STE100: has no rules for titles.
