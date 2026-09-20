# Empirica 2.0 D4 — red-first behavioral conformance report

**Status:** D4-S3b FINAL — all 50 cases statically corrected and bound to accepted D2E/D2D.
Cases 1–35 are accepted (D4-S1/S2/S3a/S3a-R) and were not modified except a shared assertion made
strictly stronger. Cases 36–50 are corrected to the accepted D2E presentation-selector registry
and D2D trusted-ingress payloads. All private ingress and structural request builders use
accepted D2D exact payloads (child_event with state/native_id/fingerprint/result_digest,
attribution with subject_kind/id/child relation/provider-model pair/observer/covered artifact IDs,
audit_verdict with reviewed_claims/frozen-deferred aggregate/scope_review, evidence_leaf with
sealed-result shape). Cases 36–42 exercise the real D9 presentation selector through the
``select_sections`` seam with only canonical operation_context/ordered reason codes/terminal
status inputs and compare real selector output to loaded D2E registry arrays via a caller-supplied
``ordered_dedupe`` helper (no copied context/fallback table); ``_SAFE_FALLBACK`` is removed.
Cases 43–44 use a real public setup (C0 ordinary approved, freeze, C1 deferred, foreground audit
child driven to pending) and assert compaction preserves the complete captured RunView exactly
with a recursive banned-key check (no copied ``_BANNED_REVISION`` constant). Cases 45–47 assert
exact sole ``run.old_version``/``run.corrupt`` Blocks and exact Fault/closed — no Fault|Block
unions. Cases 48–50 assert exact registry profile/tier/missing-capability projection, all
profiles observed exactly once, and no private material in any public surface (no
``drv.artifacts()`` as public). The preflight validates the loaded presentation_selector is
structurally integral. The normal suite is red only at the absent `empirica/v2` SUT seam;
post-binding semantic behavior is unexecuted on the current pre-D6/D7 tree. The structural
`--preflight` remains green. Existing subject suites remain green. D4-S3b changes did not
modify production runtime, accepted D2/D3 files, old tests, manifests, or quarantined two-fold
files; the broader worktree carries modified runtime files and untracked `contracts/empirica/v2/`
from earlier serial milestones, so this is an S3b attribution, not a whole-worktree statement.
No staging, commit, push, or child spawning.

This corrective run closes the §§6/8A gaps the D2A amendment and the D4 review (Terra 608348ff, Sol
9a512b7b) exposed in the prior D4 draft. D4-S1 then subtracted the no-op probe, the per-case
checkpoint machinery, and all behavior-aware probe state, and replaced the `--probe` runner/mode
with a structural `--preflight` that executes zero post-binding semantic behavior. S2/S3 behavioral
case repair is explicitly out of scope for S1 and remains outstanding.

## 1. Files

Created/owned by D4 (test/support/report only):

```text
plugins/empirica/tests/v2/__init__.py
plugins/empirica/tests/v2/__main__.py            # committed runner: red mode + --preflight structural mode
plugins/empirica/tests/v2/driver.py              # black-box driver + fake ports (probe removed in S1)
plugins/empirica/tests/v2/sut_adapter.py         # sole place that knows application.v2 composition + D9 selector seam
plugins/empirica/tests/v2/assertions.py          # schema/registry loaders + ConformanceCase + HarnessDefect + require_* helpers
plugins/empirica/tests/v2/test_activation_route_graph.py   # cases 1-6
plugins/empirica/tests/v2/test_evidence_freshness.py       # cases 7-15
plugins/empirica/tests/v2/test_budget_freeze_terminal.py  # cases 16-21
plugins/empirica/tests/v2/test_async_children.py          # cases 22-29
plugins/empirica/tests/v2/test_audit.py                   # cases 30-35
plugins/empirica/tests/v2/test_projection_context.py      # cases 36-42
plugins/empirica/tests/v2/test_compaction.py              # cases 43-44
plugins/empirica/tests/v2/test_protocol_host.py           # cases 45-50
doc/design/reports/empirica-2.0-d4-red-first.md  (this file)
```

Updated (D4-owned only):

```text
Makefile   # D4 adds ONE visible target `empirica-v2-conformance` (passes $(ARGS) through to the
             runner). The pre-existing D3 target `empirica-architecture-check` is unchanged and
             owned by D3. D4-S1 did not modify the Makefile; the runner's `--probe` argument was
             removed and replaced by `--preflight`.
```

## 2. Make target

```make
empirica-v2-conformance  ## run Empirica 2.0 host-neutral behavioral conformance
```

Invokes exactly one committed runner: `python3 plugins/empirica/tests/v2/__main__.py $(ARGS)`. Not
composed into `check-core`, `check-static`, `check`, or `check-ci` in D4. Appears in `make help`.
D4-S1 replaced the `ARGS=--probe` mutation-adequacy meta-check with `ARGS=--preflight`, a
structural-only green mode that executes zero post-binding semantic behavior.

## 3. Test count and names grouped by future owner

