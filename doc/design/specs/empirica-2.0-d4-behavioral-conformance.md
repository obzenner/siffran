# Empirica 2.0 D4 implementation spec — red-first behavioral conformance

**Status:** Frozen writer specification.

**Writer:** one GLM run, sole writer.

**Normative inputs:** D0, D1, D1-H, accepted D2 registry/schemas, accepted D3 validator, final DAG §4 and D4.

## 1. Goal

Create independent, black-box, red-first behavioral conformance tests for the complete preserved 2.0
behavior matrix. D4 changes no production runtime. It proves the current tree does not yet satisfy v2
and gives D5–D10 executable acceptance criteria.

The standalone target is intentionally RED and is not composed into ordinary suites until the relevant
implementation nodes are green. Existing suites remain green.

## 2. Files and Make target

Create only test/support/report files under:

```text
plugins/empirica/tests/v2/
  __init__.py
  driver.py
  sut_adapter.py
  assertions.py
  test_activation_route_graph.py
  test_evidence_freshness.py
  test_budget_freeze_terminal.py
  test_async_children.py
  test_audit.py
  test_projection_context.py
  test_protocol_host.py
  test_compaction.py
  fixtures/                      # only when data improves clarity

doc/design/reports/empirica-2.0-d4-red-first.md
```

Update `Makefile` with one visible target:

```text
empirica-v2-conformance  ## run Empirica 2.0 host-neutral behavioral conformance
```

Do not compose it into `check-core`, `check-static`, `check`, or `check-ci` in D4. Do not change the
accepted architecture target/config/validator, D2 contracts, runtime, manifests, or old tests.

## 3. Black-box driver boundary

Tests target one test-only protocol in `driver.py`; they do not import core policy functions and do not
reimplement the runtime algorithm.

```python
class ConformanceDriver(Protocol):
    def request(self, envelope: dict) -> dict: ...
    def workspace_write(self, path: str, content: bytes) -> None: ...
    def workspace_delete(self, path: str) -> None: ...
    def workspace_error(self, path: str, code: str) -> None: ...
    def harness_complete(self, command: str, exit_code: int,
                         files: dict[str, bytes] | None = None) -> None: ...
    def child_native_event(self, child_id: str, event: dict) -> None: ...
    def reload(self) -> "ConformanceDriver": ...
    def compact(self) -> dict: ...
    def artifacts(self) -> tuple[dict, ...]: ...
    def operational_state(self) -> dict: ...
```

`new_driver(profile_id=..., limits=..., clock=...)` is the only SUT factory used by tests.
`sut_adapter.py` is the sole place allowed to know the future application composition import or
service method names; driver/tests depend only on `ConformanceDriver`. In D4 the adapter attempts to
compose the real application through fake ports and fails explicitly when absent. `reload()` and
`compact()` always return/wrap the public driver shape rather than exposing an internal service.
Do not implement a model/stub that returns expected answers. Do not put policy in the driver. Every
normal test reaches the SUT factory independently and fails with its test name/expected behavior
rather than aborting collection in one import error.

Fakes own only observations and transport facts: workspace bytes/errors, deterministic harness exit,
clock, repositories, host child events. They never calculate approval, reason codes, claim state,
audit coverage, budgets, context relevance, or convergence.

## 4. Assertion discipline

- Send valid `empirica/v2` request envelopes matching D2 schemas. Every normal dispatch validates the
  request centrally before the SUT sees it. A separately named `raw_dispatch` is used only by strict
  decoder negative tests 45–47.
- Host profile selection is a driver-factory/environment fact, never an invented StartRun field.
- Re-gate is requested by a new canonical `spike_request` for the stale active head; next-action ID
  `spike.regate` is guidance, not a wire action kind.
- Validate every response against `response.schema.json` before semantic assertions.
- Load reason/action/section IDs from `public-contract.json`; do not copy canonical tables.
- Assert stable structured facts: result type, status, reason code/parameters, affected obligation or
  witness, exact next-action IDs/section IDs, artifact append count/type/digest relation, pass/spawn
  counters, child state, and selected contract sections.
