# Evidence-Preserving Refinement

**Lineage:** Wirth (stepwise refinement, 1971), Parnas (information hiding, 1972), Hoare (pre/postconditions, 1969), Popper (falsifiability, 1934)
**Prevents:** Giant change sets that mix discovery with delivery, retroactive rationale, untraceable implementation, permanent migration scaffolding, and review that cannot isolate one correctness question

## Core principle

Discovery optimizes for learning; promotion optimizes for justification. Preserve the complete experimental history on a discovery branch, but promote only a traceable refinement chain: accepted decision → observable obligation → architecture boundary → implementation → executable evidence → subtraction. A production slice is admissible only when it is green, dependency-closed, invariant-preserving, capability-honest, rollback-bounded, and focused on one primary reviewer question.

### Phase 1: State the change thesis and invariants

Describe the intended outcome without implementation choices. Establish the baseline, the observable change, properties that must remain true, forbidden outcomes, and the deterministic evidence that could disprove success.

**Output format:**
```
Change thesis:
- Baseline: <observed current behavior and source>
- Intended outcome: <technology-independent behavior>
- Invariants: <properties preserved throughout the migration>
- Must not: <negative constraints>
- Falsifiers: <evidence that would show the thesis is wrong>
```

### Phase 2: Explore in an evidence-preserving discovery lane

Resolve material unknowns before shaping production history. Record alternatives at full strength, predictions before experiments, results, rejected paths, and unresolved residuals. Exploration may be messy, but every accepted conclusion must cite code, documentation, or runtime evidence. Preserve the lane under a durable archive reference.

**Output format:**
```
Discovery dossier:
- Unknowns investigated: <list>
- Alternatives tested: <alternative → evidence>
- Accepted findings: <finding → evidence>
- Refuted findings: <finding → falsifier>
- Residuals: <blocked question and why, or none>
- Archive reference: <branch/tag/artifact that preserves the full history>
```

### Phase 3: Freeze decisions and observable obligations

Convert accepted findings into a small decision ledger. Order decisions by how strongly they constrain later choices. For each decision, identify observable obligations, preserved invariants, rejected alternatives, consequences, and reversal conditions. Do not encode implementation detail unless it is itself the decision.

**Output format:**
```
Decision ledger:
- D<n>: <decision>
  - Evidence: <source>
  - Constrains: <later decisions or boundaries>
  - Obligations: <externally observable requirements>
  - Preserves: <invariants>
  - Rejected alternatives: <list with reasons>
  - Reversal condition: <evidence that would reopen the decision>
```

### Phase 4: Construct the refinement and promotion graph

Build a directed acyclic graph whose nodes are decisions, obligations, invariants, boundaries, interfaces, implementation modules, evidence, and removals. Trace every implementation node upward to a decision and every obligation downward to executable evidence. Partition the graph topologically into promotion slices, each with one primary reviewer question and a coherent rollback boundary.

**Output format:**
```
Refinement graph:
- Trace chains: D → obligation/invariant → boundary/interface → implementation → evidence
- Removal chains: replacement decision → unreachable legacy path → deterministic check → deletion
Promotion stack:
- P<n>: <slice>
  - Depends on: <earlier slices>
  - Primary reviewer question: <one question>
  - Evidence shipped with slice: <tests/checks>
  - Rollback boundary: <coherent prior state>
Broken or orphaned chains: <list, or none>
```

### Phase 5: Promote admissible vertical slices

Implement slices in topological order. A slice must be green, trace-complete, invariant-preserving, capability-honest, rollback-bounded, and review-focused. Ship tests with behavior, keep host or transport integrations separate when their capability evidence differs, and attach an expiry condition to every temporary compatibility component.

**Output format:**
```
Promotion ledger:
- P<n>: <slice>
  - Decision/obligation coverage: <ids>
  - Deterministic gates: <commands and results>
  - Capability evidence: <claimed capability → observation>
  - Temporary components: <component → removal prerequisite/owner, or none>
  - Independent review: <review focus and verdict>
  - Admissible: <yes | no, with failed condition>
```

### Phase 6: Subtract, audit traceability, and release

Make the target architecture the only reachable one. Remove legacy entrypoints, fallbacks, duplicate policy/state representations, expired compatibility, and discovery-only artifacts. Measure effective reachable code rather than merely the visible diff. Trace every release claim through the graph and run the committed release gate from a clean tree.

**Output format:**
```
Release proof:
- Obligation coverage: <obligation → implementation → evidence>
- Invariant coverage: <invariant → preserving boundary → gate>
- Subtraction: <removed legacy/temporary paths and reachability evidence>
- Effective size: <baseline → final, counting supported entrypoints>
- Committed release gate: <command and result>
- Independent verdict: <reviewer and verdict>
- Release status: <ready | blocked, with exact residual>
```
