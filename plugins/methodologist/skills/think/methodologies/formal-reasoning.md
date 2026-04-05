# Formal Reasoning: Axioms, Theorems, Proofs

**Lineage:** Mathematical logic, axiomatic systems (Euclid → Hilbert → Gödel)
**Prevents:** Blind rule enforcement, blind rule violation, unjustified exceptions, cargo cult conventions

## Phases

### Phase 1: Dump raw material

Collect everything relevant to the decision or rule in question — conventions, constraints, observations, requirements, existing code, docs. Don't filter. This is raw material.

**Output format:**
```
Raw material:
- [item]: <description>
- [item]: <description>
...
```

### Phase 2: Extract axioms

From the raw material, extract statements that are self-evidently true or directly observable. For each, classify:

- `constraint` — cannot be changed (technical limits, regulatory, organizational)
- `capability` — can be leveraged (tools, frameworks, team skills)
- `structural` — just how things are (neither good nor bad, but shapes everything)

**Axiom test:** Can you observe this directly without inference? If it requires reasoning from other things, it's probably a theorem, not an axiom. Keep digging for the parent.

**Output format:**
```
Axioms:
- A1 [constraint]: <statement>
- A2 [capability]: <statement>
- A3 [structural]: <statement>
```

### Phase 3: Derive theorems

For each pair or group of axioms, ask: "If these are both true, what necessarily follows?"

**Theorem rules:**
- Every theorem MUST cite parent axioms by identifier
- If you can't trace a claim to axioms, it's either a missing axiom or an unsupported opinion
- A theorem depending on an axiom that doesn't hold in this context doesn't apply in this context
- Theorems can chain (T3 from T1 + A4) but the chain must reach axioms

**Output format:**
```
Theorems:
- T1: <claim> (from A1, A2)
- T2: <claim> (from A1, A3)
- T3: <claim> (from T1, A4)
```

### Phase 4: Identify open questions

What can't you derive from your axioms? Where are you guessing?

Tag each:
- `needs-data` — answerable with research or measurement
- `needs-decision` — requires human judgment
- `needs-experiment` — answerable only by trying

**Output format:**
```
Open questions:
- Q1 [needs-data]: <question>
- Q2 [needs-experiment]: <question>
```

### Phase 5: Validate axioms

For each axiom, actively try to break it:
- Search for counterexamples
- Check if it's still true (technologies change, constraints lift)
- Ask: if this axiom were wrong, which theorems collapse?

An axiom that fails validation is the most valuable finding — it means your derived reasoning has a flaw you can fix before it becomes a problem.

**Output format:**
```
Validation:
- A1: <holds | broken> — <evidence>
- A2: <holds | broken> — <evidence>
```

### Phase 6: Apply to context

Check which axioms hold in THIS specific context. The theorems derived from those axioms apply. The rest don't.

**Output format:**
```
Application:
- T1: <applies | does not apply> — <why>
- T2: <applies | does not apply> — <why>

Decision: <what to do, traced to specific theorems>
```

## Common patterns

- **Convention has an exception:** Decompose → find the axiom → check if axiom holds here → if not, convention doesn't apply → document why
- **Two conventions conflict:** Decompose both → axioms reveal which is more fundamental in this context
- **No convention exists:** State observations as axioms → derive theorems → the theorem IS the convention → propose it with its derivation
- **Axiom was wrong:** Revise → re-derive theorems → flag past decisions based on old axiom for review