50 named cases (one per §5 item). Some use subtests for enumerated edit/delete/absent/unreadable and
terminal variants; each subtest reports independently.

| Owner | File | Cases |
|---|---|---|
| D9 | test_activation_route_graph.py | 1 |
| D7 | test_activation_route_graph.py | 2, 3 |
| D7 | test_activation_route_graph.py | 4, 5 |
| D5 | test_activation_route_graph.py | 6 |
| D5 | test_evidence_freshness.py | 7, 8, 9, 10, 11 |
| D5-F | test_evidence_freshness.py | 12, 13, 14, 15 |
| D7 | test_budget_freeze_terminal.py | 16, 17, 18, 19, 20, 21 |
| D8 | test_async_children.py | 22, 23, 24, 25, 26, 27, 28, 29 |
| D9 | test_audit.py | 30, 31, 32, 33, 34, 35 |
| D9 | test_projection_context.py | 36, 37, 38, 39, 40, 41, 42 |
| D9 | test_compaction.py | 43, 44 |
| D6 | test_protocol_host.py | 45, 46, 47 |
| D10 | test_protocol_host.py | 48, 49, 50 |

## 4. Real-SUT binding method

`driver.new_driver(profile_id=..., limits=..., clock=...)` is the only real-SUT factory used by tests.
It builds the fake ports (workspace bytes/errors + SHA-256 digests, deterministic harness exit +
sealed command binding, clock, CAS storage, host child events) and delegates composition to
`sut_adapter.bind_live`, the sole place that imports `application.v2` and calls its `compose(...)`.
The wrapped `LiveDriver` implements `ConformanceDriver`; `reload()` re-wraps the reloaded service in
a fresh `LiveDriver` sharing the same fake ports (never returns the raw service); `compact()` returns
the compacted public dict (never the service). Fakes own only observations and transport facts; they
never compute approval, reason codes, claim state, audit coverage, budgets, context relevance, or
convergence. The driver and fakes contain no branch on an expected reason/status or case ID.

The D9 context selector is exposed through a separate test seam (`LiveDriver.select_sections` ->
`sut_adapter.bind_selector`), the only place that imports `core.context_selector`; it raises
`V2SeamAbsent` when the D9 selector module is absent. Strict-decoder negatives (45–47) install raw
persisted state through the fake run repository (`LiveDriver.inject_run_state`) before `RestoreRun` —
test input, not a public command (D4 spec §8). Every normal request goes through
`ConformanceCase.dispatch`, which validates the request against `request.schema.json` before the SUT
sees it and validates the response before semantic assertions; strict-decoder negatives use the
explicit `raw_dispatch` (no request validation; the envelope is intentionally malformed/old/unknown,
the response is still validated). No direct `driver.request` is allowed outside the central
dispatch/raw-dispatch helpers. Host profile selection is a driver-factory fact — `start_run` has no
`profile_id` parameter.

In the current tree the v2 composition seam does not exist (`application.wire.API_VERSION ==
"empirica/v1"`, no `application.v2`), so `new_driver` raises `V2SeamAbsent`. `bind_driver` converts
that into a per-case FAILURE naming the owner + expected behavior, so the suite collects and
executes fully and reports each absent behavior rather than aborting on a global import error.

## 5. Red state (real-SUT mode)

```
make empirica-v2-conformance
Ran 50 tests in 0.043s
FAILED (failures=78)
make: exit 2 (expected RED)
```

- tests collected/executed: **50** (78 reported outcomes include subtests)
- failures: **78**
- errors: **0**
- skips / expected failures: **0**

All failures are intentional: each fails at the v2 SUT seam with a precise message of the form
`[<owner>] case-<n>: <behavior> — v2 SUT seam absent: ...`. No case fails because of invalid
fixtures, un-loadable schemas, syntax errors, or a global import abort. No expectations were
weakened; no baseline exception list was added.

## 6. Structural preflight (GREEN, no behavioral execution)

D4-S1 removed the `--probe` mutation-adequacy meta-check (behavior-aware test meta-machinery) and
replaced it with a structural `--preflight` green mode. The preflight executes ZERO post-binding
semantic behavior: it never instantiates a SUT or probe and never maintains run/child/evidence/
freeze state. It structurally validates the harness only.

```
python3 plugins/empirica/tests/v2/__main__.py --preflight  => exit 0
preflight: structural only; zero post-binding semantic behavior was executed
```

The preflight checks:
1. import and collect exactly 50 unique `test_*` methods from the 8 modules;
2. reject skips, expected failures, empty/pass/TODO placeholder bodies, and any remaining
   checkpoint or probe symbol;
3. validate representative instances from every static request/envelope/action builder against the
   accepted request schema (strict negative raw requests excluded explicitly);
4. validate accepted D2A/D2B response fixtures through the response schema and prove one malformed
   response is rejected;
5. AST-check that `.request(...)` appears only inside central `dispatch`/`raw_dispatch` and raw
   dispatch/state injection are limited to exact named strict test methods; a test may invoke a
   prerequisite helper rather than call `dispatch` directly;
