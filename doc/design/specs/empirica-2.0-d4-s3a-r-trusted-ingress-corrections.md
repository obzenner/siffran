# Empirica 2.0 D4-S3a-R — bind cases 22–35 to accepted D2D

**Status:** Parent-frozen correction after accepted D2D/D2D-R and S3a review.

## Scope

Correct cases 22–35 and required D4 helpers/driver/preflight/report only. Cases 1–21 accepted and
cases 36–50 out of scope except central request builders must emit valid D2D payloads for structural
preflight. No contracts/runtime/D3/Make/manifests/old tests.

## Closed trusted builders

All private ingress and structural request builders use accepted D2D exact payloads:

- child_event: state/native_id/fingerprint/result_digest; completed has result digest; launch_rejected
  has null native/result; all other states have native ID/null result. Fingerprints are stable
  canonical digests of event facts so identical calls are identical.
- attribution: exact subject kind/id, child relation, provider/model pair, observer, ordered covered
  artifact IDs. No magic semantic model strings, independence, aliases, or partial payloads.
- audit_verdict: findings array, current argument/goal/frozen/deferred digests, exact ordered approved
  gating reviewed_claims, scope_review relation. No deferred reviewed claim or identity field.
- evidence_leaf: syntactically complete sealed-result shape. Case 25 may use a structurally valid but
  deliberately unbound late payload because first-terminal rejection must occur before semantic
  admission; other evidence tests use real sealed request facts.

Preflight's 29 representative request envelopes all validate again. Update helper signature binding.

## Child corrections 22–29

- Identical first-terminal replay in cases 24/27/28 must assert exact `Inert` and complete RunView,
  operational, and ArgumentView equality. Conflicting/non-identical terminal replay asserts exact
  `Fault` and unchanged snapshots.
- Case 25 uses complete valid trusted payloads for every late event and asserts exact Inert/Fault per
  accepted D2D while preserving the first-terminal snapshot.
- Case 27 reserve spend is exactly baseline +1 in both variants; rejection refund is exactly baseline;
  post-start failure remains exactly baseline +1.
- Case 29 full_async is exact Allow with one `reserved` child and exact public host profile/tier;
  unsupported profiles have the sole exact registry reason, ordered actions/sections, no fallback.
- Remove unused duplicate `require_child_summary`; retain one child index/assertion path.

## Audit corrections 30–35

- `require_audit_scope` returns the exact active supporting C0 artifact ID so attribution can bind its
  producer. C1 added after freeze is explicitly `gating=false`.
- `build_audit_verdict_payload` uses canonical field `reviewed_claims`; delete `deferred_claim_id` and
  any branch appending deferred claims. It emits exact current approved-gating claims in ArgumentView
  order and the frozen/deferred aggregate tuple.
- Add one helper that submits trusted covered-actor attribution bound to exact active approved C0
  artifact IDs and auditor attribution bound to the pending SUT-admitted audit child. Same-model uses
  equal normalized provider/model pairs; decorrelated uses distinct pairs; unverified makes one pair
  null. Every trusted response is validated/asserted.
- Trusted audit_verdict is the atomic pending→completed child event. Assert its returned child is
  completed. Never send a second child completion afterward.
- Case 31 audit state is exact `required` before child admission; exact residual tuple/digests remain.
- Case 32 explicitly asserts deferred C1 is non-gating and non-approved. Valid C0 audit verdict is
  admitted atomically once; identical replay exact Inert with unchanged snapshot/ArgumentView;
  conflicting verdict exact Fault unchanged. C1 remains non-approved and cannot converge.
- Cases 33/34 establish both covered actor and auditor normalized identities through trusted ingress.
  Require the sole exact independence reason and exact projected independence for same_model and
  unverified. Author decorrelation attempt changes nothing.
- Case 35 reviewed_claims is exactly ordered `[C0]`, with exact current C0 evidence digest. C1 never
  appears there; deferred C1 is bound only by exact reviewed_deferred_scope_digest, run residual IDs,
  and scope_review pass. Trusted decorrelated identities are explicit. Audit verdict atomically
  completes child; GetArgument exact passed/decorrelated; Evaluate stopped_frozen/non-converged.

## Exactness/reduction

No `drv.artifacts`, fallback counters, set reason comparisons, fabricated admitted child IDs, magic
identity strings, optional/conditional assertions, or duplicate helper models. Canonical registry and
ArgumentView order own all vocab/order. Direct ordinary request remains confined to central dispatch.

## Report/verification

Report only after checks: S3a partial cases1–35 corrected; cases36–50 outstanding; zero post-binding
semantics. Recompute normal failure outcomes and LOC. Run preflight green, normal red only absent seam,
py_compile, check-core/static, forbidden grep, diff/check, empty index.

## Guardrails

Worktree only. No outside search/reviewer IDs/Git refs. No stage/commit/push/subagents. Stop on missing
accepted D2D input.
