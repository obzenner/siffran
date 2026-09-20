# Empirica 2.0 D2E — canonical presentation selector registry

**Status:** Parent-frozen bounded amendment exposed by D4 cases 37–40.

## Problem

D1 §12 names deterministic selector inputs and context behavior, but D2 only registers
`operation_contexts`; it does not register ordered context sections, terminal addition, unknown-code
fallback, or the union algorithm. Tests therefore invented a fallback that contradicts D1.

## Scope

Add canonical presentation-selector data/clauses to PublicContract, schema, materialized GetContract
fixtures, validator parity/negatives, and D1/D2 spec clarification. No D3/D4/runtime/Make/manifests.

## Canonical registry field

Add closed `presentation_selector`:

```json
{
  "context_sections": {
    "bootstrap": ["core", "roles", "hosts/capabilities"],
    "block": [],
    "auditor": ["audit", "audit/independence", "evidence/research", "evidence/spike", "evidence/order", "evidence/freshness"],
    "restore": ["core", "run/lifecycle", "children", "hosts/capabilities", "recovery"],
    "terminal": ["terminal"],
    "on_demand": []
  },
  "terminal_sections": ["terminal"],
  "unknown_reason_sections": ["core", "run/lifecycle", "recovery"]
}
```

Every key exactly equals registered `operation_contexts`; every value is ordered/unique and resolves
to `sections`. No duplicated Python table.

## Pure algorithm contract

Input is exactly operation_context, ordered emitted reason codes, and terminal status.

1. Unknown operation context, unknown reason code, unknown reason-linked action, or unresolved section
   returns exact `unknown_reason_sections`; it does not partially union known inputs.
2. Otherwise append `context_sections[operation_context]`, then each reason's registered `sections`
   in input reason order, first occurrence wins.
3. If terminal status is non-null, it must be a registered terminal status and append
   `terminal_sections`, first occurrence wins. A nonterminal/unknown non-null status fails to the exact
   fallback.
4. Output is ordered, duplicate-free, and contains no other sections. Identical inputs yield identical
   output. Selector reads no domain state.

`block` has no copied base section: obligation and next-action guidance comes through the emitted
reason's registered sections. `on_demand` is empty because GetContract directly resolves the requested
section/target rather than inferring policy.

## Progressive discovery

Add concise `presentation/selector` section clauses describing inputs, ordered union, exact fallback,
and domain-state independence. GetContract index/section/full must discover it with canonical digest.
JSON registry remains executable SSOT; prose does not duplicate lists.

## Schema and validator

PublicContract schema requires the closed object and exact operation-context keys. JSON Schema
ensures arrays of nonempty unique strings. Procedural validator ensures exact key parity, every
section resolves, terminal status vocabulary is correct, reason section/action references resolve,
and materialized fixtures/digests remain current. Add one-mutation negatives for missing/extra context,
duplicate/unknown section, empty fallback, unknown reason section, and digest drift.

## Verification

`make contract-check`, `make check-static`, focused raw/procedural probes, `git diff --check`, empty
index. No presentation selector implementation or behavioral model in validator.

## Guardrails

Worktree only. No outside search/reviewer IDs/Git refs, D4 edits, staging, commit, push, or subagents.