6. compare `ConformanceDriver` vs `LiveDriver` and explicit wrapper-to-fake-port method pairs via
   `inspect.signature` after removing `self` (full kind/name/default agreement; semantic positional
   mapping where wrapper/fake parameter names differ);
7. reject copied full protocol/profile/tier/terminal/reason/action/section inventories and obvious
   permissive patterns (`or True`, self-membership, set-only comparison in ordered selector cases,
   conditional-only semantic assertion);
8. AST-walk all test files for calls to ConformanceCase artifact/claim helper methods and verify
   keyword arguments match the actual method signature, so an unsupported helper keyword fails
   structurally before SUT binding.
9. AST-walk `test_audit.py` and require every `build_audit_verdict_payload` call to pass
   `scope_review` explicitly (pass|fail), so an omitted scope_review fails structurally before
   SUT binding.
10. Verify the D2E `presentation_selector` is loaded from the PublicContract in `assertions`
    (not copied into a test file) and is structurally integral: every `context_sections` key
    equals a registered `operation_context`, every value resolves to a canonical section and is
    unique, and `terminal_sections`/`unknown_reason_sections` resolve to canonical sections.
    Check 7 flags any literal copy of a selector array as a copied fallback table.

`HarnessDefect` remains solely as a future real-SUT prerequisite diagnostic, with no probe or
mutation-adequacy claims. Post-binding semantics on the current pre-D6/D7 tree are unexecuted.

### §8A corrections applied

- case 3 requires successful investigate admission (Allow, not Block) and a resulting witness
  artifact; a Block cannot satisfy it.
- cases 7/10 assert exact typed artifact dependent-file/claim binding and append-before-result
  order, not count growth alone.
- cases 12/15 assert per-operation/per-path workspace observations (the run's `freshness.changes`
  observes the bound path; the Workspace port is actually called).
- case 14 first proves supporting research plus an approved passing spike over v1, then stale over
  v2, then re-gate via a fresh `spike_request` with one superseding attestation, preserved history,
  and restored approval.
- cases 17–21 independently cover pending no-pass, freeze first-write-wins, frozen deferred claims,
  every terminal variant, and late evidence/audit/child results.
- cases 22–28 assert actual before/after child states and side-effect counts; case 28 asserts exact
  child state, exact `child.terminal.parameters.state`, exact ordered canonical `next_actions`, and
  `child.recovery_action == canonical next_actions[0]` (`child.retry`).
- cases 31–35 use the D2A typed ArgumentView (digests, canonical untrusted delimiters); audit child
  IDs are SUT-admitted, not fabricated.
- cases 37–40 assert the real D9 selector's deterministic ordered output, unknown fallback, and
  absence of irrelevant sections through the `select_sections` selector seam.
- case 41 asserts exact target projection and canonical contract digest/identity.
- case 44 dispatches GetRun/RestoreRun through central validation and compares the complete bounded
  public view before/after while proving banned persisted obligation/private fields absent; no
  direct `driver.request`.
- cases 45–47 cover exact persisted identity before decode, old/null/future/partial state, unknown
  request fields/actions, and unknown persisted status using raw-wire and raw-state (`inject_run_state`)
  test inputs.
- case 49 iterates the canonical profile registry values; case 50 asserts private capability never
  appears in public responses, compaction, artifacts, or diagnostics.

## 7. Existing Make exits

```text
make check-core      GREEN  (exit 0)
make check-static    GREEN  (exit 0)
make check-claude    GREEN  (exit 0)
make check-codex     GREEN  (exit 0)
make check-pi        GREEN  (exit 0)   (local Node/Pi available)
make check           GREEN  (exit 0)
```

## 8. LOC

```text
plugins/empirica/tests/v2/*.py   5106 lines (driver + sut_adapter + assertions + runner + 8 test files)
```

S3b increased LOC from S3a-R (4740) to 5106, driven by the D2E presentation-selector binding: the
loaded ``PRESENTATION_SELECTOR``/``SELECTOR_CONTEXT_SECTIONS``/``SELECTOR_TERMINAL_SECTIONS``/
``SELECTOR_UNKNOWN_REASON_SECTIONS``/``TERMINAL_RUN_STATUSES`` registry constants, the
``ordered_dedupe`` caller-supplied-list helper, the strictly stronger recursive
``assert_no_private_capability`` (exact key + value-substring, expanded banned set), the new
``assert_block_sole_reason`` strict-result helper and the separated persisted-operational-field
helper (``assert_no_persisted_operational_fields`` owning only revisions/pointers/history/phase/
counters plus ``first_terminal_fingerprint`` and persisted hash forms — exact ``hash``/``sha256``
and keys ending ``_hash``; no ``*_digest`` fields banned), with private/native/provider keys left
solely to ``assert_no_private_capability`` and the full-contract dump solely to
``assert_no_full_contract_dump``, the strengthened ``assert_contract_result`` (digest +
sibling-payload rejection), the ``profile_missing_capabilities`` ordered accessor, the rewritten
cases 36–50, the preflight presentation_selector structural-integrity check (check 10) and
selector-array copy detection (check 7), and the report update.

