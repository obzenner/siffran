NOT ACCEPT

## Findings (severity ordered)

### High — Host audit lifecycle still classifies children by author-controlled purpose

**Locations:** `plugins/empirica/adapters/audit_protocol.py:97–100`, `:112–119`, `:147–153`; `plugins/empirica/adapters/claude/lifecycle.py:310–320`. Related changed admission: `plugins/empirica/core/evaluation.py:522–550`; class is omitted from host-visible summaries at `plugins/empirica/core/projection.py:103–110`.

Seam 7 deliberately permits an investigation-class child with `purpose="audit"` (the new transaction test explicitly proves this). Core budget selection and durable audit binding correctly use resource_class, but shared audit preparation, candidate selection, orphan reconciliation, and Claude reserved-plan discovery still use purpose. The resulting records cannot reliably be distinguished using the public child summary, which contains only child_id, purpose, state, and optional deadline/recovery fields.

**Reproduction logic (source-traced; not a newly executed reproduction):**

1. Admit the graph/investigation prerequisites and an ordinary child with `resource_class="investigation"`, `purpose="audit"`, and an ordinary worker role. Leave it reserved or pending. Investigation usage becomes 1; protected audit usage remains 0. This admitted state is already demonstrated by `plugins/empirica/tests/test_d7_transactions.py:423–479`.
2. Invoke the canonical host auditor path, which calls `AuditProtocol.prepare`. Its GetRun precheck sees the ordinary child's purpose and raises `AuditProtocolError("an audit operation is already active")` at lines 97–100, before attempting an audit reservation. Thus ordinary investigation prevents use of the available protected mandatory-audit capacity even though core correctly allows both classes.
3. On recovery, `AuditProtocol.reconcile_orphans` also treats that ordinary child as an audit operation and sends it an orphaned terminal event at lines 147–153. This terminalizes the wrong class of child. A reserved investigation child is thereby made permanently spent rather than eligible for a launch-rejection refund.
4. Under concurrent insertion between prepare's initial GetRun and reservation response, the purpose-only candidate list can include both a new ordinary `purpose="audit"` child and the actual audit child. Its ambiguity cleanup then sends launch-rejection events to every candidate, including the ordinary reservation (lines 112–119).

These are newly exposed interactions of the split-class semantics, not a request to change unrelated historical lifecycle behavior. Fix host audit selection and recovery to consult authoritative resource_class/private audit binding, not purpose. Carry the classification through the appropriate trusted/public projection contract if needed, and update all three hosts' shared protocol tests accordingly.

## Reviewed behavior and validation

- Core reserve selects exactly one account using the explicit resource_class, includes it in durable child identity, and initializes independent bounded investigation/audit defaults.
- Ordinary Claude/Pi translations hard-code investigation; shared canonical AuditProtocol reservation hard-codes audit. Codex budget configuration carries max_audit_spawns.
- Refund selects the account from the durable child record; the existing terminal fingerprint/replay and CAS retry structure is retained.
- Persisted-state decoding requires the new fields, rejects invalid classes and mismatched counters, reconciles each account against non-refunded children, and rejects multiple simultaneously active audit children. No migration of current-schema old documents is silently attempted.
- Public configuration schemas and vendor copies contain the new audit limit/reason vocabulary. Vendor validation passed byte-for-byte.

Executed focused Make targets:

- `make help` — passed.
- `make contract-check vendor-check empirica-d7-transactions empirica-d7-conformance` — passed: contracts reported 10 schemas, 12 fixtures, 32 v2 fixtures; 5 obligation and 7 runtime contract vendor files byte-identical; 26 transaction tests and 11 selected D7 conformance tests passed.

Inspected the diff against `70a6e6f`, relevant strict-state/transaction/budget/async-child tests, shared audit protocol tests, and Claude/Codex/Pi adapter changes. No source edits, staging, commits, web research, or child launches were performed. This report is the sole review-authored artifact.

## Coverage gaps

- The new purpose-collision transaction test stops at core admission and does not drive the resulting state through AuditProtocol.prepare, reconciliation, or Claude reserved-plan discovery.
- AuditProtocol test fixtures omit resource_class and do not include an investigation child whose purpose is exactly audit.
- Refund/replay conformance assertions were redirected to audit_spawns_used, but this diff does not add a two-class refund matrix proving the other nonzero account remains unchanged for both rejection and post-start failure.
- The focused transaction suite provides general CAS coverage, but the new split-budget test is sequential; mixed-class reserve/refund contention is not specifically covered by the inspected additions.

## Residual risks

- Full installed-host Claude/Codex/Pi execution and the complete async-child/adapter suites were not run in this review. Passing the focused suites does not attest live host behavior.
- The adapter purpose collision remains acceptance-blocking despite correct core account arithmetic and passing schema/vendor checks.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "One high-severity, source-traced host lifecycle classification finding with exact file/line references and reproduction logic; coverage gaps and residual risks are documented."
    }
  ],
  "changedFiles": [
    "doc/design/reports/seam7-astra-review.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "make help",
      "result": "passed",
      "summary": "Read the canonical lifecycle target list."
    },
    {
      "command": "make contract-check vendor-check empirica-d7-transactions empirica-d7-conformance",
      "result": "passed",
      "summary": "Contract/vendor checks passed; 26 transaction and 11 selected D7 conformance tests passed."
    },
    {
      "command": "git status --short; git diff --stat 70a6e6f; git diff 70a6e6f (targeted paths), plus numbered source inspection",
      "result": "passed",
      "summary": "Inspected uncommitted scope and relevant implementation/tests; initial status had no staged files."
    }
  ],
  "validationOutput": [
    "ok: 10 schemas, 12 fixtures, 32 v2 fixtures",
    "ok: 5 byte-identical obligation vendor files",
    "ok: 7 byte-identical Empirica runtime contract vendor files",
    "D7 transactions: Ran 26 tests; OK",
    "Selected D7 conformance: Ran 11 tests; OK"
  ],
  "residualRisks": [
    "AuditProtocol and Claude reserved-child discovery still infer audit identity from purpose, exposing protected-capacity denial and wrong-child lifecycle/refund handling.",
    "Live installed-host paths, full adapter suites, and mixed-class CAS/refund races were not executed during this review."
  ],
  "noStagedFiles": true,
  "diffSummary": "Reviewed Seam 7 split investigation/audit budgets, explicit durable resource classes, exact counter reconciliation, adapter/schema/vendor propagation, and associated regression tests against HEAD 70a6e6f. Review wrote only this findings artifact.",
  "reviewFindings": [
    "HIGH: plugins/empirica/adapters/audit_protocol.py:97-100,112-119,147-153 and plugins/empirica/adapters/claude/lifecycle.py:310-320 still select audit children by purpose, conflicting with newly valid investigation children named audit."
  ],
  "manualNotes": "Decision: NOT ACCEPT. Finding reproductions are source-traced, not claims of newly executed end-to-end tests."
}
```
