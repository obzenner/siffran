# Governed initialization (public contract 2.1.0)

## Prepare without investigating

The default control mode is **deliberative**. Route, then propose a graph from supplied context;
unknown discovery scope can itself be a claim. A graph is `{root, claims, edges}`. Each claim has
exact `id`, `text`, `kind` (`ordinary`, `needs-experiment`, `needs-decision`), and boolean `gating`.
Edges are `{from, to, type: "SupportedBy"}`. The graph must be a root-connected DAG. Every claim,
not only gating claims, participates in approval. Ordering alone does not change consent.

Submit `configure_run` with proposed `budgets`, `modes`, `auditor: {provider_id, model_id}`, and
optional `allow_same_model`. Omitted fields retain their current proposal values. This action
requests the host dialog; it is NOT approval. Read `run.governance` for complete scope, proposed
configuration, effective budgets, consumed/remaining capacity, inventory, revision, approval kind,
and required next action. Only a current approved proposal permits `investigate`; record that
witness before native reads, searches, commands, evidence, or children.

Bootstrap inventory is host-populated, not an author assertion: an initial Claude `GetRun` may
have no inventory until a lifecycle/context refresh or `configure_run` reads the operator file.
Once populated, read `run.governance.context.inventory.members` and propose an auditor in
`configure_run` for a one-dialog approval. Otherwise choose in the first dialog: that choice
amends the null-auditor proposal, and a second `configure_run` must obtain fresh final consent.

The host dialog can approve, amend, reject, decline, or cancel. An amendment produces a new proposal
that needs a second approval (`configure_run` again, even with no changes). Cancel, timeout, missing
UI, unknown inventory, or conflicting/stale replies never grant authority. Public reads, route,
graph/configuration corrections, and explicit `report_convergence(intent="stop")` remain available
while pending. Do not loop on a declined proposal. The host reserves an interaction through CAS
**before** opening UI: at most 3 per material proposal/revision and 128 total per run, including
pre-approval revisions. Cancel, decline, timeout, invalid UI content, and explicit rejection do
not restore capacity. A private `present` receipt becomes at most one exact-bound final receipt;
its immutable presentation fingerprint permits inert replay without re-displaying UI. A stale
reply cannot finalize the reservation, but the reservation stays charged across revision/restore.
Missing UI/capability or unusable inventory does not reserve an interaction. A crash after
reservation can consume capacity without displaying a dialog; safety takes precedence over
retrying an uncertain presentation. No receipt IDs or private history are exposed in RunView;
`interactions_remaining` and `prompt_error` disclose the safe aggregate limit and blocking reason.

Material graph, budget, mode, auditor, or host inventory/identity changes revoke approval even after
investigation witnesses exist. A new approval preserves consumed counters and evidence history;
it does not make stale evidence or audit current. Freeze requires approval and preserves it, but
committed frozen meaning remains immutable: changing it requires a fresh run.

## Explicit auto

`/empirica --auto <goal>` (Codex: `$empirica --auto <goal>`) selects immutable automatic host
acceptance, visibly `approval_kind: auto`, not human approval. It is independent of `--cli-exec`,
`--multi-provider`, and host permission modes. The same proposal/configure step and identity checks
apply. With a null auditor, the private host context transaction prepares a deterministic canonical
proposal: first known authorized member different from the known author, sorted by raw provider
then model ID; same-model only with positive singleton proof and the explicit immutable auto
policy. Approval then binds that new digest, never silently modifies a consented proposal. Unknown
members remain visible. No eligible known member produces `governance.auditor_required`; unknown
author/selection produces `governance.author_unknown` / `governance.auditor_unknown`. An existing
explicit selection is retained and must pass the same validation. Auto cannot raise any operational ceiling, including audit retries, and permits at most
8 material revisions after first approval. Deliberative runs permit at most 64. No author action
can raise those limits. Stop honestly or start a separately authorized fresh run at the limit.

## Host inventory and auditor selection

- **Pi:** `ctx.hasUI` plus documented select/input/confirm dialogs. The inventory is the configured,
  authenticated `ctx.modelRegistry.getAvailable()` result, not proof of live worldwide availability.
- **Claude:** MCP form elicitation must be advertised by the client. The server reads an operator-owned
  file named by `EMPIRICA_GOVERNANCE_CONFIG`; the form asks the operator to confirm completeness and
  authorization. `EMPIRICA_GOVERNANCE_CONFIG` must be inherited by the **Claude Code process and
  its MCP server** from the operator environment; setting it in an unrelated shell is insufficient.
  The author must not create or modify this file to obtain its own approval.
- **Codex:** deliberative approval is explicitly unavailable until a genuine human ingress is
  qualified. Auto also blocks for unmapped active slugs, including `gpt-5.1-codex`: the concrete
  OpenAI map currently recognizes only three dated snapshots, not real Codex family/moving slugs.
  Simulated snapshot conformance is not installed Codex support. Even with a recognized author
  and operator inventory, its managed process cannot attest the resolved auditor model, so this
  profile cannot claim convergence.

