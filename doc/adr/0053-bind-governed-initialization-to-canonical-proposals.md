---
number: 53
title: "Bind governed initialization to canonical proposals"
status: accepted
date: 2026-09-21
tags: [empirica, governance, approval, identity]
links:
  - target: 49
    kind: Refines
---

# Bind governed initialization to canonical proposals

## Context and Problem Statement

A model-authored configuration cannot evidence human consent.  Governance must bind the exact
claim graph, goal, ceilings, modes, auditor policy, and revision policy that the host presents;
otherwise an author can alter scope or capacity after a purported approval. Provider/model strings
also cannot safely prove cross-provider independence.

## Decision Outcome

The host-neutral policy uses a canonical SHA-256 proposal digest over every governed field. A
host-private receipt names that exact digest and supports `present`, `approve`, `amend`, `reject`,
or `dismiss` outcomes. A CAS-bound `present` reservation is required BEFORE human UI opens; only
new Allow may display, never Inert. Its immutable presentation fingerprint binds the original
run/revision/digest/approval kind while one legal final transition updates the same receipt slot.
Selected manifests preserve prior state in append-only history. Exact present replay stays inert
even after finalization; exact final replay is inert, conflicts/stale finals are no-write. Abandoned
or stale presentations remain charged. Public author actions cannot supply a receipt.

Control mode is explicit and immutable: `deliberative` is the default and `auto` is opt-in. Auto
has a finite material-revision ceiling of eight and never autonomously raises operational ceilings.
Existing pass/spawn/audit counters remain the sole charging source for work. Human presentations
have a separate finite interaction bound derived from the existing receipt slots: three per
material proposal/revision and 128 per run, including all pre-approval revisions. Reservation and
completion use the same slot, so the third prompt can still finish and concurrent calls cannot
all pass a preflight against one remaining slot. Missing UI/capability/inventory does not reserve;
a crash after reservation may conservatively consume capacity. Dismissal never asserts rejection.

Identity normalization retains raw provider/model provenance and maps only documented concrete
wire spellings while preserving complete version/variant suffixes. Unknown mappings remain
`unknown_equivalence`; they are never called decorrelated. The private approval transaction
permits `same_model` only with a positive, complete, current authorized singleton inventory.
Unmapped unrelated inventory members remain visible/digest-bound and prevent singleton proof,
but do not block a known different author/auditor pair. Unknown author and selected auditor are
typed separately from a positively observed mismatch. Exact configured Bedrock wrapper aliases
are supported; unknown provider shapes and moving/private aliases are not guessed.

Auto with a null auditor prepares a stable known eligible selection in the private context
transaction before digest-bound approval. Same-model auto requires the explicit immutable auto
policy plus positive singleton proof. Human singleton consent uses a dedicated warning/control;
changing that choice amends the proposal and requires another final decision. Both hosts use a
900-second default total decision deadline and host-owned finite 1..1500-second override; timeout
dismisses without automatic re-prompt. MCP answers ping while waiting and correlates cancellation
to the active tools/call request, not an unrelated ID.

## Consequences

* Good: approval is exact, replay-safe, and does not confuse chat output with human authority.
* Good: aliases and provider differences cannot fabricate independent-model evidence.
* Bad: hosts without a qualified approval ingress or attested inventory cannot investigate in
  deliberative mode.
* Cost: the pure policy module is deliberately small; no unbounded proposal history or duplicate
  budget counter was added. The architecture guard remains the enforceable overall size limit.

## Confirmation

`make empirica-governance-check` covers graph digest binding, bounded auto policy, exact-receipt
idempotence/conflict, version-preserving cross-provider normalization, and unknown aliases.

## Integrated runtime and compatibility

The single runtime now requires governance at StartRun and exposes contract **2.1.0** in place
under `empirica/v2` / `empirica.run/2`. Plugin release **3.2.0** requires fresh run generations;
missing governance is corrupt, never a legacy approval default. Private human `present` receipts
can finalize as `approve`, `amend`, `reject`, or `dismiss` through the real service/CAS transaction;
auto receipts have no presentation fingerprint and never imply a human decision. Amendments require a second exact
approval. Reads, route, proposal corrections, and honest stop remain available while pending.
Investigation, evidence, successful child admission, audit, and convergence require current approval
on every request. Trusted failure-only terminal cleanup of an already reserved execution remains
available after revocation; registry edges and immutable accounting still govern it.