S3b helper correction (recursive full-contract dump + exact persisted keys): ``assert_no_full_contract_dump``
was made recursive — it rejects the exact keys ``full_contract``, ``public_contract``, and ``full``
wherever they appear in any nested dict/list of an ordinary/compaction/reload surface (not only
top-level). ``assert_no_persisted_operational_fields`` now also rejects the exact keys ``revision``,
``pointer``, and ``history`` in addition to the ``*_revision``/``*_pointer``/``*_history`` suffix
forms. Cases 42–44 explicitly call all three separated helpers (``assert_no_full_contract_dump``,
``assert_no_private_capability``, ``assert_no_persisted_operational_fields``) on their
compacted/reloaded surfaces; the redundant top-level ``assertNotIn("full_contract", ...)`` calls
were removed where superseded by the recursive ``assert_no_full_contract_dump``.

## 8A. D4-S2/S3a case corrections

**S3b FINAL — all 50 cases statically corrected; zero post-binding semantics.**

Corrected cases (1–21):

| Case | File | S2 correction |
|------|------|---------------|
| 1 | test_activation_route_graph | Exact effective modes (multi_provider + required cli_exec sibling), complete bounded RunView arrays (obligations/residuals/freshness/children/host), no full/private dump |
| 2 | test_activation_route_graph | Exact single route.required reason payload, exact affected (empty, correlates to projected obligation), ordered next_actions/sections, exact relevant sections (ordered, not sorted) |
| 3 | test_activation_route_graph | Record route; investigation Allow non-converged; late route permanent stable route.late across repeat with stable affected/parameters, unchanged derived obligation snapshot, no private witness IDs |
| 4 | test_activation_route_graph | Admit one-gating-claim graph (canonical helper), Block specifically on that claim (affected obligation_id), GetArgument derives open/gating |
| 5 | test_activation_route_graph | Independent fresh variants for missing + malformed selected graph, each exact graph.invalid |
| 6 | test_activation_route_graph | Admit graph before refuting research, typed refuting research artifact (append-only), GetArgument discarded/refuted with central evidence_digest = registry_digest of active_evidence_ids, terminal stopped_residual + claim.refuted |
| 7 | test_evidence_freshness | Admit graph + research, stage harness before spike_request, exact sealed request prerequisites + result binding, GetArgument approved from complete active set |
| 8 | test_evidence_freshness | Each caller-forged approval field (ok/verdict/timestamp/artifact_digest/attribution): Block/Fault, artifact history unchanged (compared before/after), GetArgument/Evaluate no approved claim/convergence |
| 9 | test_evidence_freshness | Admit graph/research, replace graph same ID changed text/digest preserving claim kind, claim.research_unbound identifies C0, old research absent from active evidence, kind preserved |
| 10 | test_evidence_freshness | Stage completion before spike_request, distinct request then result in exact order, matching harness_request_id, prerequisite IDs, command/digest, file binding |
| 11 | test_evidence_freshness | Three independent fresh runs: forged audit (no gate change), failing exit (gate=fail, not approved), passing exit (gate=pass, approved, not converged) |
| 12 | test_evidence_freshness | Approved spike first; each state-bearing read (GetRun/GetArgument/EvaluateRun) grows workspace observe_history with exact active bound path; RunView freshness path/state for all three reads |
| 13 | test_evidence_freshness | Five fresh variants (edit/missing/unreadable/non_regular/outside_workspace), each stale + Block claim.spike_stale with exact path/state, reason parameters.changes equal RunView freshness.changes exactly and in order, repeat adds no evidence; no duplicate delete/absent |
| 14 | test_evidence_freshness | Two claims over separate files, mutate only C0, one harness invocation + one superseding C0 result (supersedes prior), C1 unsuperseded (supersedes=null, not absent), both approved/fresh (no stale freshness changes), convergence may await audit |
| 15 | test_evidence_freshness | Approved spike, Evaluate adds exact workspace observation-history entry for active bound path; no aggregate counter-only assertion |
| 16 | test_budget_freeze_terminal | Zero spawn budget Block + exhausted pass budget terminal stopped_budget, exact budget.exhausted.parameters.resource, ordered actions/sections on both Block and residual, no reservation on spawn Block |
| 17 | test_budget_freeze_terminal | Admit child, drive reserved→launching→pending through host events, pass-use unchanged before/after polling |
| 18 | test_budget_freeze_terminal | Admit C0, freeze, add C1, repeated/conflicting freeze: frozen_scope_digest byte-for-byte first-write-wins, C0 gating=true, C1 gating=false (deferred) |
| 19 | test_budget_freeze_terminal | Admit C0, freeze, add C1, stop/evaluate + GetArgument: C0 only frozen (gating=true), C1 deferred (gating=false), non-null distinct digests, ordered claim_ids=[C1], deferred_scope_digest equals ArgumentView |
| 20 | test_budget_freeze_terminal | Three terminal outcomes: stopped_residual (root-refuted), stopped_frozen (discharged + later claim), stopped_budget (exhausted pass); exact status + converged=false; no invented `failed` status |
| 21 | test_budget_freeze_terminal | Each terminal status: stopped_budget starts max_passes=1; deliver trusted late evidence/audit/child through private composition ingress (exact Inert responses, never public dispatch/redacted envelopes); complete RunView + operational fingerprint unchanged; author-forged action and new reservation not a substitute |

