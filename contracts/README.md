# Siffran contracts

These contracts are the implementation-independent boundary between plugin cores and host or
persistence adapters. Claude Code hooks, Pi events, filesystem paths, Git commands, process exit
codes, and UI details must not appear in a core request or response.

Contract versions are additive within a major version. A consumer validates the envelope before
dispatch and treats an unknown command or decision as `unsupported`, never as success.

## Protocols

- `empirica/v1` — run lifecycle and convergence decisions.
- `methodologist/v1` — methodology selection and phase progression.
- `obligations/v1` — immutable `require`/`forbid` obligations, exact witnesses, trusted observations, derived deterministic verdicts, and canonical agent-facing views. Witness refs follow `^[a-z][a-z0-9_-]*(/[A-Za-z0-9._:@-]+)+$`; the verifier's caller decides observation trust. Revisions are append-only, explicitly name their predecessor, and retain removed obligations as attributed retirement records. `contract.schema.json`, `observation.schema.json`, and `verdict.schema.json` define the wire values; the contract schema also exports `$defs/view` for host protocols.

`fixtures/` contains substrate-neutral API examples used by every adapter's conformance suite.
`obligations/v1/fixtures/` contains executable cross-language cases for verification F1–F16,
revision, canonical cold-start decoding, exact text rendering, and preservation/non-weakening.
