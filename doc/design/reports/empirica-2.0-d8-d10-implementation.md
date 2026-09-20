# Empirica 2.0 D8–D10 implementation report

**Status:** implementation and deterministic conformance complete. Exact-candidate installed-host
certification remains a separate release gate.

## D8 — durable child lifecycle

The operational child record implements reserved, launching, pending, completed, launch-rejected,
failed, cancelled, timed-out, and orphaned states. The single-writer coordinator owns transition
validation, one native binding, first-terminal-wins, exact replay idempotency, conflicting replay
rejection, launch-rejection refund, and CAS persistence. Public views redact native IDs and
capabilities. Every reservation reaches a canonical terminal state or remains an explicit active
obligation.

## D9 — audit and progressive contract

Trusted ingress records content-addressed attribution and audit artifacts. Independence is derived
from host-observed author/auditor identity, never author input. Audit verdict admission binds one
run, durable operation, child, role, dossier, native execution, complete current evidence set, and
first terminal result. Audit may block but cannot manufacture deterministic evidence.

GetContract, context selection, compaction/reload, deferred-scope projection, and graph corruption
handling consume the canonical PublicContract and return bounded v2 views.

## D10 — host honesty

Each transport is bound to an exact registry profile. Claude Code 2.1.270 and
Pi 0.84.1 + pi-subagents 0.50.0 are promoted for foreground execution only. Their async candidates
remain unsupported. Codex 0.146.0 is observational and `wip_unsupported` because the host cannot
independently observe its resolved auditor-model identity.

Deterministic adapter checks prove translation and fail-closed policy composition, not installed
reachability. Release certification additionally requires fresh, operator-attested,
candidate-bound structural receipts for the supported Claude and Pi profiles. The trusted release
operator is the receipt trust root; the verifier parses and correlates retained native JSONL,
durable state, identity, verdict, result, version, and release commit rather than trusting summary
fields.

## Verification

The current repository gate is `make check`, `make empirica-architecture-check`, and
`git diff --check`. `make release-check` adds exact-candidate installed-host receipts and therefore
is expected to fail until those fresh receipts are captured. Historical red-first reports retain
the sequence in which the conformance suites were introduced; their “expected red” wording does
not describe the current tree.

No v1 migration, legacy fallback, generic host profile, Codex promotion, or async promotion is part
of this release.
