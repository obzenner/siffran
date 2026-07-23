---
number: 3
title: Forbid inkrot (/hr); require methodologist (/think) as companion
status: accepted
date: 2026-07-17
tags:
  - architecture
  - dependencies
links:
  - target: 5
    kind: relatesto
  - target: 12
    kind: Depended on by
  - target: 15
    kind: relatesto
  - target: 16
    kind: Depended on by
---

# Forbid inkrot (/hr); require methodologist (/think) as companion

## Context and Problem Statement

The colleague's narrative invokes `/hr` (staff discovery agents) and `/think` (finalize).
These are not the same kind of dependency: `/hr` lives in a *separate marketplace* (inkrot),
while `/think` lives in *this* marketplace (siffran, the methodologist plugin). The user
directed "we are not mixing the repos, so inkrot is not a dependency here," and separately
required that "the new skill should support all methodologies from think; they depend on
intent." May the new skill depend on `/hr`? On `/think`?

## Decision Drivers

* User constraint: no cross-marketplace (inkrot) dependency — `/hr` is out of bounds.
* User requirement: the workflow's finalize stage uses `/think` and supports *all* its
  methodologies, selected by intent — `/think` is in bounds and load-bearing.
* Same-marketplace composition is not "mixing repos"; cross-marketplace is.
* Testability and staffing still need a home that doesn't reach into inkrot.

## Considered Options

* **Both forbidden** — treat `/hr` and `/think` symmetrically; the skill owns staffing and
  finalize inline. (Rejected: throws away methodologist, a sibling plugin, and the explicit
  requirement to use all its methodologies.)
* **Both required** — depend on inkrot's `/hr` and methodologist's `/think`. (Rejected:
  violates the no-inkrot constraint.)
* **Forbid inkrot, require methodologist** — `/hr` is forbidden; the skill owns discovery-
  agent staffing internally (M2 Staffer). `/think` is a required companion plugin: M5
  Finalizer delegates the finalize stage to the `/think` router, which selects the
  methodology by intent (ADR-12).

## Decision Outcome

Chosen option: "Forbid inkrot, require methodologist", because it honours both user
directives at once. `/hr`'s capability (staffing discovery agents) is reproduced inline in
M2 so the skill never reaches into another marketplace. `/think` is siffran's own plugin, so
declaring it a required companion is in-marketplace composition, not repo-mixing — and it is
mandated by the requirement to support all methodologies by intent.

### Consequences

* Good, because the skill never depends on inkrot — M2 owns staffing with no external call.
* Good, because M5 reuses the full methodologist router instead of a hand-rolled subset, so
  the workflow inherits every current and future methodology for free (ADR-12).
* Bad, because the marketplace must declare methodologist as a required companion, and the
  workflow cannot run its finalize stage without it installed.

### Confirmation

Fitness function: grep the shipped skill for any invocation of `/hr` or an inkrot path —
must return zero call sites. M2 Staffer has a passing test that stages a discovery agent
with no inkrot present. The marketplace/plugin manifest declares methodologist as a
required companion, and the M5 finalize stage invokes `/think`.

## More Information

`/hr` = inkrot marketplace (forbidden). `/think` = methodologist, siffran sibling plugin
(required companion). The `/think` integration — how the methodology is chosen by intent
and how downstream modules derive from its trace — is specified in ADR-12.
