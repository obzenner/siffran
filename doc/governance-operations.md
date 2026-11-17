# Governance: operator-side facts

Facts about run configuration and human confirmation that an operator sets up or audits. The agent cannot act on them, so
`plugins/empirica/skills/empirica/references/governance.md` leaves them out; the agent-facing rules live there.

## The core-rendered document

Every configuration review and every confirmation batch first writes a core-rendered document (`empirica.hitl/1`) beside
the run's record, with mode `0400`, before any form is shown. Each dialog names it by its short digest and absolute path,
and the decision must carry the document's digest. A receipt binds that digest, the presentation it answers and each
item's record or claim wording. Presentations are reserved with a compare-and-swap before the UI is shown, and the exact
epoch and digest checks apply across the review and the locked confirmation of an amendment. Receipts are fingerprinted
raw before they are accepted.

## Decision timeout

The host decision timeout defaults to 900 seconds. `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` accepts host-owned overrides
written as whole seconds in plain decimal digits from 1 to 1500 (no fractions, signs, exponents, or hex); a set but
malformed or out-of-range value fails extension or server start with an error naming the variable instead of defaulting.
Pi shares one timeout across the review and the locked confirmation.

## Delegated auto

Delegated auto needs the operator-recorded `EMPIRICA_AUTO_DELEGATION=1`. StartRun budgets and `EMPIRICA_MAX_*` may narrow
the fixed 8/1/2 envelope but never enlarge it.

## Reviewer configuration

- **Claude** requires client-advertised MCP form elicitation. If `CLAUDE_CODE_SUBAGENT_MODEL` is explicitly set (other
  than `inherit`), the host resolves it. Otherwise the adapter supplies only a resolvable alias from a family different
  from the observed main family. Third-party providers require the corresponding `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` pin.
- **Pi** requires `ctx.hasUI` for approval. Package-scope and reviewer-model derivation are described in the
  [Pi adapter guide](../plugins/empirica/adapters/pi/README.md#governed-initialization).
- Only exact documented model and version mappings establish identity equivalence across providers; moving or private
  aliases remain unknown. Reviewer configuration is not itself evidence of independence.

## Persisted state

A persisted document that is not exactly the current wire `empirica/v2` and state family `empirica.run/4`, or that the
strict current schema rejects, fails closed as `run.corrupt`. A later `StartRun` creates a fresh generation. The runtime
never mutates, repairs or carries approval from such a document.

## Qualification

Deterministic unit and integration tests are not native human-approval or installed-host qualification evidence; see
`make native-qualification`.
