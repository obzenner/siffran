# Empirica 2.0 D2B spec — GetContract digest identity

**Status:** Parent-frozen small corrective amendment.

## Goal

Every successful GetContract target (`index|section|full`) carries the canonical PublicContract digest
in its `contract_result`, so on-demand discovery can be correlated with RunView contract identity.
This is an existing D1 identity requirement, not a new command/action/reason/section/tier.

## Required changes

Allowed only:

- `doc/design/empirica-2.0-d1-contract.md`
- `contracts/empirica/v2/response.schema.json`
- `contracts/empirica/v2/fixtures/getcontract-*.json` only if directives need an explicit assertion
- `scripts/validate_contracts.py`

For each closed contract result branch require:

```json
{"target": "index|section|full", "digest": "sha256:<64 lowercase hex>", "...": "target payload"}
```

The digest equals the canonical digest of `public-contract.json`, computed by the existing
`registry_digest` algorithm. `materialize_contract_result` supplies it. Fixture correlation and
validator checks require exact equality. It is not embedded inside PublicContract itself.

Raw schema rejects missing/malformed digest and sibling-target payloads as before. Add one-mutation
negative cases for each target missing digest and one wrong-but-well-formed digest. No duplicated
digest constant in fixtures or another registry.

Clarify D1 GetContract identity wording. No runtime/D3/D4/Make changes.

## Verification

- `make contract-check`
- `make check-static`
- raw-schema missing digest probes for index/section/full
- procedural wrong digest probe
- empty Git index

No staging, commit, push, or spawn.
