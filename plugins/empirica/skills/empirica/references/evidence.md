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
| an `http(s)` URL, or an absolute or `~` path outside the workspace | `retrievable` | the independent audit, only for a kind its host is observed to retrieve (Claude's auditor is not yet credited with the web: live row `webfetch_capture`, ADR-0069); otherwise a human, when you report, before the audit |
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
covers it). `confirm: human` means a human confirms it: call `report_convergence` (default intent) and the host asks them. **A record that needs a human is confirmed when you report, before the audit**: do not launch the audit first. While the human is about to be asked the core refuses the audit launch with `evidence.confirmation_required`, and an audit run before the answer would be staled by it and spend an audit spawn; when nobody can be asked the audit is not held.
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
spend the presentation (the re-ask rule is below, in the paragraph on Claude). The human reads the whole verbatim quote; nothing is truncated. `empirica_read GetRun` and `empirica_read GetArgument` show
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

In Claude the human is asked inside `report_convergence`, so a turn end never settles as an evidence wait: when the confirmation is the only thing left, an auto run's Stop blocks and tells you to call `report_convergence`, and a deliberative turn ends with a notice that tells the human to tell you to report (ADR-0068). If the host cannot record whether a human can be asked, or cannot show the batch, nobody was asked and the report says so (its waiting line reads "the host could not … so nobody was asked"); `report_convergence` is not offered now and the first `next:` is `report_convergence intent=stop`. Stop honestly, or call `report_convergence` again after a change, because the next observation may clear the failure. A limit or refusal the run already knows is said instead ("the human interaction limit is reached", "already shown", "no human can be asked"), with the same first `next:`. **Later, cancel and timeout grant nothing and are not final:** the view says "already shown" and nothing asks again by itself (neither a Stop nor an auto continuation presents), but an explicit `report_convergence` shows the pending items again, each showing spending one of the run's human interactions until the limit says "the human interaction limit is reached". Report again when the human asks you to, or when one more question is worth an interaction; do not loop on it. If Empirica cannot be reached in that call, the tool errors with no `next:`; call `report_convergence intent=stop`. The full rule is ADR-0067.

`payload.observed_content_digest` may additionally bind the bytes that were observed when the host
can compute a SHA-256 digest.

Use `result: "refutes"` when the source contradicts the claim. A URL, path, or
quote is not automatically true; the independent auditor later checks relevance
and correctness, and retrieves every source its host is observed to reach. Rewording a claim changes its digest and invalidates prior
binding.

Research outcomes:

- supporting current research satisfies Fold 1;
- any current refuting research refutes the claim;
- no current research leaves it open.

## Fold 2: generic spike

Only a `needs-experiment` claim requires Fold 2, and supporting Fold-1 research must already exist.
Read the RunView’s immutable `spike_backend` first:

- `spikes_unavailable`: do not submit an unchanged request. Continue non-spike research, refine the
  graph, or stop with an honest residual. Never fabricate evidence or use an ambient host fallback.
- `qualified` / `docker-isolated-v1` or `docker-networked-v1`: this run is bound to exact
  qualification-record and execution-binding digests. Bound does not mean currently healthy. The
  host selected the profile once at `StartRun`; the plan cannot select or change it.

Submit exactly `{"kind":"spike_request","claim_id":...,"plan":...}`. The `plan` must be one closed
`empirica.spike-plan-draft/2` with exactly five root fields:

1. `schema`: exactly `empirica.spike-plan-draft/2`.
2. `image`: one syntactically valid already-local Docker name/tag, repository digest, full image ID,
   or unambiguous ID prefix. The host resolves locality and immutable native-platform identity; no
   image inventory, pull, build, or install is available to the author.
3. `argv`: one nonempty exact executable/argument vector. A shell is used only when explicitly named.
4. `inputs`: zero or more canonical non-overlapping repository-relative file/directory declarations.
   The host expands them once into a sorted bounded captured-file union, excluding symlinks and `.git`.
5. `environment`: at most 128 bounded name/value overrides. `HOME` and `TMPDIR` are framework-owned.
   Values persist verbatim and must never contain credentials or secrets; content-addressed plan
   identity can confirm guessed low-entropy values although public views expose names only.

Do not add authority, profile, resources, timeout, user, workdir, network, topology, services,
aliases, Docker options, host paths, credentials, image acquisition, or derived capture facts.
Consult the public tool schema for exact bounds rather than inventing a field.

The service captures all declared files in one workspace basis, seals immutable input/plan/policy
identities, and commits the request before effects. The harness runs at most once within that
application call. A result-CAS retry never reruns it, but a crash or separate call may execute the
sealed request again, so the workload must be deterministic, bounded, safe under duplicate
execution, and must not depend on unqualified ambient services. `docker-isolated-v1` denies outbound
network egress; `docker-networked-v1` expressly permits outbound Internet egress from the one verdict
container on its host-created private bridge.

The host owns one verdict container and every sandbox fact. Any tool works as argv, including
toolchain commands whose words resemble Docker options (`ps --pid 1`, `make build`). The complete,
drift-checked limitation set (what you choose, what the host fixes, what is never available, the
verdict rule, and each refusal `reason` with a one-line hint) is public-contract clause
`evidence/spike/limits`; read it with `GetContract` section `evidence/spike`.

The exact production workload limit is **120 seconds** inside the backend's **210-second** operation.
Pi's **300-second** and Claude MCP's **360-second** limits are outer transport margins.

Only the verdict container's inspected **natural exit** produces a verdict: zero is uppercase
`PASS`; nonzero is uppercase `FAIL` except 137, which is unavailable. Timeout, forced termination,
stdout/stderr overflow, OOM, capture/staging failure, image/platform/create/attach/inspect ambiguity,
identity mismatch, cleanup/recovery failure, or postflight/authority failure is **unavailable** and
admits **no spike result evidence**. The Fault carries one closed `reason` whenever the cause is
attributable (for example `image-not-local` or `lifecycle-unavailable`): act on its hint, which may
be to narrow or redesign, continue research, or arrange operator requalification for a future run.
A Fault without a reason is an internal host failure: retry once, then ask the operator. Never
infer FAIL or use a host fallback.

A passing result satisfies Fold 2 only while every bound file remains current. Re-submit the same
closed plan through `spike_request` only when a changed binding requires re-gating and this run
remains qualified; there is no separate re-gate action.

## Choosing a falsifier

Prefer the cheapest plan whose verdict container would fail if the claim were false:

- a focused test for behavior;
- schema validation for wire compatibility;
- a compile/type check for static contracts;
- a deterministic benchmark only when the claim is quantitative;
- a small boundary probe against the real adapter rather than a duplicate model.

Do not use a plan that merely restates the implementation. Predict the verdict before requesting
it, then compare that prediction with the admitted typed verdict and content-addressed stream
identities. Raw output is not evidence and is never projected.

## Trusted boundary

`research` and `spike_request` are public author actions. The resulting immutable
spike facts, attribution, child events, and audit verdict are private host/service
ingress. Similar JSON shape does not grant admission.