Claude uses capability-negotiated MCP form elicitation with correlated server request IDs;
concurrent client requests cannot become replies. Pi uses the documented `hasUI`, select, input,
and confirm surface. Codex deliberative approval fails explicitly unavailable; auto also blocks
for unmapped real active slugs such as `gpt-5.1-codex`. Simulated dated-snapshot tests do not prove
installed support, and process auditor identity remains unobservable. Operator inventory is host-read JSON
(`EMPIRICA_GOVERNANCE_CONFIG`); Pi uses its authenticated configured registry. The exact operator
format and trust limitations are documented in the skill's `references/governance.md`.

Singleton exceptions are implemented, not deferred: both actual host-observed models must equal
the approved sole concrete member, with positive completeness/authorization and unchanged
inventory. The basis remains visible as `operator_declared` or `pi_registry`, never an assertion
of worldwide/live model availability. Canonical role/dossier/read-only acceptance are retained;
plugin model pins and author launch overrides do not choose the auditor.

## Alternatives considered

* Default-approved legacy runs or public approval fields: rejected; both permit authors to infer
  or manufacture consent and require a second runtime meaning.
* Gate only the initial investigation witness or omit the graph from approval: rejected; later
  material changes would bypass exact scope consent.
* Unbounded autonomous replanning or budget escalation: rejected; auto is explicitly selected at
  start, cannot raise ceilings, and has eight material revisions. Deliberative revisions are
  bounded to 64; private receipts to 128.
* Reuse only a disconnected policy helper: rejected; every policy decision must reach selected
  state, immutable history, projection, and host transport through the same transaction boundary.

## Finite architecture cost

The old guard was already red before governance: the integration baseline had **9,635** effective
runtime lines versus the **9,484** ceiling. S0 isolation was measured at **9,654**. The historical
9,458-line reference at `4257c8d` remains in `architecture.json` for comparison; it is not reset to
conceal the overrun. The first reviewed integrated candidate was **10,488 lines across 82 runtime
files**. The corrected final inventory is **10,647 lines across 82 runtime files**, measured by
`make empirica-architecture-check` (tests excluded, generated/vendor Python/TypeScript included).
The measured-exact finite ceiling is **10,647**, with no discretionary percentage headroom:
**+159** versus that first candidate, **+1,012** versus HEAD's 9,635, **+1,189** versus historical
9,458. Any further runtime growth must remove code or obtain another explicit architecture decision.

The additional 159 lines pay for durable pre-dialog reservations/finalization and bounded
cancellation, shared partial-inventory/auto-preparation policy, precise unknown-subject errors,
explicit singleton consent, validated deadline handling, MCP ping/cancellation correlation,
exact Codex exemptions, and conservative amendment serialization bounds. Those corrections
close reviewed counterexamples; they are not a reserve for unrelated features.

This accepts additive cost for the pure canonical proposal/identity policy, one CAS-backed private
transaction, host inventory/form mediation, correlated MCP server requests, Pi dialogs, and gates
at existing admission/attribution boundaries, including default-deny investigation admission for
Claude third-party MCP tools and writers (not just the inherited built-in allowlist). These enforce consent that no previous subsystem
owned; deleting unrelated evidence, freshness, audit, or isolation behavior to fit the obsolete
ceiling is not an acceptable trade. No prohibited-symbol, dependency, thin-hook, or vendor rule is
relaxed. The architecture target is now composed into `check-static`/`check`, not silently skipped.

## Qualification boundary

`make empirica-governance-check` exercises real service/CAS transitions, singleton bound audit,
identity mismatch, and a real MCP subprocess with a simulated client. `make check-pi` exercises
fake documented UI against the real Python bridge; all host suites retain actual lifecycle tests.
These establish deterministic integration, **not** native human approval, installed-host receipts,
or proof that a physical human (rather than operator-controlled RPC) answered. Native/manual
qualification requires separately authorized operator-present sessions and no test writes global
configuration or installs Empirica into live sessions.
