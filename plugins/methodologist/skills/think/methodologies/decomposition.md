# Functional Decomposition

**Lineage:** Dijkstra (structured programming), Parnas (information hiding, 1972), Simon (hierarchy in complex systems)
**Prevents:** God objects, tangled dependencies, "where does this belong?" paralysis, premature abstraction without boundary analysis

## Core principle

A complex problem is solved by identifying its parts, defining the interfaces between them, and solving each part independently. The decomposition is correct when each part can be understood, built, and tested without knowledge of the others' internals.

Parnas's criterion: decompose on the basis of **what is likely to change**, not on the basis of flowchart steps.

## Phases

### Phase 1: State the whole

Define the complete problem in one paragraph. What goes in, what comes out, what are the constraints. If you can't state the whole clearly, you can't decompose it correctly.

**Output format:**
```
Problem statement:
- Input: <what enters the system>
- Output: <what the system produces>
- Constraints: <non-functional requirements, invariants, boundaries>
```

### Phase 2: Identify change axes

What are the independent reasons this system might need to change? Each axis of change suggests a module boundary.

Sources of change:
- Data format/schema
- Business rules
- External dependencies (APIs, DBs, services)
- User-facing behavior
- Performance characteristics
- Platform/environment

**Output format:**
```
Change axes:
- C1: <what might change> — <why it's independent from others>
- C2: <what might change> — <why it's independent from others>
```

### Phase 3: Define modules

Each change axis maps to a module. For each module, define:
- **Secret:** What information does this module hide? (Parnas's key question)
- **Interface:** What does the rest of the system need to know to use this module?
- **Responsibility:** One sentence — what does this module DO?

**Module test:** If two modules need to know each other's internals to work, they're either one module or the interface is wrong.

**Output format:**
```
Modules:
- M1: <name>
  - Secret: <what it hides>
  - Interface: <what it exposes>
  - Responsibility: <one sentence>
- M2: <name>
  ...
```

### Phase 4: Map dependencies

Draw the dependency graph between modules. Identify:
- Which modules depend on which
- Whether dependencies are one-directional (good) or circular (problem)
- Which module is the most depended-upon (stability requirement)

**Dependency rules:**
- Stable things should not depend on unstable things
- Depend on interfaces, not implementations
- If A depends on B and B depends on A, you have a missing abstraction between them

**Output format:**
```
Dependencies:
- M1 → M2 (uses interface: <what>)
- M3 → M1 (uses interface: <what>)
Cycles: <none | describe>
Stability order: <most stable first>
```

### Phase 5: Validate decomposition

For each module, check:
1. Can it be implemented by someone who only knows its interface and the interfaces of its dependencies?
2. Can it be tested in isolation (with mocked dependencies)?
3. If its secret changes, does anything outside the module need to change?

If any answer is no, the decomposition has a leak. Fix it before proceeding.

**Output format:**
```
Validation:
- M1: <independent: yes/no> — <evidence>
- M2: <independent: yes/no> — <evidence>
Leaks found: <none | describe and fix>
```

### Phase 6: Produce implementation order

Order modules by dependency — build leaves first, then their dependents. Identify which modules can be built in parallel.

**Output format:**
```
Build order:
1. <module> (no dependencies, build first)
2. <module> (depends on 1)
3. <module> + <module> (parallel, both depend on 1-2)
```
