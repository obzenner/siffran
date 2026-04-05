# Proof by Contradiction

**Lineage:** Reductio ad absurdum (Aristotle → formal logic), indirect proof in mathematics
**Prevents:** Decision paralysis between options, hidden assumptions in "obvious" choices, confirmation bias when evaluating approaches

## Core principle

To determine whether a proposition is true, assume it is false and derive consequences. If the consequences are absurd (contradict known facts or axioms), the original proposition must be true. In engineering: to decide between options, assume one is wrong and see if that assumption leads somewhere impossible.

This is the methodology for when direct reasoning is hard but the negation produces clear signal.

## Phases

### Phase 1: State the proposition

What are we trying to evaluate? Frame it as a clear, falsifiable claim.

If comparing options A vs B, frame as: "A is the correct choice" — then we'll assume the negation ("A is NOT the correct choice, therefore B is") and see what follows.

**Output format:**
```
Proposition P: <clear, falsifiable statement>
Negation ¬P: <what the world looks like if P is false>
```

### Phase 2: Establish known truths

What do we know for certain about this context? These are the facts that any valid conclusion must be consistent with. Sourced from code, docs, measurements, constraints — not opinions.

**Output format:**
```
Known truths:
- K1: <fact> — source: <where this comes from>
- K2: <fact> — source: <where this comes from>
```

### Phase 3: Assume the negation

Explicitly assume ¬P is true. State what this means concretely. What would the system look like? What decisions would follow? What would we build?

This is the hard part — you must take the negation seriously, not strawman it. Steel-man the negation.

**Output format:**
```
Assuming ¬P:
- Consequence C1: <what follows from ¬P>
- Consequence C2: <what follows from C1>
- Consequence C3: <what follows from ¬P + C1>
```

### Phase 4: Derive until contradiction (or don't)

Follow the consequences of ¬P until one of two things happens:

**Contradiction found:** A consequence of ¬P contradicts a known truth (K1, K2, etc.). This means ¬P is false, therefore P is true.

**No contradiction found:** The negation is consistent with all known truths. This means P is NOT proven — the negation is viable. Either P is wrong, or you need more known truths to distinguish.

Be honest. If no contradiction exists, say so. A failed proof by contradiction is valuable — it means your "obvious" choice isn't as obvious as you thought.

**Output format (contradiction found):**
```
Contradiction:
- C<n> states: <consequence>
- K<m> states: <known truth>
- These are incompatible because: <explanation>
- Therefore ¬P is false, P holds.
```

**Output format (no contradiction):**
```
No contradiction found.
- ¬P is consistent with all known truths
- This means: <P is not the only valid choice | we need more information>
- Missing information: <what would distinguish P from ¬P>
```

### Phase 5: Check the contrapositive

Even if you found a contradiction, verify the result by checking: does P (the original proposition) lead to any contradictions with known truths? If P ALSO contradicts known truths, neither option is viable and the problem is mis-framed.

**Output format:**
```
Contrapositive check:
- Assuming P: <consequences>
- Contradicts known truths: <yes — which ones | no>
- Conclusion: <P is sound | P also has problems — reframe needed>
```

### Phase 6: Produce conclusion

State the result and what it means for the decision.

**Output format:**
```
Result: <P proven | P disproven | undecidable with current information>
Reasoning: <the contradiction chain, or why no contradiction was found>
Decision: <what to do>
Confidence: <high if contradiction was clean, low if it required assumptions>
```
