# Invariant Analysis

**Lineage:** Hoare logic (1969), Floyd (verification of flowcharts), Dijkstra (weakest precondition calculus)
**Prevents:** "Works sometimes" bugs, state corruption, broken assumptions in loops/recursion, data integrity violations

## Core principle

A program is correct when you can state what must be true at every point in execution and prove that the code maintains those truths. When something "sometimes breaks," an invariant is being violated — the job is to find which one.

## Phases

### Phase 1: Identify the operation

What specific operation, function, loop, or state transition are we analyzing? Narrow the scope to one unit of behavior.

**Output format:**
```
Operation: <name/description>
Scope: <function | loop | state transition | data flow>
Location: <file:line or conceptual location>
Trigger: <what causes this operation to execute>
```

### Phase 2: State preconditions

What must be true BEFORE this operation executes for it to behave correctly? These are the promises the caller must keep.

For each precondition, state:
- The condition itself
- What breaks if it's violated
- Whether it's currently enforced (checked) or assumed (unchecked)

**Output format:**
```
Preconditions:
- P1: <condition> — violation causes: <what breaks> — <enforced | assumed>
- P2: <condition> — violation causes: <what breaks> — <enforced | assumed>
```

### Phase 3: State postconditions

What must be true AFTER this operation completes? These are the promises this operation makes to its caller.

**Output format:**
```
Postconditions:
- Q1: <condition> — guaranteed by: <what in the code ensures this>
- Q2: <condition> — guaranteed by: <what in the code ensures this>
```

### Phase 4: Identify invariants

What must remain true DURING execution? This is where bugs hide.

Types of invariants:
- **Loop invariant:** True before the loop, maintained by each iteration, true after the loop
- **Data invariant:** A property of a data structure that must hold between all operations (e.g., "sorted", "non-empty", "sum equals total")
- **State machine invariant:** Valid transitions — which states can follow which
- **Concurrency invariant:** What must hold across threads/processes

For each invariant, identify:
- The condition
- What maintains it (which code preserves this truth)
- What could violate it (which operations are dangerous)

**Output format:**
```
Invariants:
- I1 [loop | data | state | concurrency]: <condition>
  - Maintained by: <what preserves it>
  - Threatened by: <what could violate it>
- I2 [type]: <condition>
  ...
```

### Phase 5: Verify or find violation

For each invariant, trace through the code:
1. Is it established correctly at initialization?
2. Is it preserved by every operation that touches the relevant state?
3. Are there paths that bypass the maintenance code?

When you find a violation, characterize it:
- **Input that triggers it** (or condition that enables it)
- **Which invariant is violated**
- **Why the current code doesn't prevent it**

**Output format:**
```
Verification:
- I1: <holds | violated>
  Evidence: <code path or proof>
  [If violated] Trigger: <what causes it>
  [If violated] Root cause: <why the code doesn't prevent it>
```

### Phase 6: Produce conclusion

State what was found and what it means:
- Which invariants hold and can be relied upon
- Which are violated and need fixing
- What the fix should preserve (don't break working invariants while fixing broken ones)

**Output format:**
```
Conclusion:
- Sound invariants: <list>
- Violated invariants: <list with root causes>
- Recommended fix: <what to change, with which invariants it must preserve>
```