- Do not assert mutable prose except historical UX safety properties (goal visible, delimiters present,
  unsupported explicit, no full dump).
- Each test has one primary behavior and cannot pass because another prerequisite failed. Policy-free
  setup helpers drive and assert public prerequisites (started run, routed committed claim, approved
  baseline spike, admitted child, frozen scope) without calculating expected policy.
- Every case has an exact observable postcondition that rejects a schema-valid no-op/wrong result;
  conditional assertions must assert every allowed branch exactly, and always-true alternatives are
  forbidden.
- Each edit/delete/absent/unreadable or terminal/host variant starts from a fresh driver/run.
- No mocks of functions under test; use fake ports at the ownership boundaries from D1.
- No sleeps, wall clock, filesystem outside a temporary directory, network, host CLI, or model.

## 5. Required red-first cases

### Activation, route, graph, historical UX

1. Start exposes goal, modes, contract identity/digest, and selected exact host profile/tier without a
   full contract dump.
2. Route witness must precede investigate; investigate-first Blocks with the routing reason, affected
   witness/obligation, action-local next action, and only relevant sections.
3. Valid route then investigation proceeds.
4. Claim state is derived; only committed support scope gates.
5. Malformed/missing selected graph fails closed with a structured reason.
6. Refuted/discarded claim remains append-only history and needs real refuting evidence.

### Evidence, order, freshness, re-gate

7. Research and spike submitted in separate requests combine on the same active claim.
8. Caller-supplied `ok`, verdict, timestamp, artifact digest, or attribution cannot approve evidence.
9. Research is bound to current claim wording; wording change makes it inapplicable.
10. A trusted sealed spike request containing nonempty dependent files precedes the harness result.
11. Passing harness exit is the only machine approval; agent/audit claims cannot manufacture it.
12. Every state-bearing request freshly observes every active bound file.
13. Bound-file edit, delete, absent, and unreadable/error each immediately produce stale/indeterminate
    spike status and a structured Block; repeated identical observation is stable.
14. Re-gate runs only stale spikes, appends a new attestation, preserves old history, and restores
    approval after a passing deterministic result over current observations.
15. Pure-core boundary is separately asserted by D3; D4 black-box tests must show workspace reads are
    requested through the fake port, never hidden behind adapter behavior.

### Budget, freeze, terminal

16. Derivation and spawn limits enforce structured budget Blocks.
17. Idle waiting and pending audit/child consume no derivation pass.
18. Freeze is first-write-wins under repeated/conflicting requests.
19. Committed frozen scope remains the only gating/audited scope; later claims are deferred explicitly.
20. Stopped, frozen, budget-exhausted, refuted, and failed runs are honestly non-converged.
21. A late evidence/audit/child result after any terminal state never reconverges or reopens the run.

### Async children

22. Canonical requested/launching/pending/terminal transitions follow D2 registry only.
23. Admission binds one run-scoped child ID and, where supported, exactly one native ID.
24. Completion is exactly once across duplicate and out-of-order events.
25. First terminal event wins; every later event changes neither child state, budget, audit, nor
    convergence.
26. Pending state survives reload and compaction without duplicate spawn or pass consumption.
27. Launch rejection refunds exactly once; post-start failure does not refund.
28. Cancellation, timeout, and orphan recovery produce their exact state/recovery action.
29. Foreground-only/observational hosts return typed unsupported capability reasons rather than silent
    fallback; current tiers match D1-H/D2 exact profiles.

### Audit

30. Author cannot submit an admitted audit verdict or trusted attribution.
31. Dossier binds claim/evidence/frozen goal/scope/deferred digests and includes untrusted-data
    delimiters.
32. Audit verdict is observed from the trusted host boundary, admitted once, and cannot approve
    deterministic machine evidence.
33. Independence is derived from trusted observed attribution only and reports
    `decorrelated | same_model | unverified` honestly.
34. Same-model/unverified independence can Block according to public reasons but is never upgraded by
    author input.
35. Frozen scope audit covers committed scope and deferred digest exactly.

### Structured Blocks, progressive contract, context, compaction

