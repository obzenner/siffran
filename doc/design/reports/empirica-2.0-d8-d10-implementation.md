# Empirica 2.0 D8–D10 implementation report

**Status:** implemented and independently accepted; final release gates green.

## D8 — durable child lifecycle

The operational child record now implements the canonical reserved, launching, pending, completed,
launch-rejected, failed, cancelled, timed-out, and orphaned lifecycle. The single-writer coordinator
owns transition validation, one native binding, first-terminal-wins, exact replay idempotency,
conflicting replay rejection, launch-rejection refund, and CAS persistence. Public views redact the
native ID and capability reference. Foreground lifecycle behavior is available on every exact host
profile; async requests remain honestly blocked according to each profile's declared tier.

## D9 — audit and progressive contract

Trusted application ingress now records content-addressed attribution and audit artifacts. Audit
independence is derived from covered-actor and auditor identity facts, never accepted from author
input. A verdict atomically completes its admitted audit child, exact replay is inert, conflicting
replay is closed, and audit cannot manufacture deterministic evidence.

The release also adds:

- pure deterministic context selection from operation context, ordered reason codes, and terminal
  status only;
- exact GetContract index, section, and explicit-full projections;
- offline/self-contained public response-schema validation;
- bounded compaction and repository-backed reload/restore;
- deferred-scope residual and audit-dossier projection;
- canonical graph-missing versus graph-invalid handling.

## D10 — host honesty

Every transport remains bound to one exact registry profile. Public and failure-safe views project
the profile's exact tier and missing capabilities. No candidate tier was promoted: Claude, native Pi,
and Pi+subagents remain `foreground_only`; Codex remains `observational`. Unsupported async/audit
operations return canonical typed reasons rather than generic success.

## Conformance corrections

The red-first suite exposed several harness contradictions while becoming executable. Corrections
preserved product invariants rather than weakening production boundaries:

- intentionally malformed author-forgery requests use the raw-wire seam instead of the normal
  schema-valid dispatch seam;
- lifecycle-only cases install a minimal graph before inspecting ArgumentView, while a genuinely
  missing selected graph still fails closed;
- a two-claim audit fixture preserves the already-researched root wording/kind;
- current observations are asserted through workspace telemetry and are not mislabeled as
  freshness changes;
- host-neutral lifecycle tests use supported foreground execution while the dedicated host-tier
  case continues to prove async blocking.

## Verification

- Full D4 behavioral suite: **50/50 green**.
- D8 lifecycle suite: **8/8 green**.
- D9 audit suite: **6/6 green**.
- Context-selection/GetContract suite: **7/7 green**.
- Compaction/reload suite: **2/2 green**.
- Contract check: **10 schemas, 11 legacy fixtures, 33 v2 fixtures green**.
- Effective runtime: **8,366 LOC across 72 files**, 1,092 below the 9,458 baseline and 1,091 below
  the 9,457 release maximum.
- Full `make check`: green across static, core, Claude, Codex, and Pi suites.
- Final Astra review: **ACCEPT**, no remaining BLOCKER or MAJOR.
- Architecture violations: **0**.

No host capability promotion or backwards-compatibility path was added.
