# Empirica 2.0 D4-S2 — exact cases 1–21

**Status:** Parent-frozen serial writer milestone after accepted D4-S1.

## Scope

Correct only cases 1–21 and the smallest transport telemetry/prerequisite helpers they require:

- `test_activation_route_graph.py`
- `test_evidence_freshness.py`
- `test_budget_freeze_terminal.py`
- `assertions.py`
- `driver.py`, `sut_adapter.py`, and `__main__.py` only for policy-free telemetry, trusted-host test
  seam signatures, and preflight signature/sample updates
- D4 report: mark S2 partial and list exactly corrected/outstanding cases

Do not modify cases 22–50 except imports mechanically required by a shared signature. Do not modify
Makefile, contracts, D1–D3, parent D4 spec, runtime, manifests, or old tests.

No second SUT, state model, mutation oracle, or probe. Normal suite remains red at the real seam;
preflight remains structural-only.

## D2C superseding correction

D2C is accepted and supersedes every S2 instruction to inspect invented raw `drv.artifacts()` shapes.
For cases 4–15 and prerequisite helpers, dispatch `GetArgument` and assert the canonical closed ordered
`ArgumentView.artifacts` union. `assert_argument_view` must require `artifacts` and reject the removed
`evidence` field. Artifact sequence, IDs, claim kind, exact active set, digest, request/result binding,
gate, and supersession come only from D2C.

Do not use raw artifact storage to test route/investigate/graph. Graph admission is proved by the typed
ArgumentView claim/edge projection. Route and investigation witnesses are proved by their derived
RunView obligation statuses and exact `route.required`/`route.late` behavior; stable private witness
IDs are not publicly expressible and must not be invented.

Delete S2 raw-artifact assertion helpers and any report text saying production must align to
unspecified test keys. Keep `drv.artifacts()` only where an outstanding S3 case already owned it; cases
1–21 must not call it.

## Trusted application ingress correction

FakeHostChildSink telemetry is not admission. Replace S2 fake-only delivery methods with one adapter-
owned private ingress returned by future v2 composition. Tests never hold/pass a capability. The sole
`sut_adapter.py` seam may prescribe these future composition calls:

```text
trusted_child_event(run_id, child_id, event) -> standard response
trusted_evidence_leaf(run_id, payload) -> standard response
trusted_audit_verdict(run_id, child_id, payload) -> standard response
```

`LiveDriver` invokes those calls and validates/returns the standard public response; it does not only
append to a fake list. Policy-free FakeHost telemetry may additionally record delivery identity, but
cannot satisfy a test. Cases 17/21 first assert the returned response reflects the delivered event
(child state transition or terminal Inert/no-change) before inspecting subsequent RunView. Update
ConformanceDriver/preflight signatures. No concrete runtime implementation is added in D4.

## Review corrections mandatory in S2

In addition to the original case requirements:

- use one stable-prefix canonical graph builder; adding C1 never changes C0 text/digest;
- case 1 asserts the exact complete effective modes object, not key presence;
- case 2 asserts exact `affected`;
- case 3 asserts derived route/investigate obligations and permanent route.late without private IDs;
- cases 6–15 use D2C exact public artifacts and complete active-set digest;
- case 8 calls GetArgument and proves no approved claim;
- case 9 retains old research artifact in public history but excludes it from current active IDs;
- cases 12/15 compare exact normalized observation batches, not membership/counters;
- case 13 maps each variant to exact freshness state and exact reason parameters on both reads;
- case 14 proves both baseline claims approved/fresh and both afterward, with only C0 superseded;
- case 16 explicitly configures independent zero-spawn and max-passes budgets and asserts exact pass
  budget reason/actions/sections;
- case 17 requires a present exact `passes_used` operational fact (no fallback zero) and proves each
  child state through trusted ingress responses;
- cases 18/19 assert exact public gating/deferred claim sets and digest/residual parameters, not opaque
  private frozen-scope truthiness;
- stopped-frozen setup first discharges frozen C0 as an ordinary claim with supporting research, then
  adds C1 after freeze;
- case 21 asserts expected terminal status before delivery, trusted ingress acknowledgement/Inert,
  complete bounded RunView equality, and an unchanged terminal fingerprint;
