# Governed initialization (public contract 3.0.0)

## Prepare without investigating

The default control mode is **deliberative**. Route, then propose a root-connected claim DAG from supplied context. Every claim and `SupportedBy` edge, the goal, budgets, modes, selected reviewer, host-observed main model, control mode and revision limit are covered by the canonical proposal digest. Ordering alone does not change approval.

Submit `configure_run` with proposed `budgets`, `modes`, and an optional concrete reviewer as `auditor: {provider_id, model_id}`. Omitted fields retain the current proposal. This requests host review; it is not approval. Only the exact current approved proposal permits `investigate`.

Empirica receives no model catalog, operator inventory, credentials, or provider configuration. The host supplies only its observed main-model identity, the selected reviewer, and later the actually observed reviewer. The core recognizes a deliberately finite set of exact concrete model spellings and conservative provider aliases. Unknown main or reviewer identity blocks. A reviewer that normalizes to the main model always blocks; there is no waiver or singleton exception.

The ordinary host review displays the complete proposal in plain language. Untrusted scope text is fenced and control/bidi characters are escaped. Reviewer edits use two bounded scalar fields—provider and exact model id—not JSON or a catalog picker. Empty values keep an existing reviewer; when no reviewer exists, approval remains blocked until a complete pair is proposed. A one-sided, oversized, cancelled or expired edit dismisses without approval. Reject and Decline remain available when no reviewer is set.

Edits are submitted for another review and are not approved. Claude and Pi immediately open one host-owned **FINAL CONFIRMATION** in the same call. It is read-only and offers only Confirm or Decline; feedback remains a separate ordinary-review path. No author action runs between amendment and confirmation. At most two forms are used. The original deadline, raw receipt fingerprinting, CAS and exact revision/digest checks apply across both forms.

A plain-language change request is durable guidance bound to the displayed revision and digest, never consent or evidence. Approval and feedback cannot share one input surface. Cancel, timeout, invalid content, stale/conflicting replies and missing UI never grant authority. Presentations are CAS-reserved before UI and bounded to three per revision and 128 per run. Exact current-schema receipt replay is inert; conflicting replay blocks.

Material graph, budget, mode, selected-reviewer, or host-observed main-model changes revoke approval even after investigation. Consumed counters and history remain, while stale evidence and audit bindings do not become current. Freeze preserves approval but keeps committed frozen meaning immutable.

## Explicit auto

`/empirica --auto <goal>` (Codex: `$empirica --auto <goal>`) is visibly automatic acceptance, not human approval. Auto accepts only an author-proposed concrete reviewer that the same core predicate knows is distinct from the host-observed main model. Null, unknown or same-model reviewers block; no catalog is scanned and no fallback is selected. Selection is not a claim of availability, separate operator authorization or successful execution. The host must still execute a reviewer and observe an actual identity matching the selected reviewer.

Existing budget/mode gates and finite revision bounds remain: auto cannot increase an operational ceiling and permits at most eight material revisions after first approval; deliberative mode permits at most 64. Reviewer or main-model changes invalidate stale approval and audit bindings while preserving consumed counters.

## Host mediation and observed identity

- **Claude:** requires client-advertised MCP form elicitation. No governance configuration file is read.
- **Pi:** requires `ctx.hasUI` and documented select/input/confirm dialogs. Governance never enumerates `modelRegistry`; host execution-contract resolution remains separate and is not persisted, projected or digested.
- **Codex:** deliberative approval is unavailable. Auto also blocks for unmapped active slugs such as `gpt-5.1-codex`. Simulated dated-snapshot conformance is not installed-host qualification.

The host injects the selected reviewer and canonical read-only dossier into its execution contract. Actual host-observed reviewer and covered-main identities must match the approved selected reviewer and main model. Substitution or unobservable identity revokes/blocks approval. Covered-actor attribution remains evidence provenance and the observed main half of this pair.

Only exact documented model/version mappings establish equivalence across providers. Different providers neither are required nor prove independence. Recognized spelling is not proof that an account can use a model; moving/private aliases remain unknown until source-backed mapping is added.

## Limits, compatibility and recovery

Goal, claims, edges, IDs, feedback, numeric edits, receipt history and interaction time are bounded by the public contract. Reviewer identifiers are at most 128 characters. The decision deadline defaults to 900 seconds; `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` accepts finite 1..1500-second host-owned overrides. Pi shares one deadline across the dialog sequence.

Public contract 3.0.0 keeps wire `empirica/v2` and state family `empirica.run/2`. Inventory-shaped persisted runs are intentionally incompatible with the strict current schema and fail closed as `run.corrupt`. A later `StartRun` creates a fresh generation. The runtime never mutates, repairs, converts, or carries approval from the old document; historical bytes remain preserved. Receipts inside those obsolete documents are not replayable by the new runtime. Exact raw replay remains supported and tested only for documents valid under the current schema.

Deterministic unit/integration tests are not native human-approval or installed-host qualification receipts.

## Identity spelling sources

The finite map follows Anthropic's [model overview](https://platform.claude.com/docs/en/about-claude/models/overview), exact provider wire IDs, dated OpenAI snapshots, and documented Bedrock inference-profile prefixes. No family/latest alias, provider wildcard, credential/configuration lookup, or inferred private mapping is used.