36. Every Block has nonempty structured reasons; each reason exactly matches canonical parameters,
    ordered next actions, and ordered section IDs, and identifies affected obligation/witness when the
    reason is scoped.
37. Identical operation context + ordered reason codes + terminal status selects identical sections.
38. Selection does not change when obligations, witnesses, budget, child, workspace, or host details
    change while those presentation inputs remain fixed.
39. Unknown reason/action/section fails closed to the minimal safe protocol/recovery section.
40. Ambiguous/multiple reasons preserve deterministic canonical order and exclude irrelevant sections.
41. GetContract index/section/full returns exactly the requested projection and digest identity.
42. Ordinary Start/Block/Allow/compaction never dumps the full PublicContract.
43. Compaction preserves goal, modes, contract identity, active/deferred obligations, witness states,
    pending children, next actions, and explicit untrusted-data delimiters.
44. Reload from compaction/current repositories derives the same public view without persisted
    obligation contract revision/pointer/history.

### Strict v2 and host honesty

45. Exact `empirica/v2` state/protocol identity is checked before semantic decoding.
46. v1 request and old state are rejected clearly with fresh-run recovery; neither is migrated,
    defaulted, or partially decoded.
47. Unknown fields/actions/statuses fail closed.
48. Host profile and tier are exact-registry values; schema parity never upgrades enforcement.
49. Claude foreground-only, native Pi foreground-only, Codex observational, and Pi+pi-subagents
    candidate behavior match D1-H until live conformance explicitly changes the registry.
50. Private capability never appears in public responses, compaction, artifacts, or diagnostics.

At least these 50 named cases must exist. A case may use subtests for the enumerated edit/delete/error
or terminal variants, but it must report each variant independently.

## 6. Honest red-state and structural preflight

D4 does not implement a second SUT, mutation oracle, run model, child state machine, checkpoint
classifier, or behavior-aware probe. Post-binding semantics cannot execute until the real v2 seam
exists; the report states that limitation explicitly.

### Real-SUT red mode

The normal run executes/collects the complete suite and fails because the real v2 SUT seam or
behavior is absent—not because fixtures are invalid, schemas cannot load, tests have syntax errors,
or one global import aborts collection.

### Structural preflight mode

A green `--preflight` mode performs no behavioral simulation. It verifies:

- exactly 50 named methods import/collect;
- zero skips, expected failures, placeholder/TODO/pass bodies, or duplicate names;
- all static envelope/action builders validate against accepted request schemas;
- response assertion helpers validate representative accepted D2A fixtures and reject representative
  malformed shapes;
- every normal test request routes through central validated dispatch; only strict decoder tests use
  raw dispatch/raw state;
- `ConformanceDriver`, live wrapper, and fake port method signatures agree;
- canonical protocol/identity/profile/state/action/reason/section data is loaded rather than copied;
- forbidden tautology/permissive patterns (`or True`, self-membership, set-only ordered comparisons,
  conditional-only semantic assertion) are absent.

Preflight does not claim behavioral mutation adequacy. Independent review inspects exact assertions;
D5–D10 turn owner subsets green against the real SUT and add focused mutations where the behavior is
then executable.

The red report records:

- total tests collected/executed;
- failures/errors grouped by future owner D5/D5-F, D6, D7/D7-W, D8, D9, D10;
- at least one concrete current false behavior for separate research/spike and stale-file evaluation
  when the existing service can be driven safely;
- zero skips/expected failures;
- preflight and existing suite exit 0 and empty index;
- explicit statement that no post-binding semantic path has executed yet.

Do not weaken expectations to make D4 green. Do not add a baseline exception list.

## 7. Required commands

```text
make empirica-v2-conformance               # expected RED against absent/nonconforming real SUT
make empirica-v2-conformance ARGS=--preflight  # GREEN structural/schema preflight only
make check-core                    # GREEN
make check-static                  # GREEN
make check-claude                  # GREEN
make check-codex                   # GREEN
make check-pi                      # GREEN where local Node/Pi is available
```

Use existing Make targets only. The standalone D4 target invokes one committed test runner command and
appears in `make help`.

## 8. Complexity constraints

