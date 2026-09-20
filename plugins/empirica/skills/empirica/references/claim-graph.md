# Claim graph

Read this file immediately before seeding or replacing the selected graph.

## Canonical v2 shape

The graph is exactly `root`, `claims`, and `edges`:

```json
{
  "root": "G0",
  "claims": [
    {
      "id": "G0",
      "text": "The proposed change satisfies the goal and invariants.",
      "gating": true,
      "kind": "ordinary"
    },
    {
      "id": "G1",
      "text": "The uncertain mechanism works against the real boundary.",
      "gating": true,
      "kind": "needs-experiment"
    }
  ],
  "edges": [
    {"from": "G0", "to": "G1", "type": "SupportedBy"}
  ]
}
```

Each claim has exactly:

- `id`: unique string;
- `text`: falsifiable statement whose digest binds its evidence;
- `gating`: whether it blocks the active scope;
- `kind`: `ordinary`, `needs-experiment`, or `needs-decision`.

Each edge has exactly `from`, `to`, and `type`. The only supported type is
`SupportedBy`, directed from a claim to a required supporting claim. Both endpoints
must exist and differ. Edges must be unique and acyclic, and every claim must be
reachable from the declared root by following `SupportedBy` edges.

Within the effective scope, support is conjunctive: a claim can be approved only
when its own evidence requirements and every direct in-scope supporting child are
approved. Before freeze, effective scope is the claims marked `gating`; after
freeze, it is the immutable frozen claim IDs. A deferred child does not silently
expand a frozen commitment, but adding its node or edge still changes the argument
and makes prior audit coverage stale.

A failed support prevents parent approval but does not refute the parent. The
parent remains open unless its own evidence makes it blocked or discarded. A
discarded branch stops dependency evaluation only on that path; shared descendants
remain active through other live paths. Blocking output identifies the unresolved
supporting claim rather than claiming that an already evidenced parent lacks local
evidence.

Do not send the legacy `nodes`/confidence representation as the v2 graph.
Confidence and terminal state are derived projections, never graph input.

## Construction rules

1. Make the root the goal-level assurance claim.
2. Add one gating claim for each material unknown or invariant.
3. Attach every claim to the root through `SupportedBy`; detached claims are invalid.
4. Use `needs-experiment` only when a deterministic command can falsify the claim.
5. Use `needs-decision` only for an irreducible human choice.
6. Keep claims stable enough for evidence binding. Rewording intentionally makes
   old evidence and audit coverage stale.
7. Replace a refuted branch with narrower claims only when evidence requires it.

## Author action

Submit the canonical graph only through the active host's public author-action
surface as the `payload` of `{"kind": "graph"}`. The service requires at least
one valid claim. Trusted host actions are not part of graph submission.
