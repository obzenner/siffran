# Abstraction Refinement

**Lineage:** Wirth (stepwise refinement, 1971), Dijkstra (layers of abstraction), Back (refinement calculus)
**Prevents:** Premature detail, analysis paralysis from too many options, architectures that are correct in parts but incoherent as a whole, solutions that solve the wrong problem at the right level of detail

## Core principle

Start with the most abstract correct solution — one that captures WHAT must happen without specifying HOW. Then refine toward implementation in discrete steps, where each step adds detail while preserving the correctness of the level above. If you can't refine without breaking the abstraction above, the abstraction is wrong.

The key insight from Wirth: the order of decisions matters. Decide the most important things first (data representation, module boundaries) and defer details (algorithms, optimizations) until the structure is settled.

## Phases

### Phase 1: State the abstract solution

Describe what the system must do in the most general terms possible. No implementation details. No technology choices. No data structures. Just behavior.

**Test:** Could this description apply to a manual process, a script, a microservice, and a monolith equally? If it mentions specific technologies, you're not abstract enough.

**Output format:**
```
Abstract solution:
- The system accepts: <what>
- The system produces: <what>
- The system guarantees: <behavioral properties>
- The system must not: <negative constraints>
```

### Phase 2: Identify the key decisions

What are the decisions that, once made, constrain everything else? These are your refinement axes. Order them by impact — the decision that constrains the most subsequent decisions comes first.

Typical decision ordering:
1. Data representation (what is the core data model?)
2. Module boundaries (what are the parts?)
3. Communication patterns (how do parts interact?)
4. Algorithms (how does each part do its job?)
5. Optimizations (how do we make it fast?)

**Output format:**
```
Key decisions (ordered by impact):
- D1: <decision> — constrains: <what subsequent decisions this affects>
- D2: <decision> — constrains: <what>
- D3: <decision> — constrained by: D1, D2
```

### Phase 3: First refinement — data and boundaries

Make the highest-impact decisions. Define the core data representation and module boundaries. This is the level where architectural mistakes are most expensive.

**Refinement rule:** The refined version must satisfy every property stated in the abstract solution. If it doesn't, the refinement is wrong — not the abstraction.

**Output format:**
```
Refinement 1:
- Data model: <core types and their relationships>
- Modules: <what parts exist and what each is responsible for>
- Verification: <for each abstract property, how this refinement preserves it>
```

### Phase 4: Second refinement — interfaces and flow

Define how the modules communicate. What data crosses boundaries? What are the contracts? What is the flow of control?

**Output format:**
```
Refinement 2:
- Interfaces: <what each module exposes>
- Data flow: <how data moves between modules>
- Control flow: <what triggers what>
- Verification: <how this preserves properties from refinement 1>
```

### Phase 5: Third refinement — algorithms and implementation

Now — and only now — choose algorithms, libraries, concrete data structures. Each choice is constrained by the decisions above. If a choice conflicts with a higher-level decision, the choice is wrong, not the higher-level decision (unless you discover the higher-level decision was based on a false assumption — then go back and fix it).

**Output format:**
```
Refinement 3:
- For each module:
  - Algorithm/approach: <what and why>
  - Data structures: <concrete choices>
  - Dependencies: <libraries, services>
- Verification: <how this preserves properties from refinement 2>
```

### Phase 6: Validate the refinement chain

Trace from bottom to top: does the implementation (refinement 3) satisfy all properties stated in the abstract solution (phase 1)?

For each abstract property, show the chain:
- Abstract property → refinement 1 decision that supports it → refinement 2 interface that implements it → refinement 3 algorithm that executes it

If any chain is broken, identify WHERE it breaks and which refinement level needs adjustment.

**Output format:**
```
Validation:
- Property "<X>": abstract → R1(<decision>) → R2(<interface>) → R3(<algorithm>) — <chain intact | breaks at R<n>>
Broken chains: <none | list with fix recommendations>
```