Outstanding cases (36–50) after S2/S3a: corrected in D4-S3b (see table below).

### D4-S3a-R case corrections (D2D trusted-ingress binding)

**S3a-R PARTIAL — cases 1–35 corrected and bound to accepted D2D; cases 36–50 outstanding.**

Corrected cases (22–35):

| Case | File | S3a-R correction |
|------|------|------------------|
| 22 | test_async_children | D2D child_event payload (state/native_id/fingerprint/result_digest); reserved→launching through trusted ingress, before/after states; launching→orphaned (non-registry edge) fails Fault/Block with exact snapshot unchanged; iterate pending→completed to prove not all rejected |
| 23 | test_async_children | D2D child_event payload; one SUT child ID; launching with native binding through trusted ingress; identical idempotent; conflicting binding and foreign child ID fail closed; native_id/private never in response/compacted view/ArgumentView |
| 24 | test_async_children | D2D child_event payload; reserved→launching→pending→completed; snapshot RunView/operational/ArgumentView; identical duplicate exact Inert with complete equality; conflicting terminal exact Fault with unchanged snapshots |
| 25 | test_async_children | D2D payloads for every late event (child/audit/evidence); structurally valid but unbound evidence_leaf; first-terminal child state, audit/budget/convergence, RunView, operational fingerprint unchanged |
| 26 | test_async_children | D2D child_event in require_child_state; exact spawns_used/passes_used; compact preserves pending; reloaded driver; counters unchanged |
| 27 | test_async_children | D2D child_event; reserve spend baseline+1; launch_rejected refunds exactly baseline; replay exact Inert with ArgumentView equality; post-start failure remains baseline+1; never reserved→pending |
| 28 | test_async_children | D2D child_event; exact child state, sole child.terminal, ordered next_actions/sections, recovery_action==next_actions[0]; identical replay exact Inert with RunView/operational/ArgumentView equality |
| 29 | test_async_children | full_async exact Allow with one reserved child; unsupported profiles sole exact registry reason via assert_block_only, ordered actions/sections, no fallback |
| 30 | test_audit | Author public attempts use structurally valid D2D payloads; Block/Fault; ArgumentView/RunView/operational unchanged; fabricated child ID does not affect state |
| 31 | test_audit | C0 ordinary approved, freeze, C1 deferred gating=false; audit state exactly 'required' before child admission; exact residual tuple/digests; no full/private dump |
| 32 | test_audit | C1 explicitly non-gating and non-approved; C0 audit verdict atomic pending→completed; identical replay exact Inert; conflicting verdict derived from admitted payload (copy dict, verdict only) exact Fault unchanged; C1 non-approved; Evaluate exact Allow converged=false stopped_frozen |
| 33 | test_audit | Both covered-actor and auditor identities via require_trusted_audit_attribution; sole exact independence reason via assert_block_only (ordered exact reason codes); same_model and unverified variants; exact active run status (failed independence leaves run active pending a valid independent audit) |
| 34 | test_audit | Both identities via trusted ingress; author decorrelation attempt (valid D2D payload, forged capability) rejected/ignored; snapshots unchanged; sole exact block reason remains original; never upgraded |
| 35 | test_audit | reviewed_claims exactly [C0] with exact C0 evidence digest; C1 never in reviewed_claims; C1 bound by reviewed_deferred_scope_digest + residual claim_ids + scope_review pass; trusted decorrelated identities; atomic child completion; GetArgument passed/decorrelated; Evaluate stopped_frozen/non-converged |

S3a-R helper and seam corrections (D2D trusted-ingress binding):

- Module-level D2D payload builders: ``child_event_fingerprint`` (stable canonical digest of
  event facts), ``build_child_event_payload`` (state/native_id/fingerprint/result_digest with
  exact branch/null relations), ``build_attribution_payload`` (subject_kind/id, child relation,
  provider/model pair, observer, covered artifact IDs), ``build_evidence_leaf_payload``
  (harness_request_id/command_digest/prerequisite_research_ids/file_bindings/exit_code/result_digest).
- ``require_child_state`` constructs a valid D2D ``childEventPayload`` (fingerprint + result_digest
  for completed) instead of the raw ``{"state":..., "native_id":...}`` dict.
