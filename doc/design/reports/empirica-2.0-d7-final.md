# Empirica 2.0 D7/D7-W final implementation report

**Status:** implemented and independently accepted. No staging, commit, push, or PR action.

## Delivered

D7 now provides:

- canonical `s256-…` storage identifiers and strict self-locating `er2` handles;
- one immutable `OperationalState`, decoded/encoded only by `application.run_state`;
- required `committed_artifact_head_id` state-schema amendment;
- strict content-addressed transaction manifests and reachable ordered history;
- pure evaluation and bounded public projection;
- coherent D5 observation capture and immutable spike execution snapshots;
- request-before-execution/result-after-execution spike transactions;
- append-before-CAS, whole-attempt conflict retry, idempotent domain membership, and orphan invisibility;
- revision-checked reads and deduplicated no-op operations;
- post-plan active-head capture when the active set changes, with evaluated-observation reuse when it does not;
- hardened located filesystem/Git bridge composition without a selector index or legacy decoder;
- exact terminal, corruption, graph, budget, route, freeze, and claim-state behavior owned by D7.

## Runtime files added

| File | Physical LOC |
|---|---:|
| `plugins/empirica/core/run.py` | 39 |
| `plugins/empirica/core/evaluation.py` | 350 |
| `plugins/empirica/core/projection.py` | 169 |
| `plugins/empirica/application/location.py` | 128 |
| `plugins/empirica/application/snapshot.py` | 187 |
| `plugins/empirica/application/transaction.py` | 453 |
| `plugins/empirica/adapters/state/located.py` | 25 |

Existing D5/D6 modules, schemas, bridge composition, host tests, and conformance fakes were amended rather than duplicated.

## Transaction proof coverage

`plugins/empirica/tests/test_d7_transactions.py` contains 14 focused tests covering:

- orphan invisibility and manifest ordering;
- missing/wrong-kind/duplicate/corrupt history failure;
- state-witness mismatch;
- CAS retry and content-address idempotency;
- repeated graph/research membership without corruption;
- deduplicated no-op revision retry;
- identical derivation pass idempotency;
- read revision retry;
- immutable spike execution with result-CAS retry and one harness invocation;
- complete post-plan capture for disjoint heads and graph head removal/reactivation;
- state-only commit observation reuse;
- selected missing/malformed graph classification as `graph.invalid`;
- repository corruption and ResolveRun response semantics;
- deterministic projection without private committed-head leakage.

## Verification

| Gate | Result |
|---|---|
| `make empirica-d7-location` | 17 passed |
| `make empirica-d7-transactions` | 14 passed |
| `make empirica-d7-conformance` | 11 passed: D7 cases 2–5, 16, 18–20 and strict cases 45–47 |
| `make check-core` | green |
| `make check-static` | green |
| `make check` | green, including Claude, Codex, and 129 Pi tests |
| `make empirica-architecture-check` | 7,760 LOC / 69 files; zero violations |
| `git diff --check` | green |
| index | empty |

Runtime budget: **7,760**, versus D6-C4 **6,486** and maximum **9,457**. D7 adds **1,274 effective runtime LOC** and remains **1,697 LOC below** the maximum.

The full 50-method D4 suite was intentionally red at the D7 boundary because D8–D10 were still
deferred. Those later milestones are now implemented and the same suite is green; the historical
D7-boundary count was 23 failures and 43 errors (subtest outcomes included).

## Independent review

Astra reviewed the complete implementation, identified transaction-proof defects, and drove regression fixes for repeated artifacts, revision validation, coherent post-plan observations, immutable spike execution, reachable-only corruption, claim-state SSOT, pass accounting, response classification, and policy ownership. After the final evaluated-observation reuse correction, Astra returned:

> **ACCEPT — no remaining BLOCKER or MAJOR in the narrow fix.**

The earlier broad Astra review findings were all addressed and covered by focused tests before the final acceptance check.

## Residual risks

- D8–D10 were outside D7 acceptance and were subsequently implemented and verified.
- No compatibility path exists for v1/raw handles or state lacking the required committed-history head.

No files were staged or committed.
