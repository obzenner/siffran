# Requirement-First Verification

**Lineage:** Stepanov (generic programming — requirements over syntax), Hoare (pre/postconditions, 1969), Popper (falsifiability), property-based testing (QuickCheck, Claessen & Hughes, 2000)
**Prevents:** Illusion-of-understanding from reading generated code, conflating "compiles" with "correct", learning a language as a disconnected feature list, accepting code that is valid in the language but violates its actual requirements

## Core principle

The real content of most code is not in the language. It is a set of **requirements** — orderings, traversal costs, algebraic laws, invariants, preconditions — true on paper, in math, in any language. A language is one particular *encoding and enforcement* of those requirements. So reason in three layers and gate each separately, because **knowing which layer a failure lives in is the entire payoff.**

- **Layer 1 — Requirement (language-free):** the minimal true requirements, stated as properties. Transfers across every language; survives any one dying.
- **Layer 2 — Encoding (the language's costume):** how *this* language names and enforces each requirement. Gate = the toolchain (compiler, type-checker, linter). A failure here is an *encoding* bug.
- **Layer 3 — Harness (behavioral):** the requirements the toolchain *could not* enforce, made executable (property tests, fuzzing, differential tests, sanitizers). Gate = running the harness. A failure here is a *requirement* bug.

The most informative state in the method: **Layer 2 green, Layer 3 red** — code that compiles cleanly but violates its properties. That is exactly what generated code produces and exactly what reading the code would not catch.

This is the default discipline for learning-through-verification: when the user wants to learn a language by *verifying generated code* rather than authoring it. The prediction step (Phase 2) and the gate-tracing step (Phase 3) are the friction that makes it work — without them the loop collapses into "run it, looks fine, next," and teaches nothing.

## Phases

### Phase 1: Pick artifact and write the Layer 1 requirement sheet

Choose one artifact with sharp, legible structure — a fold over a monoid, a merge of sorted sequences, a dedup, a binary search, a small state machine. Avoid sprawl; you want the requirements legible.

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

### Phase 2: Predict the Layer 2 encoding

For each Layer 1 requirement, name the language feature you expect to encode it, and predict where the toolchain will **catch** a violation versus where it will stay **silent**. Writing the prediction down is mandatory — the gap between prediction and reality is the lesson. Skipping it collapses the method into passive reading.

**Output format:**
```
Encoding predictions:
  - Requirement <Px/property> → feature <the language's costume for it>
    Toolchain verdict prediction: <caught at compile/lint | silent — leaks to harness>
    Confidence: <high | low> — <why>
Predicted leak set (requirements no gate will catch): <list — these drive Phase 4>
```

### Phase 3: Obtain code and run the Layer 2 gates

Generate the code (or take the code under review). Run every static gate the language offers — compiler, type-checker, linter, static analysis. For each failure, trace it back to **which requirement it mis-encoded**. The toolchain error usually names a *different* concept than the one you set out to work on — that mismatch tells you which interlocking strand you actually don't understand yet.

**Output format:**
```
Layer 2 gates run: <commands — looked up from current docs, not memory>
Results:
  - <gate>: <pass | fail>
    [If fail] Error names concept: <X>
    [If fail] Mis-encoded requirement: <which Layer 1 line>
    [If fail] Strand I didn't understand: <the interlocking concept the error exposed>
Layer 2 status: <green | red>
```

### Phase 4: Build and run the Layer 3 harness

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

### Phase 5: Log the split (the Stepanov filter)

Classify each requirement by where it got caught. This accumulating log IS the architect's map — a principled account of what each language's features actually buy you, derived from watching requirements get caught or leak, not from a features list.

- **Structural / universal** — orderings, algebraic laws (associativity, identity, idempotence), invariants, permutation/conservation, round-tripping. The transferable skill; lives in Layer 3.
- **Costume / local** — how this language spells ownership, lifetimes, nullability, error propagation, thread-safety markers, where escape hatches are required. Real and worth knowing, but does not transfer; lives in Layer 2.

**Output format:**
```
Caught for free by the toolchain (tight costume): <requirements + which feature caught them>
Leaked to the harness (language can't help here): <requirements>
Classification:
  - Universal (transfers to any language): <list>
  - Local (this language's costume): <list>
Strand still weak: <the concept I keep snagging on — my real curriculum>
```

### Phase 6: Conclusion and next artifact

State what was learned and pick the next artifact to pull a *different* cluster of interlocking strands (a custom iterator, an enum state machine, a generic container, a small concurrent structure — each lights up a different overlapping cluster). Re-hit some concepts, add new ones, so the web gets covered from several angles.

**Output format:**
```
Conclusion:
  - Code verdict: <correct & idiomatic | encoding bug | requirement bug — with evidence>
  - What this language's costume bought: <which requirements it enforced for free>
  - What stayed on me: <requirements only the harness could catch>
  - Lesson (prediction vs reality gap): <where Phase 2 was wrong, and why>
Next artifact: <what to pick next, and which new strands it forces to interlock>
```
