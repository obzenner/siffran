# Empirica 1.3.0 Pi dogfood report

Branch under observation: `feat/obligations-contract` (PR #25)

Stance adopted before starting: “parametric knowledge = hypothesis only; every load-bearing claim discharged against evidence or surfaced as UNVERIFIED.”

## Running findings

### P-1 — Resolved invocation was not injected into model context

- **Expected:** The skill presents the resolved invocation at `SKILL.md` line 91 (`The user invoked: $ARGUMENTS`) so Step 1 can route from the actual goal and mode flags. ADR-0039 lines 19 and 36 require an actionable, lossless agent boundary that does not force the actor to infer needed work from prose, counts, or history.
- **Observed:** The active-run `empirica_status` output was verbatim: `Empirica run eyJnIjoxLCJwIjoiYmU2ZDM1YmMxMWYxYTljNiIsInIiOiIxOTIwYjRjOS03ZjA4LTRjZjUtOWVkNi1mZGRhODNjZjhjYTkifQ==: no contract yet (no graph).` The resolved goal and `multi_provider=true` mode were absent from model context and status. I had to stop and ask; the user then supplied: `The goal is: design a retry policy for the bridge transport. Modes: multi_provider=true.`
- **Result:** FAIL — the pre-graph boundary provided the allowed no-contract literal but did not provide the resolved `$ARGUMENTS` needed for routing, requiring out-of-band reinjection by the user.
- **Fix request (not applied):** Inject the adapter-resolved goal and modes into the model-visible run-start/restore boundary, or return them from `empirica_status`, without weakening the canonical `run.contract` interface.

### Initial status boundary

- **Expected:** On Pi, `empirica_status` exposes the opaque run handle and current `run.contract`, or the literal `no contract yet (no graph)` (SKILL.md lines 113–118; ADR-0040 lines 21 and 34).
- **Observed:** Before invocation, the required first call returned verbatim: `No active Empirica run.` Immediately after the user confirmed the goal was set, the first active-run call returned verbatim: `Empirica run eyJnIjoxLCJwIjoiYmU2ZDM1YmMxMWYxYTljNiIsInIiOiIxOTIwYjRjOS03ZjA4LTRjZjUtOWVkNi1mZGRhODNjZjhjYTkifQ==: no contract yet (no graph).`
- **Result:** PASS — the active boundary supplied both the opaque run handle and the specified no-graph literal without requiring inference.

### Early convergence-block visibility probe

- **Expected:** Invoking `report_convergence` early in an active run is blocked, and the blocked-tool reason is visible in model context; once a graph exists, the response should also carry the canonical `run.contract` rather than requiring inference from counts or prose (SKILL.md lines 113–118; ADR-0039 lines 19, 23, 36; ADR-0040 line 36).
- **Observed:** Before invocation, the probe returned `No active Empirica run.` The required active-run probe, performed before graph/research/spike work, returned verbatim: `active run but the claim graph is missing; refusing to stop (fail closed)`.
- **Result:** PASS for ADR-0040 blocked-reason visibility: the blocked reason was directly visible in model context. Contract rendering at a graph-backed Block remains UNVERIFIED until the graph is seeded.

### P-2 — Confidence-only graph update retired every unchanged obligation

- **Expected:** A changed claim is retired/replaced only when its text, kind, or hold changes; unchanged claims are preserved across graph revisions (ADR-0039 lines 38–40 and Confirmation T4 at line 52). Updating evidence-derived confidence alone must not erase obligations.
- **Observed:** After all Fold-1/Fold-2 witnesses were observed as passing, the assessor rewrote the same graph with only each confidence changed from `0` to `0.9`. The returned contract was verbatim headed `Obligation contract empirica/eyJnIjoxLCJwIjoiYmU2ZDM1YmMxMWYxYTljNiIsInIiOiIxOTIwYjRjOS03ZjA4LTRjZjUtOWVkNi1mZGRhODNjZjhjYTkifQ==@2` and listed every `empirica/G0` through `empirica/G4` as `[retired@2] graph write — authority: 2c353a5b06c745ee5c18f791d932947446ca79d1b3ee12bf3030da2f9861163d`; its verdict had `satisfied=-; ... residual=-; unwitnessed=-`. `empirica_status` returned the same all-retired contract.
- **Result:** FAIL — an evidence-derived confidence update caused unchanged obligations to disappear instead of being preserved.
- **Fix request (not applied):** Diff durable claim obligations only on contract-significant fields (text, kind, hold/scope), preserving obligation ids and witness observations across confidence-only graph writes.

### P-3 — Graph-backed Block omitted `run.contract` from the model-visible tool error

- **Expected:** `RestoreRun`, `Block`, and terminal `Allow` expose the canonical `run.contract`; string-only channels render that same view so the actor never infers outstanding work from a reason string (SKILL.md lines 115–118 and 372–374; ADR-0039 lines 23 and 36; ADR-0040 lines 21 and 34).
- **Observed:** With graph claims approved but audit absent, the original `report_convergence` returned only this verbatim reason: `Claim graph is converged, but the run may not report converged: no independent audit was performed: spawn the independent auditor to verify this run before converging (ADR-20 P6 — the author cannot grade its own convergence). The auditor must re-read each approved claim's Fold-1 citation, confirm the source supports the claim, and write a verdict carrying the nonce from its spawn (ADR-20 P6, ADR-25).` No rendered contract, handle, obligation must, witness, hold, or provenance appeared. After the user stated Empirica was fixed mid-run, the same tool boundary did render the full contract and handle, including the open `empirica/audit/0ebc2725b2d27b03f154978aaa02da4f574001cc6cb038515d04657702d6065f` obligation and its missing judgment witness.
- **Result:** FAIL for the original code under test; post-fix retest PASS.
- **Fix request:** Preserve the post-fix behavior: when the Pi tool gate denies `report_convergence`, include `renderText(result.run.contract)` and the opaque handle in the model-visible error.

### P-4 — Subagent interception blocked discovery before ticketing or audit spawn

- **Expected:** Pi intercepts the configured `subagent` tool, issues an audit ticket, enforces the spawn budget, and permits the scoped `empirica:empirica-auditor` while budget remains (SKILL.md lines 113 and 438–456; ADR-0040 lines 22 and 34). The subagent runtime independently requires `action: "list"` before execution so only executable/non-disabled agents are launched.
- **Observed:** Initially, the mandatory discovery call `subagent({action: "list"})` was intercepted and returned verbatim: `empirica spawn gate unavailable (failing closed)`. No agent registry, audit nonce, ticket, or obligation contract reached model context, so safely launching the auditor was impossible. After the user stated `empirica was fixed`, the same management call succeeded and returned the executable registry; a distinct-provider `bedrock--claude-opus-5` auditor was then launched.
- **Result:** FAIL for the code originally under test; PASS only after an external mid-run fix changed the measured runtime. The original past-budget denial remains UNVERIFIED.
- **Fix request:** Intercept only executable `subagent` invocations; do not ticket management/control actions such as `list`. For executable audit calls, send an actor shape accepted by the core, return the issued nonce to the audit task, reserve/enforce one spawn, and render the returned contract on denial.

### P-6 — Audit tickets were issued before downstream spawn acceptance

- **Expected:** A spawn ticket witnesses a requested/accepted auditor spawn and supplies the nonce bound to that spawn (SKILL.md lines 438–458; ADR-0040 line 34).
- **Observed:** The first post-fix execution attempt was rejected by pi-subagents before launching a child with `Structured single-child execution cannot be combined with workflowScript.`, yet the user received ticket nonce `917db2e6a7245932c747619e6bd74f28`. The subsequent accepted distinct-provider spawn produced a second nonce, `e5ac38f5292c78e98adb1bca346dc683`, only after its task had already been dispatched carrying the first nonce.
- **Result:** FAIL — pre-tool interception ticketed an invocation that the downstream tool rejected, and the nonce corresponding to the actual spawn arrived too late to be included in the child task. Ticket issuance witnesses a tool-call attempt, not a successful spawn, and nonce delivery is racy.
- **Fix request:** Finalize the ticket only after downstream spawn acceptance, or inject a provisional nonce into the child call and cancel it deterministically on downstream rejection; ensure the actual child receives the nonce bound to its own spawn.

### P-7 — Settled reminders accumulated into one duplicated message

- **Expected:** A settled nudge is a best-effort reminder that starts a follow-up turn once; it should preserve the contract without flooding or concatenating duplicate reminders (SKILL.md line 113; ADR-0040 lines 28, 36, and 50).
- **Observed:** One user-visible turn contained four back-to-back copies of the complete reminder and contract, with boundaries concatenated as `held=-empirica (reminder, not a gate)`.
- **Result:** FAIL — follow-up delivery worked, but duplicate queued nudges accumulated and were concatenated without separators.
- **Fix request:** Coalesce pending reminders by run/revision, suppress a reminder when an equivalent one is already queued, and preserve a delimiter if multiple messages must be delivered.

### Settled-nudge delivery

- **Expected:** Ending a turn with open obligations causes a settled nudge to reach the model and reliably start another turn (SKILL.md line 113; ADR-0040 lines 28 and 36).
- **Observed:** The next turn started with this verbatim nudge:

  > empirica (reminder, not a gate): this run has outstanding work before it can report convergence — Claim graph is converged, but the run may not report converged: no independent audit was performed: spawn the independent auditor to verify this run before converging (ADR-20 P6 — the author cannot grade its own convergence). The auditor must re-read each approved claim's Fold-1 citation, confirm the source supports the claim, and write a verdict carrying the nonce from its spawn (ADR-20 P6, ADR-25).. Continue, or call the report_convergence tool once the evidence is in.
  >
  > Obligation contract empirica/eyJnIjoxLCJwIjoiYmU2ZDM1YmMxMWYxYTljNiIsInIiOiIxOTIwYjRjOS03ZjA4LTRjZjUtOWVkNi1mZGRhODNjZjhjYTkifQ==@2
  > empirica/G0 [retired@2] graph write — authority: 2c353a5b06c745ee5c18f791d932947446ca79d1b3ee12bf3030da2f9861163d
  > empirica/G1 [retired@2] graph write — authority: 2c353a5b06c745ee5c18f791d932947446ca79d1b3ee12bf3030da2f9861163d
  > empirica/G2 [retired@2] graph write — authority: 2c353a5b06c745ee5c18f791d932947446ca79d1b3ee12bf3030da2f9861163d
  > empirica/G3 [retired@2] graph write — authority: 2c353a5b06c745ee5c18f791d932947446ca79d1b3ee12bf3030da2f9861163d
  > empirica/G4 [retired@2] graph write — authority: 2c353a5b06c745ee5c18f791d932947446ca79d1b3ee12bf3030da2f9861163d
  > Verdict: satisfied=-; holds=-; violated=-; residual=-; unwitnessed=-; held=-

- **Result:** PASS for follow-up delivery reliably starting another turn and for the nudge carrying a rendered contract. The contract content itself remains FAIL under P-2 because all unchanged obligations were retired. A second settled turn delivered the same reminder and contract verbatim after the request to run `/compact`; no compaction summary was present in model context, so this second message is additional nudge evidence, not compaction evidence.

### P-5 — `make check` stayed green across the live P-2/P-3/P-4 failures

- **Expected:** ADR-0039 line 52 says `make check` confirms graph-write preservation (T4), Block rendering, and contract round trips; ADR-0040 lines 52–54 name Pi gate/lifecycle/registration tests as confirmation of the live adapter behavior.
- **Observed:** `make check` completed with `All checks passed.` It included 139/139 application checks, 67/67 Pi tests (twice through bundle/package validation), the new retry-policy spike, and ADR health. Its Pi output explicitly said `all Block renderers retain contract text` and `subagent is allowed, budget Block denies, and transport failure closes`, despite this live run observing P-3’s contract-less Block and P-4’s denial of the mandatory non-spawn `subagent list` call.
- **Result:** FAIL — the deterministic confirmation set does not exercise the real model-callable error boundary or actual pi-subagents management/execution payload shapes, and it did not reject P-2’s all-retired terminal-facing contract.
- **Fix request (not applied):** Add live-shaped fixtures for `report_convergence.execute` after the `tool_call` denial, management versus execution subagent payloads, core-valid audit actor records/nonces, and confidence-only graph transitions through terminal handoff.

### P-8 — Auditor can return a verdict but cannot submit the required verdict artifact

- **Expected:** The independent auditor receives the opaque handle and issued nonce, computes per-claim and argument digests from the canonical evidence functions, and submits `audit_verdict` through `BridgeTransport`; convergence then observes that verdict (SKILL.md lines 438–474; ADR-0040 lines 22 and 34).
- **Observed:** The distinct-provider fallback re-audit independently re-read G0–G4’s citations, ran `make bridge-retry-spike` three times, and returned `Verdict: PASS.` However its model-callable `empirica_knowledge` surface allowed only `attribution`, `evidence_leaf`, `freeze`, `graph`, and `route`; it had no `audit_verdict` kind and `empirica_status` reported no active run in the child session. The parent is forbidden to author its own verdict and the auditor was told not to invent digests. The next `report_convergence` therefore returned: `an auditor was spawned but no readable verdict is present` and exposed an open audit judgment witness.
- **Result:** FAIL — ticketing and independent review can occur, but Pi provides no end-to-end auditor-to-verdict submission path, so the mandatory audit obligation cannot be discharged honestly.
- **Fix request:** Provide the ticketed auditor a scoped verdict submission tool carrying the active run handle and its own nonce, backed by the canonical digest functions; do not require the parent author to translate or author the auditor’s verdict.

### P-9 — Expected spawn-budget denial was not demonstrated

- **Expected:** The intercepted Pi subagent execution reserves against ADR-17’s run spawn budget and denies the first spawn past the cap (SKILL.md lines 306–337; ADR-0040 lines 22 and 34).
- **Observed:** The run accepted a primary auditor spawn, a fallback spawn, and a second fallback re-audit spawn. Pi-subagents reported its separate fan-out budget as `64`, but no Empirica denial or model-visible Empirica spawn count/cap was returned. The invalid pre-spawn workflow call also minted a ticket without launching.
- **Result:** UNVERIFIED for the exact past-cap boundary — the active cap was not visible in the obligation/status boundary, and deliberately buying further model calls solely to search for it would be wasteful. The accepted repeated spawns plus P-6 remain risk evidence, not proof that the configured cap was exceeded.
- **Fix request:** Expose the current run spawn obligation/cap through `run.contract` or status, atomically reserve on accepted execution, and add a zero-inference deterministic way to probe the next reservation.

### Compaction preservation

- **Expected:** The compaction summary contains the same canonical `run.contract`, including obligation musts, witnesses, observations, holds, and provenance (SKILL.md lines 115–118 and 372–374; ADR-0039 lines 23 and 36; ADR-0040 lines 34 and 43).
- **Observed:** `/compact` has not occurred during an active run.
- **Result:** UNVERIFIED.

### Terminal handoff

- **Expected:** Terminal Allow exposes the canonical `run.contract` and names `run.contract_artifact_id` (SKILL.md lines 115–118; ADR-0039 lines 42 and 46).
- **Observed:** The run has not started or reached a terminal decision.
- **Result:** UNVERIFIED.

## Pending live sequence

1. Receive `/empirica --multi-provider <goal>`.
2. Call `empirica_status` first and quote the handle plus contract/no-graph literal.
3. Call `report_convergence` before graph or evidence work and quote its result verbatim.
4. Route, seed graph, perform Fold-1 research, then deterministic Fold-2 spike through `empirica_knowledge`.
5. Request the scoped `empirica:empirica-auditor` through the intercepted `subagent` tool; test one spawn beyond the budget and record the denial.
6. Record any settled nudge verbatim.
7. After `/compact`, inspect whether the supplied summary contains the obligation contract.
8. Quote terminal `run.contract` and `run.contract_artifact_id`.

No application or `plugins/empirica/adapters/pi/**` files were edited.
