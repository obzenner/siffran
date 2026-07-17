---
number: 1
title: Record architecture decisions
date: 2026-07-17
status: accepted
tags:
  - meta
  - process
---

# Record architecture decisions

## Context and Problem Statement

We are formalizing a new workflow skill for the siffran marketplace and making a series of
load-bearing architectural decisions. We need a durable, reviewable record of each decision
— its context, the alternatives considered, and why one was chosen — that lives in the repo
and can be validated.

## Decision Drivers

* Decisions must be traceable to their rationale and rejected alternatives.
* The record must live in the repo, not in chat, and be machine-validatable.
* The format should support decision drivers, considered options, and consequences (MADR).

## Considered Options

* **No formal record** — rely on commit messages and memory.
* **Freeform docs** — Markdown notes with no schema or validation.
* **Architecture Decision Records (MADR 4.0.0)** via the `adrs` CLI — structured,
  linkable, and validatable with `adrs doctor`.

## Decision Outcome

Chosen option: "Architecture Decision Records (MADR 4.0.0)", because it gives each decision
a structured home with explicit alternatives and a confirmation/fitness-function section,
and the `adrs` CLI validates the whole set for structural health.

### Consequences

* Good, because every decision carries its rejected alternatives and a way to confirm it.
* Good, because `adrs doctor` mechanically validates the record set.
* Bad, because the MADR structure is more ceremony than a one-line note for trivial choices.

### Confirmation

Fitness function: `adrs --ng doctor` reports zero errors across the ADR directory. Every
decision ADR includes Context, Decision Drivers, Considered Options, Decision Outcome, and
Confirmation sections.

## More Information

MADR 4.0.0 format; tooling: `adrs` 0.7.3 (adr-tools compatible). Lineage: Michael Nygard,
"Documenting Architecture Decisions."
