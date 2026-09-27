# Governed initialization (public contract 3.0.0)

## Prepare without investigating

The default control mode is **deliberative**. Route, then propose a root-connected claim DAG from supplied context. The claim graph defines the work but is not part of human approval. The approval digest binds the immutable goal as read-only context plus run configuration: budget ceilings, modes, and control mode.

Submit `configure_run` with proposed `budgets` and `modes`. Omitted fields retain the current configuration. This requests host review; it is not approval. The schema rejects reviewer fields because reviewer selection belongs to host configuration. Only the exact current approved configuration permits `investigate`.

The ordinary host review displays the goal read-only and the complete configuration in plain language. Edits are submitted for another review and are not approved. Claude and Pi immediately open one host-owned **FINAL CONFIRMATION** in the same call. It is read-only and offers only Confirm or Decline. No author action runs between amendment and confirmation. The original timeout, raw receipt fingerprinting, CAS, and exact epoch/digest checks apply across both forms.

Cancel, timeout, invalid content, stale/conflicting replies, and missing UI never grant authority. Presentations are CAS-reserved before UI and bounded to three per configuration epoch and 128 per run. Exact current-schema receipt replay is inert; conflicting replay blocks. Configuration changes revoke approval. Graph and host-observed author changes do not; the audit dossier continues to bind the goal, graph, and scope.

## Explicit auto

`/empirica --auto <goal>` (Codex: `$empirica --auto <goal>`) is visibly automatic acceptance, not human approval. Existing budget/mode gates remain: auto cannot increase an operational ceiling. Reviewer selection still comes from host configuration, and observed audit independence remains required for convergence.

## Host mediation and reviewer selection

- **Claude:** requires client-advertised MCP form elicitation. If `CLAUDE_CODE_SUBAGENT_MODEL` is explicitly set (other than `inherit`), the host resolves it. Otherwise the adapter supplies only a resolvable alias from a family different from the observed main family. Third-party providers require the corresponding `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` pin.
- **Pi:** requires `ctx.hasUI` for approval. The auditor package is launched in the project or user scope that resolves this package's own auditor file. Its model follows host settings: `subagents.agentOverrides["empirica.empirica-auditor"].model`, then `subagents.defaultModel`, then the main model. An unavailable or same-model reviewer blocks before launch.
- **Codex:** deliberative approval and audit remain unavailable.

Reviewer selection is not itself evidence of independence. Actual host-observed reviewer and covered-actor identities must still be known and different. Only exact documented model/version mappings establish equivalence across providers; moving/private aliases remain unknown.

## Timeout, compatibility, and recovery

The decision timeout defaults to 900 seconds. `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` accepts finite 1..1500-second host-owned overrides. It is displayed read-only and is never approvable. Pi shares one timeout across the dialog sequence.

Public contract 3.0.0 keeps wire `empirica/v2` and state family `empirica.run/2`. Persisted documents with removed governance fields are intentionally incompatible with the strict current schema and fail closed as `run.corrupt`. A later `StartRun` creates a fresh generation. The runtime never mutates, repairs, converts, or carries approval from an obsolete document.

Deterministic unit/integration tests are not native human-approval or installed-host qualification receipts.