- ``build_audit_verdict_payload`` uses canonical field ``reviewed_claims`` (not ``reviewed``);
  deleted ``deferred_claim_id`` parameter and the branch appending deferred claims; always
  includes the current argument/goal/frozen/deferred aggregate tuple and ``scope_review``
  (null when frozen_scope_digest is null, pass|fail when non-null); ``findings`` is a list.
- ``require_audit_scope`` returns the exact active supporting C0 artifact ID
  (``c0_artifact_id``) so attribution can bind its producer; asserts C1 added after freeze is
  explicitly ``gating=false``.
- ``require_trusted_audit_attribution`` submits trusted covered-actor attribution bound to exact
  active approved C0 artifact IDs and auditor attribution bound to the pending SUT-admitted audit
  child. Same-model uses equal normalized provider/model pairs; decorrelated uses distinct pairs;
  unverified makes one pair null. Every trusted response is validated/asserted.
- Removed unused duplicate ``require_child_summary``; retained one child index/assertion path
  (``index_children`` + ``assert_child_summary``).
- Preflight: 29 representative request envelopes all validate with accepted D2D payloads;
  ``require_child_summary`` removed from ``_helper_targets``; ``require_trusted_audit_attribution``
  added; ``build_child_event_payload``/``build_attribution_payload``/``build_evidence_leaf_payload``
  imported for representative envelope construction.
- S3a final correction: ``assert_block_only`` now compares the actual ordered reason-code list
  exactly to the expected list (duplicates and order both fail), replacing the prior set-only
  comparison; every ``build_audit_verdict_payload`` call in cases 32–35 passes ``scope_review``
  explicitly; case 32 conflicting verdict derives from the admitted payload by copying the dict
  and changing only the verdict (not a rebuild); case 32 Evaluate asserts exact ``Allow``
  ``converged=false`` and ``stopped_frozen`` (deferred C1 cannot converge, no ``.get`` fallback);
  case 33 asserts exact ``active`` run status (failed independence leaves the run active pending a valid independent audit); preflight check 9 AST-enforces the
  ``scope_review`` keyword on every ``build_audit_verdict_payload`` call in ``test_audit.py``.

Outstanding cases: none. All 50 cases are statically corrected; cases 36–50 were corrected in
D4-S3b (see table below). Post-binding semantics remain unexecuted on the current pre-D6/D7 tree.

### D4-S3b case corrections (D2E presentation-selector + D2D binding)

**S3b FINAL — all 50 cases statically corrected; zero post-binding semantics.**

Corrected cases (36–50):

| Case | File | S3b correction |
|------|------|----------------|
| 36 | test_projection_context | Two real Blocks: empty run → exact sole graph.missing (not scoped, no affected); admitted ordinary graph → exact sole claim.research_missing; each exact ordered reason, empty params, ordered next_actions/sections, nonempty message, schema-valid RunView; research_missing affected exactly {obligation_id} resolving to a RunView obligation (no permissive key subset) |
| 37 | test_projection_context | Every canonical operation context with no reasons/status → exact ordered context_sections[context]; called twice for determinism; known terminal status appends exact terminal_sections first-occurrence deduped |
| 38 | test_projection_context | Fixed block+one reason+null status, vary workspace/transport facts only → exact unchanged ordered output equal block base + reason sections deduped; no domain-state inference |
| 39 | test_projection_context | Unknown reason and nonterminal/unknown status → exact ordered unknown_reason_sections (not subset, no _SAFE_FALLBACK); unknown action and unknown GetContract section → exact Fault/closed |
| 40 | test_projection_context | Multiple ordered known reasons → exact ordered first-occurrence union of block base then each reason section; reversed input → corresponding canonical order; no irrelevant/duplicate |
| 41 | test_projection_context | GetContract index/full and representative sections incl presentation/selector → exact projection, canonical identity/digest, no sibling target payload; unknown section Fault/closed |
| 42 | test_projection_context | StartRun exact Allow + no dump; Evaluate empty run exact sole graph.missing Block + no dump (no conditional); compacted no full contract/private/persisted recursively via all three separated helpers (``assert_no_full_contract_dump``, ``assert_no_private_capability``, ``assert_no_persisted_operational_fields``); redundant top-level ``assertNotIn`` removed |
| 43 | test_compaction | Real public setup (C0 ordinary approved, freeze, C1 deferred, foreground audit child → pending); capture GetRun; compaction preserves exact goal/status/modes/contract identity/digest/relevant sections/obligations/residuals/freshness/children/host/next_actions; pending child + deferred C1 residual; no full contract/native/capability/counters/revision/history/phase via all three separated helpers (``assert_no_full_contract_dump``, ``assert_no_private_capability``, ``assert_no_persisted_operational_fields``); redundant top-level ``assertNotIn`` removed |
| 44 | test_compaction | Compact + reload; reloaded driver GetRun/RestoreRun equal pre-compaction RunView exactly (run ID/pending child/deferred); full-contract/private/persisted-operational fields asserted via all three separated helpers recursively (``assert_no_full_contract_dump``, ``assert_no_private_capability``, ``assert_no_persisted_operational_fields``), not only top-level |
| 45 | test_protocol_host | Persisted v1 identity with hostile/partial semantic fields → exact sole run.old_version Block with canonical actions/sections before decode; v1 wire envelope → exact Fault/closed |
| 46 | test_protocol_host | Wire variants null/empty/v1/future/partial → exact Fault/closed; persisted identities null/missing/v1/future → exact sole run.old_version Blocks with start-fresh; exact v2 + malformed/partial encoding → sole run.corrupt Block |
| 47 | test_protocol_host | Unknown top-level/command/action field and action kind → exact Fault/closed; exact-v2 persisted unknown status → sole run.corrupt Block with start-fresh; no Fault|Block unions |
| 48 | test_protocol_host | Default host RunView profile ID/tier equal exact registry; missing capabilities equal exact registry list/order; no schema parity inference |
| 49 | test_protocol_host | Iterate all canonical profile IDs; each StartRun host view equals exact registry profile/tier/missing-capability projection; all observed exactly once |
| 50 | test_protocol_host | Reserve foreground child; forged-capability D2D child_event → Fault/Block rejection with RunView/ArgumentView unchanged; valid launching via private ingress (private trusted response validated structurally only, not a public surface); immediately GetRun asserts admitted child exact launching, reuses that GetRun response as the public privacy surface; no capability/capability_ref/native_id/fingerprint/provider-model/secret in any public surface; no drv.artifacts() as public |