- regenerate actual method/subtest failure counts and LOC in the partial report.

## Canonical graph/scenario helper

Add one policy-free helper that submits a canonical selected graph with explicit root, ordered claims
(`id`, exact text, `gating`) and edges, then proves admission/public artifact growth. Use fresh driver
instances for independent variants. Claim IDs and texts used by later research/spikes must come from
that graph. Do not rely on undeclared implicit C0.

Graph and public provenance assertions use accepted D2C, not internal storage:

- route/investigate are derived obligation states;
- research artifact ID, claim ID/digest, result;
- `spike_request`: kind, server-generated nonempty harness_request_id, claim ID/digest, command and
  digest, exact nonempty dependent files, ordered prerequisite research artifact IDs;
- spike result: distinct later artifact, same harness_request_id, exact prerequisite IDs,
  exit_code/gate/file bindings, and supersedes when re-gating.

Assert append order by D2C `sequence`. Do not infer type/binding through JSON strings, raw repository
shape, or count growth.

## Policy-free telemetry/test seams

It is permitted to expose immutable fake-port telemetry only for workspace observations and harness
invocations. Trusted lifecycle events use the real private composition ingress defined above; fake
telemetry alone is insufficient.

Update `ConformanceDriver`, `LiveDriver`, fake classes, and S1 preflight signature pairs consistently.
Telemetry records transport facts only and must not derive claim/audit/run policy.

Harness results are staged **before** dispatching `spike_request`; the application must append the
sealed request before invoking the harness and appending the result. Never stage completion after a
synchronous request and call that a completed spike.

## Cases 1–6

1. Assert goal, exact effective modes requested (and required boolean sibling), canonical contract
   identity/digest, exact selected host profile/tier, active status, complete bounded RunView arrays,
   and no full/private dump.
2. Assert exact single `route.required` reason payload, affected values, ordered next_actions/sections,
   and exact relevant sections (ordered where contract order matters).
3. Graph not required here. Record route; investigation must Allow non-converged, append one typed
   investigation witness after route, and a subsequent route must Block with exact permanent
   `route.late` payload without replacing either first-write-wins witness.
4. First admit a valid one-gating-claim graph; without research, Evaluate must Block specifically on
   that claim with `claim.research_missing`, and GetArgument must derive it open/gating.
5. Independent fresh variants for missing selected graph and malformed selected graph; each fails
   closed with exact `graph.invalid`, no generic exception/fallback.
6. Admit graph before refuting research. Assert typed refuting research artifact remains append-only,
   GetArgument derives the claim discarded/refuted with matching evidence digest, and terminal
   evaluation reports exact `claim.refuted`/stopped residual non-convergence. Do not merely observe
   count growth.

## Cases 7–15

7. Admit graph and supporting research in one request, then stage a passing harness and submit the
   spike in a separate request. Assert exact sealed request prerequisites and result binding; use
   GetArgument to prove the same gating claim is approved from the complete active set.
8. For each caller-forged approval field, author-path ObserveAction is Block/Fault, artifact history is
   unchanged, and GetArgument/Evaluate shows no approved claim/convergence.
9. Admit graph/research, then replace the graph with the same ID and changed exact text/digest. Assert
   old research remains history but is absent from current active evidence; exact
   `claim.research_unbound` identifies C0.
10. Admit graph/research, stage deterministic completion, dispatch spike_request, and assert distinct
    typed request then result artifacts in that exact order with matching harness_request_id,
    prerequisite IDs, command/digest and file binding. This must fail if result handling is omitted or
    result precedes request.
11. Use independent fresh runs for forged audit, failing exit, and passing exit. Only exit 0 yields a
    spike gate `pass` and derived approved claim in GetArgument; nonzero yields `fail` and not approved;
    forged agent/audit input never changes machine gate. Do not equate claim approval with final run
    convergence/audit completion.
12. Establish a genuinely approved active spike first. Exercise each state-bearing read
    (`GetRun`, `GetArgument`, `EvaluateRun`) and assert workspace observation history grows for that
    operation with the exact active bound path; assert RunView/Argument freshness path/state where the
    branch exposes it.