Operator configuration format (example only; provision outside the governed author session):

```json
{
  "version": 1,
  "inventory": {
    "members": [
      {"provider_id": "anthropic", "model_id": "claude-sonnet-4-6"},
      {"provider_id": "anthropic", "model_id": "claude-opus-4-6"}
    ],
    "source": "operator_declared",
    "complete": true,
    "authorized": true
  }
}
```

Include the author and every authorized alternative. Incomplete, invalid, or missing inventory is
**unknown**, never a fabricated singleton. Complete authorized inventory containing unmapped
members is **partial**: a known author and known different selected auditor can still be approved,
but no singleton exception can be inferred. Keep every raw member visible and digest-bound;
unknown author/selected identities block with their precise typed reason. Inventory is re-read before investigative
admission/audit launch; changes revoke consent. The host injects the approved auditor model and
canonical read-only role/dossier, never an author-supplied model override or plugin pin. Actual
observed author/auditor identities must match their approved concrete normalized identities.
A substitution or unobservable identity blocks and invalidates approval; it is not a fallback.

Only exact documented version/variant mappings establish identity across providers. Different
providers are not required and do not by themselves establish independence. A same-model exception
requires positive complete authorized singleton inventory, explicit approved `allow_same_model`,
unchanged inventory at launch, and both observed identities equal to the sole normalized member.
Claude has a dedicated boolean, default false, warning **SAME MODEL / LOWERED INDEPENDENCE**;
Pi asks a dedicated confirmation. Author-proposed true is not consent. A changed auditor or
exception choice creates a new proposal requiring a fresh final approval; refusal never approves.
Known alternatives or any unmapped member prohibit the singleton exception.
Its `operator_declared` or `pi_registry` basis stays visible. Unknown/zero/multiple inventory cannot
waive independence. This is host-mediated consent, not proof of a physical human or isolation from
the same OS principal who can modify plugin/configuration files.

## Limits and recovery

The proposal is bounded, not silently truncated: goal 4096 characters, 32 claims (2048 characters
per claim text), 128 edges, and 32 inventory members. IDs are at most 128 characters. Oversized
input is rejected; split the goal into independently governed runs rather than hiding scope.
Claude's `scope_json` amendment field admits **1,241,316 characters**: at most 12 escaped ASCII
characters per Unicode scalar (including astral surrogate pairs), covering root, 32 claim IDs/texts,
128 edge endpoints, and conservative JSON syntax. Quotes, backslashes and Unicode are not stripped;
all scope text remains exact UNTRUSTED data. This bounds complete standard JSON serialization,
not arbitrary whitespace padding. Private receipt history is bounded to 128 slots. Reservations
and their final outcomes share a slot, so the third authorized prompt can still complete at the
limit; explicit auto records non-human receipts without a presentation. Effective budgets remain
the sole work-accounting source; proposed ceilings do not install until approval.

The human decision deadline defaults to **900 seconds (15 minutes)**. Both hosts accept the
host-owned `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` override only for finite values in **1..1500**
seconds; invalid/out-of-range values fall back to 900. This is not a public tool argument. Pi
shares one total deadline across its selection/input/confirmation sequence, not one timeout per
dialog. MCP applies it to the elicitation request; ping remains responsive while waiting and only
cancellation naming the active tools/call ID aborts that dialog. Expiry records dismissal (not a
human rejection), ignores late replies, and never automatically re-prompts. The upper bound is
below the documented 30-minute Claude stdio idle bound.

Contract 2.1.0 keeps `empirica/v2` and `empirica.run/2` with required strict governance fields.
Pre-3.2.0 runs lack these fields and fail closed; they are not silently approved or migrated.
Start a fresh generation. Do not overwrite old state or upgrade an active installed session as
part of repository tests. Deterministic fake host tests are not native human approval receipts;
installed qualification requires separate operator-present authorization.

## Identity spelling sources

The deliberately limited concrete mapping follows Anthropic's
[model overview](https://platform.claude.com/docs/en/about-claude/models/overview) and exact
provider wire IDs; OpenAI entries use the dated snapshots documented for
[GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1),
[GPT-4.1 Mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), and
[GPT-4o](https://developers.openai.com/api/docs/models/gpt-4o). A recognized spelling is not proof
that a host/account can use it. Newly released or private aliases remain unknown until mapped
from source-backed concrete identity evidence. Exact configured wrapper providers
`amazon-bedrock-eu`, `amazon-bedrock-us`, and `amazon-bedrock-global` use the known Bedrock wire
convention; they do not make unknown underlying models known. AWS documents geographic
`us.`, `eu.`, `apac.` and `global.` cross-region inference profile prefixes in its
[cross-region inference documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html).
No invented provider-prefix wildcard is used. Unretained legacy Claude 4 IDs are not mapped;
Vertex/Foundry spellings, private models and moving aliases remain unverified. Claude labels
unrecognized model shapes with provider `unknown`, rather than guessing Anthropic provenance.