S3b shared-helper and preflight corrections (D2E/D2D binding):

- Loaded the D2E ``presentation_selector`` from the PublicContract
  (``PRESENTATION_SELECTOR``, ``SELECTOR_CONTEXT_SECTIONS``, ``SELECTOR_TERMINAL_SECTIONS``,
  ``SELECTOR_UNKNOWN_REASON_SECTIONS``, ``TERMINAL_RUN_STATUSES``); tests compare real selector
  output directly to these loaded arrays — no context/fallback table is copied into a test file.
- Added ``ordered_dedupe(*lists)`` — a caller-supplied-list first-occurrence dedupe helper with no
  context/reason mapping; callers pass loaded registry lists (context base, reason sections,
  terminal sections) and the helper builds the expected selector output.
- Strengthened ``assert_no_private_capability`` from a flat substring scan to a recursive
  exact-key + value-substring walk with an expanded banned set (fingerprint, provider_id,
  model_id, secret); exact key matching so canonical ``missing_capabilities`` never matches the
  banned key ``capability``.
- Added ``assert_block_sole_reason`` (exactly one reason, empty/exact params, ordered
  next_actions/sections, nonempty message) — strictly stronger than ``assert_block``.
- Separated the public-surface field bans into three distinct purpose-specific helpers (no
  shared generic ``BANNED_PERSISTED_KEYS`` constant): ``assert_no_persisted_operational_fields``
  owns ONLY revisions (exact ``revision`` and ``*_revision``), pointers (exact ``pointer``
  and ``*_pointer``), history (exact ``history`` and ``*_history``), exact ``phase``,
  operational counters (``*_used``), the persisted ``first_terminal_fingerprint``, and
  persisted hash forms (exact ``hash``/``sha256`` and keys ending ``_hash``) — it does NOT ban any
  ``*_digest`` field (content digests are public identity); ``assert_no_private_capability`` owns
  private/native/provider capability keys; ``assert_no_full_contract_dump`` recursively rejects the
  exact keys ``full_contract``, ``public_contract``, and ``full`` wherever they appear in any
  nested dict/list of an ordinary/compaction/reload surface (not only top-level). Cases 42–44
  explicitly call all three separated helpers directly on their compacted/reloaded surfaces;
  the redundant top-level ``assertNotIn("full_contract", ...)`` calls were removed where
  superseded by the recursive ``assert_no_full_contract_dump``. Removed the copied
  ``_BANNED_REVISION`` constant from ``test_compaction.py``.
- Strengthened ``assert_contract_result`` to assert the canonical digest and reject sibling
  target payloads.
- Added ``profile_missing_capabilities`` ordered accessor for exact list/order comparison.
- Removed obsolete ``_SAFE_FALLBACK`` from ``test_projection_context.py`` and the
  ``drv.artifacts()`` fake-artifact privacy check from case 50.
- Preflight: check 10 verifies the loaded ``presentation_selector`` is structurally integral
  (every context_sections key equals a registered operation_context, every value resolves to a
  canonical section and is unique, terminal/unknown_reason sections resolve); check 7 flags literal
  copies of selector arrays as copied fallback tables; ``assert_block_sole_reason`` and
  ``assert_no_persisted_operational_fields`` added to the helper-signature binding check.

Prerequisite helper corrections (S2):

- `require_research_recorded` returns the typed research artifact ID and validates claim/result on
  the typed artifact.
- `require_approved_spike` stages harness completion before `spike_request`, returns request/result
  artifacts, calls GetArgument, requires claim state `approved` with matching spike evidence and
  freshness; must not claim approval after observing only a request artifact.
