# Empirica 2.0 D4-S3b — cases 36–50

**Status:** Parent-frozen final D4 milestone after accepted S1/S2/S3a and D2E.

## Scope

Correct only cases 36–50, listed shared assertions/preflight/driver helpers, and D4 report. Cases 1–35
are accepted and must not change except a shared assertion made strictly stronger. No contracts,
runtime, D3, Make, manifests, or old tests.

## Canonical selector data

Load `presentation_selector` from PublicContract beside existing registry data. Assert its structural
integrity in preflight; do not copy any context/fallback table. Tests compare real selector output
directly with relevant registry arrays/reason section arrays. No second selector/state model.

## Cases 36–42 — Blocks and progressive disclosure

36. Exercise two real Blocks: empty run → sole `graph.missing`; admitted canonical ordinary graph →
    sole `claim.research_missing`. For each, assert exact ordered reason code, empty/exact parameters,
    ordered next_actions/sections equal registry, nonempty message, and complete schema-valid RunView.
    Research-missing affected is exactly one obligation_id that resolves to the returned RunView
    obligation; no permissive affected-key subset.
37. Through real selector seam, for every canonical operation context with no reasons/status assert
    exact ordered output equals `presentation_selector.context_sections[context]`; call twice for
    determinism. Also a known terminal status appends exact `terminal_sections` with first-occurrence
    dedupe.
38. Fix presentation inputs (block + one known reason + null status), vary workspace and fake transport
    facts only, and require exact unchanged ordered output equal the canonical block base plus reason
    sections, first occurrence deduped. No domain-state inference.
39. Unknown reason and unknown/nonterminal status each return exactly ordered
    `unknown_reason_sections`, not a subset. Unknown action and unknown GetContract section return
    exact Fault/closed through existing raw/ordinary seams. Do not hardcode fallback sections.
40. Multiple ordered known reasons return exact ordered first-occurrence union of block context base
    then each registry reason section; reversed reason input produces its corresponding canonical
    order; no irrelevant/duplicate section.
41. GetContract index/full and representative sections including `presentation/selector` return exact
    requested projection, canonical identity/digest, no sibling target payload. Unknown section is
    Fault/closed (case39).
42. StartRun exact Allow and no dump; Evaluate on empty run exact sole graph.missing Block and no dump;
    compacted view no full contract/private material. No conditional-only assertion.

## Cases 43–44 — compaction/reload

Use a real public setup: C0 ordinary approved, freeze, C1 deferred, foreground audit child driven to
pending. Capture complete GetRun.

43. Compaction preserves exact goal, status, modes, contract identity/digest/relevant sections,
    active/deferred obligation rows, residuals, freshness, pending child summaries, host, and ordered
    next_actions from the captured RunView. It adds/exposes exact canonical untrusted delimiters only
    where the compaction surface specifies them. Assert pending child, deferred C1 residual, no full
    contract, native/capability, operational counters, persisted revision/history/phase, or hashes.
44. After compact + reload, use reloaded driver for GetRun and RestoreRun. Both complete bounded
    RunViews equal the pre-compaction RunView exactly, including run ID/pending child/deferred facts.
    Assert banned persisted/private fields recursively, not only top-level.

## Cases 45–47 — strict protocol/state decoding

Raw malformed requests still pass response schema validation.

45. Persisted non-v2 identity with hostile/partial semantic fields restores as sole `run.corrupt`
    without reusing those fields. A non-v2 wire envelope is exact Fault/closed.
46. Wire protocol variants null/empty/v1/future/partial are exact Fault/closed. Persisted identities
    null/missing/v1/future and malformed exact-v2 state all produce the same sole `run.corrupt`
    Block with fixed safe fields, start-fresh recovery, and no migration/defaults.
47. Unknown top-level request field, command field, action kind, and action field each exact
    Fault/closed. Exact-v2 persisted unknown status is sole `run.corrupt` Block with canonical
    start-fresh recovery. No Fault|Block unions.

## Cases 48–50 — hosts and privacy

48. Default host RunView profile ID and tier equal exact registry entry; missing capabilities equal
    exact registry list/order; no schema parity inference or unknown fields.
49. Iterate all canonical profile IDs. For each StartRun public host view equals exact registry
    profile/tier/missing-capability projection. Assert all registry profiles observed exactly once.
50. Start run and reserve one admitted foreground child. Publicly dispatch a syntactically valid
    closed D2D child_event with forged capability; require Fault/Block capability rejection and
    complete RunView/ArgumentView unchanged. Then deliver valid launching event through private
    ingress to exercise the real boundary. Assert no capability, capability_ref, native_id, terminal
    fingerprint, provider/model private attribution, or supplied secret marker appears in any public
    response, GetRun, GetArgument artifacts, Evaluate Block, GetContract index, compacted view, or
    rejection diagnostic. Do not inspect `drv.artifacts()` or internal operational state as if public.

## Shared exactness and preflight

- Add ordered-dedupe test helper only if it consumes caller-supplied registry lists and contains no
  context/reason mapping.
- Add recursive banned-key/value assertion for public/compaction surfaces.
- Strengthen strict result helpers rather than accepting unions.
- Preflight checks presentation_selector loaded (not copied), exact selector protocol/live signature,
  raw helper confinement, zero direct request, no conditional-only assertions, valid representative
  envelopes/fixtures. It executes no selector/SUT behavior.
- Remove obsolete `_SAFE_FALLBACK`, fake artifact privacy checks, and copied banned-policy constants
  where canonical/private-key helpers already own them.

## Report and verification

Report all 50 cases statically corrected; zero post-binding semantics; cases grouped by future owner;
current normal failure count/LOC; no probe/mutation adequacy claim. Run preflight green, normal red only
absent seam, py_compile, check-core/static/claude/codex/pi where available, forbidden-pattern scans,
diff/check, empty index.

## Guardrails

Worktree only. No outside search/reviewer IDs/Git refs. No staging, commit, push, or subagents. Stop if
strict expected Block/Fault semantics contradict accepted D1/D2 schemas rather than weakening tests.
