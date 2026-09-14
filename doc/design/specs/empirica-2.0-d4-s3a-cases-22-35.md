# Empirica 2.0 D4-S3a — exact async-child and audit cases 22–35

**Status:** Parent-frozen serial writer milestone after accepted D4-S1/S2 and D2C.

## Scope

Correct only cases 22–35 and shared driver/assertion/preflight/report code they require:

- `test_async_children.py`
- `test_audit.py`
- `assertions.py`, `driver.py`, `sut_adapter.py`, `__main__.py`
- D4 report partial status/count/LOC

Do not modify cases 1–21 except a shared helper signature mechanically, or cases 36–50. No
contracts/runtime/D1–D3/Make/manifests/old tests.

No public redacted trusted envelopes, fake-only delivery, second SUT/model, mutation oracle, or probe.
All real child/audit/attribution events use dedicated private composition ingress methods and their
returned standard responses. Author-forgery negatives continue through public dispatch.

## Private ingress and shared helpers

- Remove obsolete `child_native_event` fake-sink seam from ConformanceDriver/LiveDriver/preflight; no
  test uses it.
- `require_child_state` invokes `trusted_child_event(run_id, child_id, event)` and asserts the returned
  response contains the exact child state.
- Add `trusted_attribution(run_id, payload) -> response` to ConformanceDriver/LiveDriver, calling the
  dedicated private service method directly; tests never possess capability material.
- Add helpers to flatten/index child summaries, require exact operational integer fields
  (`spawns_used`, `passes_used`) without fallback defaults, and snapshot complete public RunView plus
  operational side-effect facts.
- Add audit setup helpers using public D2C: canonical graph/ordinary supporting research, optional
  needs-experiment claim, freeze/deferred claim, typed GetArgument dossier, admitted audit child driven
  reserved→launching→pending via private ingress, and exact verdict payload built from current public
  argument/claim digests. Helpers assert prerequisites and expose SUT-admitted IDs only.
- Trusted audit verdict fields: verdict/findings/current argument digest/reviewed approved gating
  claim/evidence tuples; frozen runs additionally exact goal/frozen/deferred tuple and scope_review.
  Independence is never supplied by verdict; it is derived from trusted attribution ingress.

Preflight must bind helper call signatures and compare the new protocol/live ingress signatures.

## Cases 22–29

22. Drive an actual canonical reserved→launching transition through trusted ingress and assert both
    before/after states. Then attempt launching→orphaned (not a registry edge) through trusted ingress;
    require Fault/Block and exact child/operational snapshot unchanged. Iterate at least one accepted
    terminal path to prove registry transitions are not all rejected.
23. Reserve once: exactly one SUT child ID. Drive launching with one host/native binding through trusted
    ingress. Repeating identical binding is idempotent; conflicting binding and foreign child ID fail
    closed. Native ID/private capability never appears in response, compacted view, diagnostics, or
    artifacts.
24. Drive reserved→launching→pending→completed canonically. Snapshot complete RunView, operational
    counters, artifacts/audit state after first completion. Identical duplicate is idempotent with all
    snapshots equal; out-of-order/non-identical terminal is Fault/Inert and leaves snapshots equal.
    Completion side effect occurs exactly once.
25. Drive a canonical first terminal event. For each later child/audit/evidence event, use private
    ingress and require first terminal child state, audit/budget/convergence, complete RunView, and
    operational fingerprint unchanged. Do not merely compare one state field.
26. Establish pending child and exact `spawns_used`/`passes_used`. Compact and assert public pending
    child preserved without private dump. Reload, then use **reloaded driver** for GetRun/Evaluate;
    assert pending child survives and both counters unchanged—no duplicate spawn/pass.
27. Fresh launch-rejection path: reserve (spawns +1), trusted reserved→launch_rejected, exact refund to
    baseline; replay identical rejection and prove no second refund/side effect. Fresh post-start path:
    reserve→launching→pending→failed; spawn remains spent exactly +1. Never use reserved→pending.
