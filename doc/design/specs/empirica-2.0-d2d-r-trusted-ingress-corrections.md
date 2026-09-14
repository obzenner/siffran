# Empirica 2.0 D2D-R — trusted-ingress review corrections

**Status:** Parent-frozen correction to D2D after Terra/Sol review.

## Scope

Correct only D2D contract/schema/fixtures/validator/docs. No D3/D4/runtime/Make/manifests/old tests.

## Corrections

1. Trusted audit `reviewed_claims` remains an ordered array in canonical ArgumentView claim order.
   Raw `uniqueItems` stays, and procedural validation additionally rejects duplicate `claim_id`
   before any map construction. Compare the ordered `(claim_id,evidence_digest)` list exactly to
   current approved gating claims; deferred claims are never included.
2. Trusted evidence `file_bindings` raw schema uses `uniqueItems`; procedural validation rejects
   duplicate normalized `path` even when hashes differ. Correlate payload bindings **exactly and in
   order** to the admitted spike result bindings, not only request dependent paths. Also correlate
   exact exit_code, command_digest, prerequisites, and result digest/artifact ID. Add independent
   one-mutation negatives for wrong hash, duplicate path, wrong exit, command, prerequisite, and
   result.
3. Audit verdict action `child_id` must resolve in the expected RunView to exactly one audit-purpose
   child in `completed` state. The action is the trusted host-observed completion bound from a
   previously pending child; it atomically admits verdict + terminal child result. D4 establishes
   the pending precondition and must not send a second child completion afterward. Add wrong-child,
   wrong-purpose, and non-completed postcondition negatives.
4. Auditor attribution child resolves to exactly one audit-purpose `pending` child. Covered actor
   attribution continues to resolve only current active artifacts belonging to approved gating
   claims. Add wrong-state negatives.
5. Add concise canonical `trusted/ingress` PublicContract section discoverable through GetContract.
   Clauses explain: payload shapes are closed by request schema; private capability admission;
   child/audit exact replay; normalized attribution facts and application-derived independence;
   audit verdict's atomic completion and exact approved-gating coverage; evidence exact sealed
   correlation. Do not duplicate field tables in prose.
6. PublicContract schema/fixtures/index/profile discovery mechanically include the new section.

## Replay verification boundary

D2D statically requires event/verdict fingerprint/digest facts and canonical clauses. It does **not**
add a persisted replay machine or pretend independent one-request fixtures prove behavior. D4 cases
24/25/27/28/32 exercise real bound-SUT replay semantics:

- identical child terminal replay => exact Inert, unchanged state;
- non-identical child terminal replay => exact Fault, unchanged state;
- identical audit verdict replay => exact Inert, unchanged state;
- conflicting audit verdict replay => Fault, unchanged state.

Update D2D wording that previously assigned replay procedural negatives to static contract fixtures.

## Tests

Add adversarial tests that directly fail before each correction and pass afterward. Validator error
messages must identify duplicate identity/path or mismatched field. No lossy dict before duplicate
checks, no EXPECTED vocabulary tables, and no second lifecycle/policy model.

Run `make contract-check`, `make check-static`, direct adversarial probes, `git diff --check`, empty
index.

## Guardrails

Worktree only; no outside search/reviewer IDs/Git refs, staging, commit, push, or subagents.