- stdlib Python plus already-required `jsonschema` only;
- no second domain model, state machine, mutation oracle, or expected-state probe in tests;
- no copied protocol/contract/profile/reason/action/section/transition tables or identity constants;
  derive them from D2 registries;
- no giant scenario DSL or generated test code;
- shared helpers perform schema/registry assertions and policy-free prerequisite setup only;
- prerequisites include routed committed claim, supporting research + a completed passing baseline
  spike over a named workspace version, canonical reserved→launching→pending child, post-freeze
  deferred claim, and reserved audit child; every helper asserts the public prerequisite and exposes
  actual IDs rather than fabricating them;
- driver/fakes contain no branch on expected reason/status or case ID;
- each test file remains responsibility-focused;
- context cases 37–40 use a test-adapter method exposing the real pure D9 selector with only canonical
  operation_context, ordered reason codes, and terminal status inputs; they do not infer context by
  manufacturing unrelated domain state;
- strict state cases 45–47 may install raw persisted state through a fake repository port before
  RestoreRun; this is test input, not a public command;
- fixtures are minimal external inputs, not snapshots of full registry or full responses.

## 8A. Required corrections exposed by D2A/review

Before D4 acceptance, all existing 50 cases must use the accepted D2A complete RunView and typed
ArgumentView. In particular:

- case 3 requires successful investigate admission and a resulting investigation witness/artifact;
  a Block cannot satisfy it;
- cases 7/10 assert exact typed artifact kind/claim/research prerequisite/dependent-file binding and
  append-before-result order, not count growth alone;
- cases 12–15 assert per-operation/per-path workspace observations; case 14 first proves supporting
  research plus an approved passing spike over v1, then stale over v2, only-stale command execution,
  one superseding attestation, preserved history, and restored approval;
- cases 17–21 independently cover pending wait/no-pass, conflicting freeze/deferred claim, every
  terminal variant, and late evidence/audit/child results with status unchanged;
- cases 22–28 assert actual before/after child states and side-effect counts. Case 28 asserts exact
  child state, exact `child.terminal.parameters.state`, exact ordered canonical next_actions, and
  `child.recovery_action == canonical next_actions[0]`; no separate state/action table;
- cases 31–35 use D2A ArgumentView digests, gating claims, reviewed evidence digests, audit state,
  independence, and canonical untrusted delimiters; audit child IDs are admitted, not fabricated;
- cases 37–40 assert the real selector's deterministic ordered output, unknown fallback, and absence
  of irrelevant sections through the selector seam;
- case 41 asserts exact target projection and canonical contract digest/identity;
- case 44 dispatches GetRun/RestoreRun through central validation and compares the complete bounded
  public view before/after while proving banned persisted obligation/private fields absent;
- cases 45–47 cover exact persisted identity before decode, old/null/future/partial state, unknown
  request fields/actions, and unknown persisted status using raw-wire/raw-state test inputs;
- case 49 iterates canonical profile registry values; hardcoded profiles are allowed only as explicit
  single-host scenario selectors;
- all ordinary response assertion fixtures satisfy D2A required modes, obligations, residuals,
  freshness, children, and host fields; GetArgument uses the typed branch.

No assertion may compare canonical registry data to itself as a substitute for an observed SUT fact.
No direct `driver.request` is allowed outside the central validated dispatch/raw-dispatch helpers.

## 9. Forbidden changes

No production runtime, D2/D3 accepted files, old tests, manifests/version, skill/docs prose, host
adapters, or quarantined two-fold files. No staging, commit, push, or child spawning.

## 10. Stop/escalate

Stop if a case requires a new public reason/action/section or host tier, if the D2 schema cannot
express a required response, or if test setup would need to duplicate a domain algorithm. A fresh
`spike_request` is the canonical request path for `spike.regate`; do not invent a re-gate action.
Report any other gap; do not decide it.

## 11. Handoff

Report files, exact test count/names grouped by owner, real-SUT binding method, zero skips, red
failure/error counts and representative outputs, concrete reproduced 1.3 defects if available, all
existing Make exits, test/support LOC, residual test-independence risks, forbidden files untouched,
and empty Git index.
