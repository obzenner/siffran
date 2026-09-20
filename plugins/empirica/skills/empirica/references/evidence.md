# Research and deterministic spikes

Read this file before recording research or requesting an experiment.

## Fold 1: research

Every gating claim needs current research before approval. Research is a concrete
observation from `docs`, `code`, `runtime`, or `web` and must include enough
provenance to retrieve and check the source.

Public author action:

```json
{
  "kind": "research",
  "claim_id": "G1",
  "source_kind": "code",
  "result": "supports",
  "payload": {
    "source_ref": "plugins/example/core.py:40-63",
    "citation": "The boundary derives the value rather than accepting it."
  }
}
```

`payload.source_ref` and a non-empty verbatim `payload.citation` are mandatory.
`payload.observed_content_digest` may additionally bind the bytes that were observed when the host
can compute a SHA-256 digest.

Use `result: "refutes"` when the source contradicts the claim. A URL, path, or
quote is not automatically true; the independent auditor later checks relevance
and correctness. Rewording a claim changes its digest and invalidates prior
binding.

Research outcomes:

- supporting current research satisfies Fold 1;
- any current refuting research refutes the claim;
- no current research leaves it open.

## Fold 2: spike

Only a `needs-experiment` claim requires Fold 2. Supporting Fold-1 research must
already exist. Submit a `spike_request` with the exact deterministic command and a
non-empty list of repo-relative dependent files:

```json
{
  "kind": "spike_request",
  "claim_id": "G1",
  "command": "make focused-check",
  "dependent_files": ["plugins/example/core.py"]
}
```

The service—not the author—captures a coherent workspace tree, seals the request,
runs the harness exactly once against captured bytes, records file hashes, and
derives pass/fail solely from the subprocess exit code. Do not submit an
`evidence_leaf` or a claimed gate.

A passing exit code satisfies Fold 2 only while every bound file remains current.
A changed binding makes the spike stale. Request the same canonical
`spike_request` path again; there is no separate re-gate operation.

## Choosing a falsifier

Prefer the cheapest command that would fail if the claim were false:

- a focused test for behavior;
- schema validation for wire compatibility;
- a compile/type check for static contracts;
- a deterministic benchmark only when the claim is quantitative;
- a small boundary probe against the real adapter rather than a duplicate model.

Do not use a test that merely restates the implementation. Predict the expected
failure or result before running the command, then compare it with the observed
exit code and output.

## Trusted boundary

`research` and `spike_request` are public author actions. The resulting immutable
spike facts, attribution, child events, and audit verdict are private host/service
ingress. Similar JSON shape does not grant admission.