- `require_investigate_admitted` implemented: dispatches investigate, asserts Allow, returns None;
  admission and behavior are checked via derived RunView obligation state, not a typed witness
  identity.
- `require_graph_admitted` added: submits canonical graph with explicit root, ordered claims (id,
  exact text, gating, kind) and edges, proves admission via the typed ArgumentView claim/edge
  projection, and asserts the projected root claim carries the D2C kind from the graph payload.
- `canonical_graph` includes the D2C `kind` field (configurable, default `needs-experiment` for
  two-fold evidence/freshness cases; research-only freeze/terminal cases pass `kind="ordinary"`).
  `assert_claim_kind` and `get_argument_claim(kind=...)` assert the projected claim kind.
- Typed artifact lookup helpers (`find_argument_artifacts`, `find_one_argument_artifact`,
  `get_argument_artifacts`) and D2C artifact assertions (`assert_research_artifact`,
  `assert_spike_request_artifact`, `assert_spike_result_artifact`, `assert_artifact_sequence_order`,
  `assert_evidence_digest`) added; all failures remain `HarnessDefect` diagnostics. The central
  `active_set_digest` helper and `assert_evidence_digest` assert D2C invariant §10 (evidence_digest
  equals the canonical registry digest of the exact ordered active_evidence_ids), replacing all
  per-case self-comparison.

Policy-free telemetry/trusted-host test seams (S2):

- `FakeWorkspace.observe_history()` returns tuples of (exact normalized path batch, result batch)
  per observe call.
- `FakeSpikeHarness.invocations()` returns harness invocation history as returned attestations.
- `LiveDriver.trusted_child_event` / `trusted_evidence_leaf` / `trusted_audit_verdict` call the
  dedicated private service methods of the same names directly and validate the returned response
  (never public `dispatch` or redacted envelopes). Cases 17/21 assert the returned response reflects
  the delivered event before inspecting subsequent RunView.
- `FakeArtifactRepository` preserves global append order (ordered list, not frozenset).
- `FakeWorkspace.observe()` classifies `non_regular` and `outside_workspace` freshness states via
  error codes (D1 §6).
- `ConformanceDriver`, `LiveDriver`, and `_FAKE_PAIRS` updated consistently with the new seams.

The preflight now includes a compact helper-signature binding check (check 8) that AST-walks
all test files for calls to ConformanceCase artifact/claim helper methods and verifies keyword
arguments match the actual method signature, so an unsupported helper keyword (e.g. a stale
`outcome` filter before the parameter was added) fails structurally before SUT binding.

## 9. Residual test-independence risks

- Cases that assert equality of `operational_state()` snapshots (e.g. case 25 "first terminal wins")
  depend on the SUT exposing a comparable operational-state dict; the assertion shape may need
  tightening once D7/D7-W define the snapshot projection surface.
- `GetArgument` dossier assertion (31) reads structured `freeze.deferred` residuals; the exact
  parameter field name for the deferred-claim digest is owned by D9 and may need alignment.
- Independence reporting cases (33/34) assert an exact non-decorrelated reason
  (`audit.same_model`/`audit.independence_unverified`) via `assert_block_only` with ordered exact
  reason codes (duplicates/order fail), not a set-of-reasons shortcut; the audit verdict and
  attribution payloads are bound to accepted D2D shapes.
- Case 35 asserts exact `reviewed_claims` order (gating before deferred) and exact
  `reviewed_*_digest` equality; these field names are owned by D9.
- Cases 37–40 compare real selector output to loaded D2E registry arrays via `ordered_dedupe`;
  the exact compaction-surface shape (case 43) and the compaction delimiters are owned by D9 and
  may need alignment once the D9 compaction projection is implemented.
- Case 46 distinguishes `run.old_version` (non-v2 protocol) from `run.corrupt` (v2 protocol +
  malformed/partial encoding); the exact structural trigger for `run.corrupt` is owned by D6.
- Case 50 asserts no private material in public surfaces via the strengthened recursive
  `assert_no_private_capability`; the exact set of private field names is owned by D10.

## 10. Forbidden files untouched

D4-S3b changes did not modify production runtime, D2/D3 accepted files, old tests, manifests/version,
skill/docs prose, host adapters, or quarantined two-fold files. Cases 1–35 test files
(`test_activation_route_graph.py`, `test_evidence_freshness.py`, `test_budget_freeze_terminal.py`,
`test_async_children.py`, `test_audit.py`) and `driver.py`/`sut_adapter.py` were not modified;
only shared assertion helpers in `assertions.py` were strengthened. The broader worktree carries
modified runtime/contract files from earlier serial milestones (visible in `git status --short`);
this is an S3b attribution, not a whole-worktree statement. The pre-existing D3 target
`empirica-architecture-check` and the architecture validator are unchanged. No staging, commit,
push, or child spawning.

## 11. Git index

Empty — nothing staged. The D4 files are untracked; the Makefile is modified but unstaged.