13. For each fresh variant `edit`, `missing`, `unreadable`, `non_regular`, `outside_workspace`, first
    establish supporting research plus an approved passing baseline over v1. Mutate only the fake
    workspace fact; next read must Block `claim.spike_stale` with exact path/state matching RunView
    freshness. Repeating the identical observation remains the same stale result and adds no evidence
    artifact. No duplicate delete/absent pseudo-variant.
14. Use two approved gating claims/spikes over separate files. Mutate only C0. Prove only C0 is stale;
    stage one completion and submit a fresh canonical spike_request for C0. Assert exactly one harness
    invocation and one superseding C0 result, prior history preserved, C1 result not superseded or
    rerun, both claims approved/fresh afterward. Assert approval through GetArgument; final convergence
    may still await audit and must not be fabricated.
15. With an approved active spike, Evaluate must add an exact workspace observation-history entry for
    the active bound path through FakeWorkspace; no aggregate counter-only assertion.

## Prerequisite helper corrections

- `require_research_recorded` returns the actual typed research artifact ID and validates claim/result.
- `require_approved_spike` stages harness completion before spike_request, returns request/result
  artifact IDs, then calls GetArgument and requires current claim state `approved` with matching spike
  evidence and freshness. It must not claim approval after observing only a request artifact.
- Add graph and typed artifact lookup helpers; all failures remain `HarnessDefect` diagnostics.

## Cases 16–21

16. Independently verify zero spawn budget and exhausted derivation-pass budget. Assert exact
    `budget.exhausted.parameters.resource`, ordered actions/sections, no reservation on spawn Block,
    and terminal `stopped_budget`/non-converged when pass budget exhausts.
17. Admit an actual child and drive canonical reserved→launching→pending through host events before
    repeated Evaluate/GetRun polling. Compare exact pass-use fact before/after; pending/idle polls do
    not consume a pass.
18. Admit graph C0, freeze, then add C1/change candidate scope and issue repeated/conflicting freeze.
    Assert frozen claim IDs/digest remain byte-for-byte first-write-wins while C1 is deferred.
19. Admit C0, freeze it, then add C1 after freeze. Stop/evaluate and GetArgument must show C0 as the
    only frozen/audited gating scope, C1 in deferred scope, non-null distinct frozen/deferred digests,
    and exact `freeze.deferred` parameters. Do not expect deferred residual without creating C1.
20. Fresh variants establish all canonical terminal non-converged outcomes expressible by D1:
    unresolved/root-refuted → `stopped_residual`, committed discharged scope plus later claim →
    `stopped_frozen`, exhausted pass budget → `stopped_budget`. Assert exact status and
    `converged=false`; do not invent a `failed` run status absent from the registry.
21. For each terminal status above on a fresh run, admit any needed child before terminalization, then
    deliver real trusted late evidence, audit, and child terminal events through the private host test
    seam. Compare complete public RunView status/converged and operational terminal fingerprint before
    and after each event: first terminal remains unchanged and no event reopens/reconverges the run.
    An author-forged trusted action or a new child reservation is not a substitute for a late result.

## Assertion discipline

Each named behavior has direct exact assertions. A multi-part case must execute/assert all parts on a
conforming SUT; do not use an early synthetic marker. No `set(...)` comparison where order is required,
no conditional-only semantic assertion, no self-comparison, no `or True`, no JSON substring typing.

## Report and verification

Report S2 as partial, cases 1–21 statically corrected, cases 22–50 outstanding, and post-binding
semantics unexecuted while seam absent. Recompute LOC; qualify file-attribution claims as S2 changes.

Run:

- `python3 plugins/empirica/tests/v2/__main__.py --preflight` => green
- normal target => exactly 50 methods, red only at absent seam
- `python3 -m py_compile plugins/empirica/tests/v2/*.py`
- `make check-core`, `make check-static`
- grep forbidden permissive/substring/probe patterns
- `git diff --check`; empty index

## Guardrails

Work only in `/private/tmp/empirica-twofold-fix`. Do not search outside it. Do not use reviewer run IDs,
Git refs, `find /`, or recursive root grep. Inputs are this file, parent D4 spec, D1, and accepted D2A/B
JSON. Stop with an exact path if missing. No staging, commit, push, or subagents.
