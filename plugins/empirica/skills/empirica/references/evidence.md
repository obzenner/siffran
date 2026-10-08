# Research and deterministic spikes

Read this file before recording research or requesting an experiment.

## Fold 1: research

Every gating claim needs current research before approval, except a `needs-decision` claim, which is human-owned and needs only the human's answer. Research is a concrete
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
Cite one source per record. The form of `source_ref` decides how the record is verified
(ADR-0067); the service derives the class, you never state it:

| `source_ref` | class | verified by |
|---|---|---|
| a repo-relative file, optionally with lines (`path`, `path:40`, `path:40-63`, `path:40-63,70-72`), any `source_kind` | `verbatim` | the service, when the record is submitted: `citation` must appear there verbatim (whitespace runs compare as one space; join separate excerpts with `…`), else `research.citation_unverified` and nothing is recorded |
| an `http(s)` URL, or an absolute or `~` path outside the workspace | `retrievable` | the independent audit, when its host is observed to retrieve that kind; otherwise a human, at the end of the pass |
| a `runtime` observation with neither of the above | `observed` | a human, always |

A `code`, `docs` or `web` ref that is none of these forms is refused (`problem: source_ref_form`),
and so is an absolute path that names a file inside the workspace (cite the repository-relative
path) or whose spelling could hide where it points (`.` or `..` segments, `//`, a trailing slash).
The check is lexical only; whether a symlink points elsewhere is for the audit to see. A `runtime` record must say why no deterministic spike can establish the observation:

```json
{"kind": "research", "claim_id": "G2", "source_kind": "runtime", "result": "supports",
 "payload": {"source_ref": "nightly export log, 2026-09-27",
             "citation": "tile reads: 0 outside the index",
             "spike_unavailable": "The job only runs on the nightly cluster."}}
```

Without `spike_unavailable` the record is refused (`problem: spike_reason_missing`). Prefer a spike
whenever one is possible; the reason is shown to the human verbatim.

### What still needs confirming

The requirement is visible the moment the record exists: the claim's obligation row and the dossier
(`verification: {class, status, retrieve}`) say where its confirmation goes. `confirm: audit` means the
host's auditor retrieves the source (research awaiting audit still approves the claim; the audit gate
covers it). `confirm: human` means a human confirms it: call `report_convergence` (default intent) and the host asks them.
`confirm: unobserved` means a human can be asked but the host has not yet looked for a dialog (Claude looks
inside `report_convergence`): call `report_convergence` with the default intent, not `intent=stop`, and the
human is asked in that call if the host has a dialog. `confirm: unavailable` means nobody can be asked (no
human ingress, a delegated or non-interactive run, or a host that looked and found no dialog): the requirement
stays open, and the run can only stop as a residual.

Within one claim the blockers lead in a fixed order: the terminal states first; then what you can act
on now, missing or stale research and then a missing or stale spike (`claim.spike_missing`,
`claim.spike_stale`); last, waiting on a human (`evidence.confirmation_required`). A human batch is still
projected and still shown at the end of your pass whatever leads, so a claim that needs a spike and
also has a URL record waits on the spike first and asks the human in the same pass.

Nothing is asked of the human while you work. At the end of the pass, when you call
`report_convergence`, a pending human confirmation blocks with `evidence.confirmation_required`
(before the pass is charged). The host then shows the core's batch once, one decision per item. The
batch is capped (at most 64 items, decisions first) and owned by the service, never by the host; what a
presentation shows is exactly what it records as shown, so an item that was not on screen is never marked
shown. The choices are **Confirm**, **Reject** (with an optional note to you) or **Later**. There is no "approve all", a
decided item is never asked again, and **Later**, a cancelled dialog and a timeout leave it pending and
spend the presentation: it is shown again only after the derivation changes (new or reworded work, a new
record). The human reads the whole verbatim quote; nothing is truncated. `get_run` and `get_argument` show
you what waits (source and claim), whether a human is asked at your report, each citation's verification status, and
the human's note or answer.
A confirmed record counts; a rejected record is inactive and does not refute the claim, so find other
evidence. A claim whose only support is research awaiting a human is not approved, and convergence
waits for every pending human confirmation. Confirmations change the evidence digest, so an earlier
audit is stale.

A `needs-decision` claim is the exception to "research first": it needs no research, only the human's
answer. The human approves it (it is then approved and human-owned) or rejects it (it is discarded);
decisions are asked in the same batch as research records. Do not record research to stand in for the
decision.

In Claude the human is asked inside `report_convergence`, so on its first block the Stop never pauses for a confirmation: when it is the only thing left, the Stop blocks and tells you to call `report_convergence`; a Stop that continues after that block follows ADR-0068. If the host cannot record whether a human can be asked, or cannot show the batch, nobody was asked and the report says so (its waiting line reads "the host could not … so nobody was asked"); `report_convergence` is not offered now and the first `next:` is `report_convergence intent=stop`. Stop honestly, or call `report_convergence` again after a change, because the next observation may clear the failure. A limit or refusal the run already knows is said instead ("the human interaction limit is reached", "already shown", "no human can be asked"), with the same first `next:`. If Empirica cannot be reached in that call, the tool errors with no `next:`; call `report_convergence intent=stop`. The full rule is ADR-0067.

`payload.observed_content_digest` may additionally bind the bytes that were observed when the host
can compute a SHA-256 digest.

Use `result: "refutes"` when the source contradicts the claim. A URL, path, or
quote is not automatically true; the independent auditor later checks relevance
and correctness, and retrieves every source its host can reach. Rewording a claim changes its digest and invalidates prior
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
  "command": "python3 -m json.tool contracts/empirica/v2/public-contract.json",
  "dependent_files": ["contracts/empirica/v2/public-contract.json"]
}
```

The service—not the author—captures a coherent workspace tree, seals the request,
runs the harness exactly once against captured bytes, records file hashes, and
derives pass/fail solely from the subprocess exit code. A spike's file bindings must include every
file its command reads, because the harness sandbox contains only the bound files. Do not submit an
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
