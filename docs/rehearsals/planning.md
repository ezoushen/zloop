# Mandate and alignment question rehearsal

Exercise the [shared question filter](../../runtime/interview/question-filter.md)
through the active host's planning entry point. [Alignment](../../skills/align/SKILL.md)
uses the same filter. These are behavioral acceptance cases, not evidence that a
live orchestrator has already passed them.
Run the question turn in the coordinator; collect the verifier's report separately.

## First-round fixture

The user wants smaller feature-flag artifacts for a rollout service and asks to
grill a blocked design decision. A census suggests 410 of 2,750 flag definitions
may be unused, but about 130 call sites build flag names from tenant or region
strings, so absence from static references is insufficient deletion proof. The
edge-worker bundle already drops unused flag definitions; the dashboard bundle
ships the full evaluation rules, about 2 MB. Removing flag definitions and removing
evaluation rules have different compatibility risks. The user has not yet chosen
between them.

Expected opening: explain that size comes from both flag definitions and evaluation
rules, then ask the governing scope question. For example:

> Must every existing flag remain available, or may we remove flags only after
> proving they are unused? I recommend keeping every flag available for this
> round; the dynamic usage still needs investigation before deletion is justified.

Wait for that choice before asking about an API change needed by a particular
approach. With data preservation accepted, the next material question can be:

> If all adequate compatible options are proved insufficient, may existing callers
> require changes? I recommend allowing that only as a proven last resort;
> affected consumers would need migration and compatibility checks.

Equivalent wording passes. A deletion-list questionnaire about test-only flags,
internal-only flags, generic matches and orphan fixtures fails as the opening. Calling
the 410 flags "proved unused" or promising a measured saving before verification
also fails. Consumer classifications or release-strategy menus, re-asking facts
already supplied, checkpoint metadata and "I'll default unless you object" fail
as the opening. A shorter interview must still represent uncertainty accurately.

## Continuation and boundary cases

| Case | Expected behavior |
| --- | --- |
| Bare "grill this plan" during mandate/alignment | Follow grilling and its question filter before composing questions: principles first, one or two per round. |
| User says "API changes only if that is the only adequate way" | Record the condition, investigate non-breaking mechanisms and retain comparison evidence. Continue authorized work without another approval of the same condition; other permission gates still apply. |
| Five independent material forks remain | Ask at most the two that unblock the next work, retain the others as unresolved and pause their dependent work. Do not silently choose user-reserved forks. |
| Two numbered questions contain several decisions | Count the subquestions and trailing approval requests. Reduce the round to its governing decisions; headings alone do not satisfy the budget. |
| Missing technical fact | Investigate it rather than asking the user to choose a mechanism without evidence. Keep independent work moving. |
| Accepted mandate already settles the next step | Ask no new steering question. Preserve the decisions and proceed within authorization. |
| User explicitly asks for an exhaustive design-tree interview | Ask more questions per round, still only principles and material forks; the agent decides implementation detail and lists material ones for veto. Preserve settled choices; missing sources remain investigation gaps. |

## Assessment

Retain the actual first response, loaded filter path/content hash, unresolved
decisions, user answers and next action for each exercised host/model. Judge the
decision level, answerability, uncertainty and continuation, as well as the number
of questions. Grade the complete visible reply, without extracting its question
paragraph from surrounding plans, tables or metadata. The opening must retain
both artifact-size sources even when the first scope question concerns only one.
A source-text assertion or one successful model response is not
proof that every future planning session will follow the policy.