28. Fresh per adverse state `cancelled|timed_out|orphaned`: drive canonical path to pending, deliver
    terminal through trusted ingress, assert exact child state, exact sole `child.terminal` reason
    parameters, ordered canonical next_actions/sections, and
    `recovery_action == next_actions[0]`. Repeated identical terminal is idempotent.
29. Iterate canonical profile registry, no copied profile/tier table. For each exact profile, async
    reserve behavior matches its tier: `full_async` admits one reserved child;
    `foreground_only` exact `host.async_unsupported`; `observational` exact
    `host.audit_output_unobservable`. Assert exact host profile/tier in RunView and no silent fallback.

## Cases 30–35

30. On a prepared run, author public attempts for audit_verdict and attribution are Block/Fault and
    leave ArgumentView audit, artifacts, complete RunView, and operational fingerprint unchanged.
    No fabricated child ID may affect state.
31. Build C0 ordinary approved, freeze, then add C1 deferred. GetArgument must exactly project:
    canonical delimiters; goal/argument/frozen/deferred digests; C0 `gating=true`, C1 false; ordered
    `freeze.deferred.parameters={claim_ids:[C1],deferred_scope_digest:<exact>}`; D2C artifacts/active
    digest; audit `required|pending` as appropriate; no full/private dump.
32. Build frozen scope with C0 ordinary approved and C1 `needs-experiment` missing machine spike.
    Admit pending audit child. Deliver one exact current audit verdict through private ingress, then
    duplicate it identically. Compare audit projection, artifact count, child state, budgets, and
    operational fingerprint after first/duplicate—admitted once. Conflicting verdict fails closed.
    C1 remains non-approved, proving audit cannot manufacture machine evidence; run never converges.
33. Independent fresh variants for trusted host attribution yielding `same_model` and missing/
    incomparable yielding `unverified`. Use otherwise approvable frozen scope and exact audit verdict.
    GetArgument audit.independence equals exact variant; Evaluate exact matching public reason
    (`audit.same_model` or `audit.independence_unverified`), non-converged. No set-of-reasons shortcut.
34. Establish trusted `same_model` or `unverified`; then author publicly claims decorrelation. Author
    attempt is rejected/ignored with snapshots unchanged. Exact subsequent audit projection and Block
    reason remain the original non-decorrelated value; never upgraded.
35. Build C0 ordinary approved, freeze, add C1 deferred, trusted decorrelated identities, admitted
    pending audit child. Capture typed dossier. Deliver exact passing verdict binding current argument,
    goal, frozen/deferred digests, approved gating C0 evidence digest, ordered deferred C1 digest, and
    scope_review pass; then terminal completion. GetArgument audit is `passed`, independence
    `decorrelated`, exact reviewed coverage/digests, no stale/extra claim. Evaluate remains exact
    `stopped_frozen`, `converged=false`; deferred scope cannot become convergence.

## Assertion/SSOT discipline

- Use D2C public ArgumentView; no raw `drv.artifacts()` in cases 22–35.
- Use canonical registry orders/sets and exact profile values loaded from JSON.
- No set comparison where exact reason/reviewed/deferred order is required.
- No fallback counters, fabricated child IDs (except explicit foreign-ID negative), self-comparison,
  truthiness-only digest, conditional-only assertion, or direct ordinary `.request` call.
- Every multi-part case asserts all named postconditions; no early marker/probe.

## Report/verification

Report S3a partial: cases 1–35 corrected, 36–50 outstanding; post-binding semantics still unexecuted.
Recompute current methods/subtest failures and LOC; qualify milestone attribution.

Run preflight green; normal 50 methods red only at absent seam; py_compile; check-core/static;
forbidden-pattern grep; diff/check and empty index.

## Guardrails

Work only in `/private/tmp/empirica-twofold-fix`; no outside search/reviewer IDs/Git refs. Stop on a
contract gap. No stage/commit/push/subagents.
