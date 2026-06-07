# Three-Layer Verification (shared spine)

This is the shared conceptual core for the two **requirement-first** methodologies. It is **not selected directly** by the router — it is read on demand by:

- `requirement-first-verification` — *verify to learn* (a human learning a language by verifying generated code)
- `requirement-first-development` — *verify to ship* (an agent developing/verifying code through layered gates)

Both shells define the same six phases. **Phases 1, 3, and 4 are identical across both modes and live here in full.** The shells own only the phases that genuinely diverge by purpose (Phase 2, 5, 6). When executing either shell, read this file first, then follow the shell for the mode-specific phases.

## Core principle

The real content of most code is not in the language. It is a set of **requirements** — orderings, traversal costs, algebraic laws, invariants, preconditions — true on paper, in math, in any language. A language is one particular *encoding and enforcement* of those requirements. So reason in three layers and gate each separately, because **knowing which layer a failure lives in is the entire payoff.**

- **Layer 1 — Requirement (language-free):** the minimal true requirements, stated as properties. Transfers across every language; survives any one dying.
- **Layer 2 — Encoding (the language's costume):** how *this* language names and enforces each requirement. Gate = the toolchain (compiler, type-checker, linter). A failure here is an *encoding* bug.
- **Layer 3 — Harness (behavioral):** the requirements the toolchain *could not* enforce, made executable (property tests, fuzzing, differential tests, sanitizers). Gate = running the harness. A failure here is a *requirement* bug.

The most informative state in the method: **Layer 2 green, Layer 3 red** — code that compiles cleanly but violates its properties. That is exactly what generated code produces and exactly what reading the code would not catch.

## Phase 1 (shared): Write the Layer 1 requirement sheet

Choose one artifact with sharp, legible structure — a fold over a monoid, a merge of sorted sequences, a dedup, a binary search, a small state machine. (How the artifact is *chosen* differs by mode — see the shell.)

Then, with **zero of the target language in your head**, state the minimal true requirements. Discipline: *weakest sufficient requirement* — demand no more than the operation needs (forward single-pass traversal, not random access; a total order, not a specific comparator). State postconditions as **properties** (sorted, permutation-of-input, idempotent, associative, round-trips) — these become the Layer 3 oracle.

**Output format:**
```
Artifact: <what it is, why its requirements are legible>
Inputs: <types/shapes, language-free>
Preconditions: <P1, P2, ... — what the caller must guarantee>
Weakest traversal/ordering requirement: <the least the operation needs>
Postconditions (as properties):
  - <prop 1, e.g. output is a permutation of input>
  - <prop 2, e.g. f(f(x)) == f(x)>
```

## Phase 3 (shared): Obtain code and run the Layer 2 gates

Generate the code (or take the code under review). Run every static gate the language offers — compiler, type-checker, linter, static analysis. For each failure, trace it back to **which requirement it mis-encoded**. The toolchain error usually names a *different* concept than the one you set out to work on — that mismatch tells you which interlocking strand you actually don't understand yet.

**Output format:**
```
Layer 2 gates run: <commands — looked up from current docs, not memory>
Results:
  - <gate>: <pass | fail>
    [If fail] Error names concept: <X>
    [If fail] Mis-encoded requirement: <which Layer 1 line>
    [If fail] Strand exposed: <the interlocking concept the error revealed>
Layer 2 status: <green | red>
```

## Phase 4 (shared): Build and run the Layer 3 harness

Take the predicted leak set from Phase 2 — the requirements no static gate enforced — and make each one executable. Match the tool to the requirement type:

- Algebraic laws / invariants over inputs → **property-based testing**
- Correctness against a known-good answer → **differential testing** vs a reference
- "Any input must not crash/corrupt" → **fuzzing**
- Concurrency data races → **race detectors / sanitizers**
- Concurrency ordering / deadlock / lock-free → **exhaustive interleaving explorers**
- Memory / UB in escape-hatch code → **interpreters / sanitizers that check UB**
- Resource lifecycle (cleanup, leaks) → **instrument teardown and assert it ran**

Each test is a restatement of a Phase 1 property — which is why Phase 1 comes first. The hardest part (stating the oracle) *is* the Stepanov activity of naming the minimal true requirement; very often the missing oracle is an algebraic law.

**Output format:**
```
Harness (one entry per leaked requirement):
  - Property: <the Phase 1 line being checked>
    Tool: <family + the current-docs invocation>
    Result: <pass | FAIL>
    [If fail] Trigger input: <minimal case>
    [If fail] Requirement violated: <which property>
Layer 3 status: <green | red>
Most informative finding: <any "Layer 2 green, Layer 3 red" case>
```

## The Stepanov filter (shared taxonomy)

Both modes classify each requirement by where it got caught. The classification axis is identical; only what you *do* with the log differs by mode (Phase 5).

- **Structural / universal** — orderings, algebraic laws (associativity, identity, idempotence), invariants, permutation/conservation, round-tripping. Transfers to any language; lives in Layer 3.
- **Costume / local** — how this language spells ownership, lifetimes, nullability, error propagation, thread-safety markers, where escape hatches are required. Real and worth knowing, but does not transfer; lives in Layer 2.
