# Governed initialization (public contract 3.0.0)

## Prepare without investigating

The default control mode is **deliberative**. Route, then propose a root-connected claim DAG from supplied context. The claim graph defines the work but is not part of human approval. Size the run from that graph and the task: count gating claims, estimate how many need deterministic experiments, and budget the expected audit rounds (normally 2: an initial audit and one retry). The author agent chooses the ceilings; adapters and core only transport, display, and enforce them. The approval digest binds the immutable goal as read-only context plus run configuration: all three budget ceilings, control mode, and the exact rationale.

Submit `configure_run` with **all three** proposed ceilings and a nonblank 1–600 character `rationale`. Explain why the graph and task need that size; do not write a command, authority claim, evidence claim, or approvable conclusion. This requests host review and never approves itself. The schema rejects missing ceilings and reviewer fields because reviewer selection belongs to host configuration. Only the exact current approved configuration permits `investigate`.

The ordinary host review displays the goal read-only, the three budget ceilings, and escaped literal rationale under **Agent sizing rationale — unverified**. Claude uses its built-in **Accept** button to approve the displayed values, **Decline** to reject, and Esc to decide later; editing any value and accepting submits a ceiling-only amendment. Pi presents the same model in one host-native component. Amendments immediately open one host-owned locked confirmation in the same call. It shows original versus amended ceilings and warns **Written for the original proposal; not regenerated for these human-edited values**. It is read-only: Accept/Approve approves exactly that revision, while Decline/Esc/Keep pending retains the edits without consent. No author action runs between amendment and confirmation. The original timeout, raw receipt fingerprinting, CAS, and exact epoch/digest checks apply across both forms.

Cancel, timeout, invalid content, stale/conflicting replies, and missing UI never grant authority. After dismissal, timeout, or rejection the author resubmits `configure_run` (same or human-edited ceilings) to reopen the dialog and records `investigate` only once the view says `approved`. Presentations are CAS-reserved before UI and bounded to three per configuration epoch and 128 per run. Exact current-schema receipt replay is inert; conflicting replay blocks.

## Modes

- **Deliberative:** every material configuration change returns to the human for review and locked confirmation when amended.
- **Interactive auto:** the initial complete sized proposal has one human approval episode before investigation. Dismissal, timeout, cancellation, rejection, a declined confirmation, or unavailable UI grants nothing and never falls back to delegation. After the first successful approval, non-raising changes are accepted automatically without another dialog; raises are refused. Exhaustion requires a fresh run rather than a prompt or `budget.raise` loop (ADR-0063). On exhaustion the host settles the turn with a notice instead of blocking; stop honestly with `report_convergence` `intent: "stop"` or, in deliberative mode, propose a raise for human approval.
- **Delegated auto:** with no human present and operator-recorded `EMPIRICA_AUTO_DELEGATION=1`, proposals may be accepted only inside the fixed 8/1/2 envelope. StartRun budgets and `EMPIRICA_MAX_*` may narrow but never enlarge it. Oversize work must reduce scope or move to an approval-capable host interactively.

Reviewer configuration still comes from the host, and observed audit independence remains required for convergence.

## Host mediation and reviewer configuration

- **Claude:** requires client-advertised MCP form elicitation. If `CLAUDE_CODE_SUBAGENT_MODEL` is explicitly set (other than `inherit`), the host resolves it. Otherwise the adapter supplies only a resolvable alias from a family different from the observed main family. Third-party providers require the corresponding `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` pin.
- **Pi:** requires `ctx.hasUI` for approval. Package-scope and reviewer-model derivation are owned by the [Pi adapter guide](../../../adapters/pi/README.md#governed-initialization); unavailable or same-identity-class reviewers block before launch.
- **Codex:** no approval UI; deliberative and non-delegated auto sizing cannot be approved. Delegated auto remains inside 8/1/2, while audit remains unavailable.

Reviewer configuration is not itself evidence of independence. Audit identity classification and its fail-closed cases are owned by the [audit guide](audit.md#independence-reporting). Only exact documented model/version mappings establish identity equivalence across providers; moving/private aliases remain unknown.

## Timeout, compatibility, and recovery

The host decision timeout defaults to 900 seconds. `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` accepts host-owned overrides written as whole seconds in plain decimal digits from 1 to 1500 (no fractions, signs, exponents, or hex); a set but malformed or out-of-range value fails extension or server start with an error naming the variable instead of defaulting. It appears in the dialog header and is never approvable. Pi shares one timeout across review and locked confirmation.

Public contract 3.0.0 keeps wire `empirica/v2` and state family `empirica.run/2`. Persisted documents with removed governance fields are intentionally incompatible with the strict current schema and fail closed as `run.corrupt`. A later `StartRun` creates a fresh generation. The runtime never mutates, repairs, converts, or carries approval from an obsolete document.

Deterministic unit/integration tests are not native human-approval or installed-host qualification receipts.
