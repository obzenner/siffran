# Independent review — PR #25

Reviewed HEAD `3825949` against `origin/main` (`e9e84b3`). Read the PR body, ADRs 0039–0041, the brief/adjudication, GLM audit/re-audit and Pi dogfood report before tracing implementation. Production files were not edited. This report is the sole authored file.

## Findings

1. **BLOCKER — The purported secret audit nonce is public, predictable, and sufficient to forge convergence.** `plugins/empirica/application/service.py:753–759,1336–1342` deliberately derives it as truncated SHA-256 of public run-key fields and ordinal. The opaque handle is merely decodable encoding; `GetArgument` exposes ticket ordinals (`service.py:245–247`). More directly, `RestoreRun` returns every raw ticket, including nonce (`service.py:316`), and Claude/Codex compaction serializes that snapshot into model context (`adapters/claude/restore.py:64–65`; `adapters/codex/lifecycle.py:397–403`). The active-run `audit_verdict` operation performs no host/principal authentication (`service.py:429–432`), and coverage accepts a matching issued, non-void nonce (`core/audit.py:49–68`). **Executed reproduction:** predict the first nonce from the handle, build a verdict using public `GetArgument` digests, submit it without any child output, then evaluate: `converged=True`. Codex's own test performs essentially this attack: reads the nonce from RestoreRun, submits a verdict through the bridge, and asserts convergence (`adapters/codex/tests/test_codex_adapter.py:362–391`). Thus “Codex stays open” is not enforced. `KNOWLEDGE_ACTION_KINDS` really is closed (`adapters/pi/src/index.ts:32,165–168`), so the named Pi tool rejects `audit_verdict`; that does not secure a publicly callable local bridge against an author with ordinary execution tools. Nor is the same-user run store/transcript storage a principal boundary. Consumed tickets remain reusable by design (`service.py:804–808`); coverage excludes void, not consumed tickets.
   **Suggested fix:** Use an authenticated host-owned ingest capability and private, unpredictable ticket material, redact every public snapshot, and prevent author-side bridge/store access from masquerading as host observation; otherwise explicitly withdraw the mechanical-independence claim.

2. **MAJOR — Claude has no implementation of the promised author-result redaction.** `plugins/empirica/adapters/claude/lifecycle.py:229–257` reads the child's verdict and writes a diagnostic to stderr, but never transforms the Agent result returned to the author. The auditor is explicitly instructed to return the nonce-bearing fence (`adapters/claude/audit.py:23–24`). `SubagentStop` observation is not a parent-tool-result replacement, and the transcript fallback reads a nonce-bearing child transcript (`lifecycle.py:205–226`). Consequently a normal successful child answer can deliver its nonce to the author; “no diagnostic prints it” does not solve that channel. This host path was not live-tested in this review.
   **Suggested fix:** Prove a supported Claude interception channel that consumes and replaces the parent-visible child result, or mark private-verdict delivery unsupported on Claude rather than complete.

3. **MAJOR — Pi redaction is conditional on successful ingestion and volatile correlation.** `plugins/empirica/adapters/pi/src/index.ts:287–305` deletes the ticket mapping before awaiting ingestion and only redacts afterward. A bridge exception leaves the raw nonce-bearing result intact; I reproduced `PI_NONCE_VISIBLE_AFTER_INGEST_FAILURE=true`. Unknown `toolCallId` returns untouched; a reload loses `auditTickets` (`index.ts:244`), while `session_start` restores only the handle (`index.ts:124–130`). Malformed/mismatched fences and error results also bypass redaction (`index.ts:291–302`), and the advertised `event.result` fallback is read but not redacted (`index.ts:263–280`). Two distinct concurrent IDs are correctly separated while the Map survives, but each entry stores only a nonce, not its original run handle; completion uses the mutable current `runHandle`. The handler mutates its argument and returns nothing instead of returning the Pi result override; this works for current shared array references but not the advertised string/result shapes. The installed Pi runner uses handler return values to mark a result modified (`@earendil-works/pi-coding-agent/dist/core/extensions/runner.js:649–699`).
   **Suggested fix:** Redact before any await on every result path, return an explicit content/details override, and persist host-only `{toolCallId, runHandle, ticket}` correlation with reload/error recovery.

4. **MAJOR — Model independence is not checked against the actual dispatched model.** Pi's `actorModel()` returns a string agent name before consulting definition metadata (`plugins/empirica/adapters/pi/src/index.ts:433–442`), so the normal scoped auditor call records `model="empirica:empirica-auditor"`; the production handler reproduction confirmed that exact value. `isAuditorSpawn()` merely searches the entire user-supplied input for a substring (`index.ts:430–431`). The agent can run the same model under another agent alias and never compare equal. Claude never sends `StartRun.actor` at all (`adapters/claude/run_start.py:76–90`). `actors.same_actor` only case-folds exact model strings and treats every tier alias as incomparable (`application/actors.py:102–109`); `opus` versus its resolved concrete model is therefore permitted, not independently verified. The test explicitly expects unknown/tier auditor Allow (`tests/test_application.py:1827–1839`).
   **Suggested fix:** Obtain resolved author and child identities from trusted host dispatch metadata; unresolved aliases must remain an explicit unverified-independence state rather than satisfying a mandatory independent audit.

5. **MAJOR — GetArgument passes a tuple-valued oracle to boolean claim APIs, producing an incorrect dossier.** `plugins/empirica/application/service.py:233–237,270–271` passes `build_evidence_oracle()` directly to `gating_goals` and `state_of`, whose contract is boolean (`core/claims.py:36–50`). `(False, reason)` is truthy. **Executed reproductions:** a confidence-0.9 claim without evidence is advertised as `approved` while EvaluateRun returns Block; an unsupported `refuted_by` makes the dossier contain zero claims while RestoreRun correctly reports one gating claim. The latter defeats “every gating claim” coverage by omitting precisely the questionable subtree. The digest helper itself is shared, but it cannot repair different claim selection.
   **Suggested fix:** Adapt the oracle with `lambda nid, purpose: evidence(nid, purpose)[0]` consistently for dossier traversal/state and test unsupported refutations plus evidence-less high-confidence claims.

6. **MAJOR — Shared digest code is not order-independent, so separate bridge processes can disagree.** `plugins/empirica/application/knowledge.py:413–419` sorts leaves by only a subset of the fields it hashes: `kind`, `gate`, and `result_hash` are hashed but absent from the ordering key. `Knowledge.from_artifacts` preserves iteration order from the repository's artifact set (`knowledge.py:210–230`). For two claim-bound research leaves identical on the sort key but different in `kind`, reversing input order produced digests `45683d9d…` versus `86eb6232…`. Artifact frozenset iteration can change with process hash randomization; Pi starts separate bridge processes. This is inherited digest logic, but ADR-41 newly relies on it to promise that GetArgument-only verdicts pass by construction. `service.py:236` and `service.py:942` calling the same function is not sufficient.
   **Suggested fix:** Canonicalize each complete hashed leaf and sort by that complete representation; test permutations and differing `PYTHONHASHSEED` values across dossier and gate processes.

7. **MAJOR — Freeze projection reverses the committed scope.** `plugins/empirica/core/obligations.py:98–99` marks claims *in* `frozen_claims` as “outside frozen scope.” The actual convergence/resume semantics defer claims *not in* the committed set (`core/convergence.py:99–100`; `application/service.py:355–356`). **Executed reproduction:** freeze `['G0']` in a three-claim graph; the contract marks G0 deferred and C1/C2 ordinary, while snapshot telemetry reports two deferred claims. B5-T3 only asserts that *some* deferred and blocked obligations exist, so it passes this inversion (`tests/test_application.py:1638–1641`).
   **Suggested fix:** Defer `frozen_claims is not None and claim_id not in frozen`; assert the exact held and unheld claim-ID sets, including an empty committed scope.

8. **MAJOR — Synthetic obligations disappear without retirement, including across a read-only boundary.** Budget/stall obligations are added only by the initial terminal response (`plugins/empirica/application/service.py:1052–1055,1082–1085`); RestoreRun/GetRun call `_contract_view` with both flags false (`service.py:332,1115,1153`). **Executed:** terminal `stopped_budget` → RestoreRun gives `preserved=False`, “empirica/run/budget: obligation disappeared without retirement”; the stalled equivalent loses `empirica/run/stall`. No observation intervenes. Separately, audit presence depends on all claims currently being approved (`service.py:1178–1189`). Lowering confidence alone removes the audit obligation while durable contract revision stays `1`; `preserved()` rejects that transition too. An argument-shape change can replace one digest-qualified audit requirement with another without retiring the old one. ADR-39 documenting these as synthetic does not make them satisfy its advertised invariant.
   **Suggested fix:** Derive persistent terminal requirements from stored terminal state on every view, and give synthetic audit requirements explicit lifecycle/retirement semantics or a separate interface with a narrower preservation contract.

9. **MAJOR — Several real Block boundaries still throw away an available contract.** Pi audit-ticket Block returns only `ticket.result.reason` (`plugins/empirica/adapters/pi/src/index.ts:332`). Codex spawn and CLI-budget denials return only prose (`adapters/codex/lifecycle.py:309–310,342–344`). Claude CLI dispatch denial prints only prose (`adapters/claude/lifecycle.py:292–296`). These are not unreadable-state Fault exceptions: the service has supplied `run.contract`, and the adapters discard it. The Pi test named “auditor ticket Block denies with rendered contract” checks only `/ticket denied/`, not a contract (`adapters/pi/test/audit.test.ts:46–50`). Codex's renderer regression covers Stop, not these PreToolUse branches.
   **Suggested fix:** Route every Block through one contract-aware rendering function and parameterize tests over all operation-specific denial paths.

10. **MAJOR — Spawn accounting is neither exactly-once nor tied to a reservation.** After Pi reserves, an audit-ticket Block/Fault returns without refund (`plugins/empirica/adapters/pi/src/index.ts:321–332`); a GetArgument transport exception does the same (`index.ts:335–351`). Claude similarly reserves before ticket denial (`adapters/claude/lifecycle.py:156–180`). On the other side, `_void_spawn` decrements the global spawn count even for an already-void or unknown nonce (`application/service.py:788–798`). **Executed:** reserve twice, issue one ticket, void that same nonce twice: count becomes zero, refunding the other reservation. The test claiming “releases once” starts with just one reservation, so flooring at zero conceals the double-refund (`tests/test_application.py:1810–1824`). Pi also assumes every `isError` means the child never ran (`index.ts:291–294`), which is false for a launched child's timeout/error.
   **Suggested fix:** Allocate reservation IDs, bind each ticket to one reservation, and make release an idempotent state transition triggered only by host-confirmed non-launch.

11. **MAJOR — `preserved()` does not prove the identity/history property used to certify this PR.** `lib/obligations/project.py:96–122` compares only formerly-live obligations; it never checks contract identity, revision monotonicity, prior retirement retention, or hold changes. **Executed:** take P05's already-retired after-view, erase all retirements and rewind revision to 1: `Preservation(ok=True)`. Consequently it cannot certify “the SAME contract (revision, retirements).” The application's OB1 test compares a contract to itself (`tests/test_application.py:1337–1339`), and OB5 compares the old pre-terminal contract against both outputs rather than comparing terminal handoff to artifact/restore (`tests/test_application.py:1352–1364`), missing finding 8. The Pi tests execute real registered handlers, not copied algorithms, but `FakePi` merely saves callbacks (`adapters/pi/test/fakes.ts:47–66`); even `live-bridge.test.ts:30–32,99–104` uses this fake host and directly invokes its callback. It proves a real Python bridge round trip, not real Pi result replacement or pi-subagents execution.
   **Suggested fix:** Separate non-weakening from exact boundary preservation, check identity/revision/history explicitly, compare adjacent actual boundary outputs, and add host-runner/pi-subagents conformance tests rather than labeling the bridge-only test live-host proof.

12. **MINOR — Nudge identity and reset semantics differ from the documented policy.** `plugins/empirica/adapters/pi/src/index.ts:389–407` keys on operational `run.revision`, prose reason, and top-level `converged`, not contract revision/verdict. Knowledge appends do not change operational revision (`application/service.py:490–498`), so new evidence with the same reason can be suppressed; budget bookkeeping can instead produce unnecessary distinct nudges. `parseInt(...) || 3` makes configured zero become three; counters are not reset on a new run or the advertised “resume by continuing.” The pause notice also drops the contract.
   **Suggested fix:** Key nudges by run handle plus canonical contract/verdict digest, validate the configured limit explicitly, and define/reset pause state on a real resume event.

## Boundary inventory and dimensional verdicts

| Boundary | Verdict and evidence |
|---|---|
| Application Block / Allow | **Partial.** `_run_view` consistently supplies the persisted revision's projection (`application/service.py:1117–1129`), including retirements; `wire.run_obj` strips a `None` contract (`application/wire.py:185–190`). Synthetic requirements and corrupt-pointer behavior prevent a universal preservation claim (findings 8 and 11). |
| Inert / Fault | **Not contract-bearing.** `application/wire.py:205–213` returns no run. Inert(no_run) has nothing to preserve; an active-run Fault needs an explicit unknown/unavailable-state exception to ADR-39, not a claim that the same contract survived. `_load_contract` silently returns None for unreadable/missing contract data (`service.py:1213–1225`), so an otherwise-readable run can also produce Allow without its contract. |
| RestoreRun | **Fail globally.** Durable claim revision/retirements are loaded, but synthetic terminal requirements disappear and raw ticket secrets are exposed (`service.py:308–334`; findings 1 and 8). |
| Claude compaction | **Partial / security fail.** Active-run embed includes the exact `run.contract` supplied by RestoreRun, but includes nonce-bearing snapshot and omits `run.goal`/`run.modes` as run fields (`adapters/claude/restore.py:54–72`). Terminal runs are deliberately omitted. |
| Codex compaction | **Partial / security fail.** Same pattern (`adapters/codex/lifecycle.py:390–406`); no terminal handoff restoration. |
| Pi compaction | **Partial.** Success includes rendered contract plus JSON details (`adapters/pi/src/index.ts:376–383`). Fault, missing contract, or exception silently falls through to ordinary compaction, with no deterministic preserved embed. Success replaces the host summary with only Empirica goal/obligations; preserving unrelated task context and model-visible handle/modes needs a host-level test. |
| Terminal handoff | **Fail on subsequent restore.** Initial response carries a richer view and the durable claim-contract artifact pointer; the pointed artifact is not that full view (`service.py:1052–1055,1082–1085`; finding 8). Claude Allow serializes the typed result (`adapters/claude/completion.py:88–89`); actual Stop stdout visibility to the author was not live-verified here. Codex uses `systemMessage` (`adapters/codex/lifecycle.py:378–380`). |
| Pi nudges | **Partial.** Normal nudge appends contract text (`adapters/pi/src/translate.ts:207–214`); pause notice and key policy do not preserve the same semantics (finding 12). Pi still has no completion veto when the report tool is not invoked. |
| Pi tool results / denials | **Fail on identified branches.** Ordinary registered tools render the supplied view (`adapters/pi/src/index.ts:133–139`); ticket denial and failed auditor ingestion do not (findings 3 and 9). `textResult` also reports “no contract yet (no graph)” for a Fault lacking a run instead of surfacing the fault, so absence can be misrepresented as normal pre-graph state. |
| Claude stderr | **Partial.** Stop Block and ordinary spawn denial render the contract (`adapters/claude/completion.py:88–96`; `lifecycle.py:98–105`); CLI spawn denial does not. Exit-zero SubagentStop stderr is not a demonstrated author-visible contract handoff. |
| Codex block reason | **Partial.** Stop uses `_render_contract` (`adapters/codex/lifecycle.py:370–372`); spawn/CLI denials do not (finding 9). |

**Security:** FAIL. Closed Pi knowledge kinds and void-ticket exclusion work, but public/deterministic nonces, unauthenticated ingest, missing Claude redaction and unresolved model identities defeat ADR-41's stronger claim. Consumed tickets are bookkeeping, not one-shot capabilities. Codex's lack of native ingest is not a core prohibition on forged convergence.

**Digests:** FAIL. Dossier/gate share `build_digest_of` and `claims.argument_digest`, but tuple-valued traversal changes the dossier claim set and incomplete sorting makes evidence digests process-order-sensitive. Frozen/blocked claims do not alter the shared digest algorithm itself; freeze projection is separately wrong.

**B5 revisions:** PARTIAL. Graph writes and freeze append a real revision before CAS, retaining explicit retirements (`application/service.py:881–889,667–678,1227–1289`). Confidence-only updates correctly avoid durable revision churn. The synthetic lifecycle and the verification used to certify revision/history are not consistent with the headline invariant. A batch retirement can also attribute every retired claim to the first refutation found (`service.py:1276–1285`), so mixed graph edits deserve a per-retirement-authority test.

**Pi host/design:** PARTIAL. Foreground auditing is a reasonable temporary compatibility restriction, not a security mechanism. The installed pi-subagents implementation recognizes `async === false`, but the PR does not execute it in the “live bridge” test. It must test timeout, cancellation, reload, workflow/resume shapes, and the original-run association. `isExecutableSpawn`'s XOR shape check does not validate downstream execution semantics; writing `input.task` is not proof that a workflow or resumed child receives that task.

**Tests as specification:** FAIL for the advertised universal proofs. Concrete gaps are identified above, despite passing focused suites. Application A2 tests nonce-free tickets on a dossier fetched before ticket issuance (`tests/test_application.py:1775–1786`), a vacuous empty-list check; the Pi bridge test does cover populated GetArgument tickets, but neither checks RestoreRun secrecy. Schema validation of committed fixtures is not fixture regeneration or a cross-host execution proof (`scripts/validate_contracts.py:41–81`). I did not establish that any fixture was falsely claimed to be generated; the reproducibility check is simply absent.

**Repository hygiene:** No merge-blocking hygiene finding. `make help` ran first; lifecycle targets have help descriptions and validator implementations live in `scripts/`. No new Makefile heredoc issue found. Both Empirica host manifests say `1.3.0`; `make docs-check` confirms README/CLAUDE generated tables match. Committed `.pi/settings.json:2–15` is an explicit repository-development override without machine-local secrets, acceptable if intentional; `.gitignore:16` covers `.pi/git/`. `make check` is not strictly “every check”: `docs-check` belongs only to `release-check` (`Makefile:64,280`), and missing ruff/adrs/Node/typecheck dependencies can skip gates (`Makefile:84–91,109–115`; `scripts/validate_pi_adapter.py:17–30`). Do not equate a generic green banner with all runtime surfaces being verified.

**Reusable-library/API design:** No Empirica-specific implementation leak found in `lib/obligations`; grep hits are only disclaimers and the ordinary English word “claim” (`lib/obligations/__init__.py:3–4`, `project.py:4`). Its preservation API is weaker than the application's intended proof (finding 11). `run.argument` is a defensible command-specific fragment next to `run.contract`, provided consumers do not assume every run view carries an argument. A dedicated top-level response would improve type precision, but is not the security or correctness problem here.

## Validation performed

- `make help`; `gh pr view 25`; read-only git diff/status inspection.
- Application suite: **161/161 passed**.
- `make empirica-pi-check`: **79/79 passed**, zero skips, bridge smoke and TypeScript typecheck passed.
- `make docs-check obligations-check vendor-check contract-check`: passed; 22 obligation fixtures, five byte-identical vendor files, seven API schemas and 11 API fixtures.
- In-memory Python/Node adversarial probes executed production service/adapter code: predictable nonce/forged convergence, snapshot/Claude nonce exposure, wrong frozen IDs, missing dossier claims, false dossier approval, order-sensitive leaf digest, budget/stall/audit disappearance, duplicate void refund, lost-retirement acceptance, and Pi ingest-failure nonce exposure.
- `make check` exceeded the 300-second tool timeout; a separate `make test` exceeded 180 seconds. Neither is reported green. Initial exploratory probes had fixture-shape mistakes; corrected probes produced the results quoted above. No production mutation experiments were performed.

## Overall verdict: REQUEST CHANGES

Three requirements before merge:

1. **Replace the audit trust claim with an actually enforced principal boundary:** close predictable/public nonce and direct-ingest attacks, resolve real model identities, and prove author-visible redaction on each supported host. Codex must explicitly fail closed for unavailable authenticated audit ingestion.
2. **Repair and prove the complete contract lifecycle:** exact freeze scope, boolean dossier traversal, canonical digests, terminal/restore synthetic retention, and every operation-specific Block renderer; assert adjacent boundary outputs including revision and retirements.
3. **Run host-conformant adversarial tests and a complete release gate:** real Pi runner/pi-subagents and Claude result handling, concurrent/reloaded/failed audits, idempotent reservation cleanup, and the public-nonce attack; obtain an unambiguous completed `make check` plus `make docs-check`.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Twelve severity-tagged findings cite production file paths and lines; executable production-code probes reproduced audit forgery, contract loss, scope inversion, digest instability, and Pi nonce leakage."
    }
  ],
  "changedFiles": ["doc/design/reports/review-astra-pr25.md"],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {"command": "make help", "result": "passed", "summary": "Read lifecycle entry points first."},
    {"command": "gh pr view 25", "result": "passed", "summary": "Read PR claims and limitations."},
    {"command": "python3 plugins/empirica/tests/test_application.py", "result": "passed", "summary": "161/161 checks passed."},
    {"command": "make empirica-pi-check", "result": "passed", "summary": "79/79 tests; zero skips; bridge smoke and typecheck passed."},
    {"command": "make docs-check obligations-check vendor-check contract-check", "result": "passed", "summary": "Generated docs, schemas/fixtures, and vendor consistency passed."},
    {"command": "In-memory Python and Node adversarial probes", "result": "passed", "summary": "Reproduced the counterexamples documented in findings 1, 3, 5–8, 10, and 11 without editing production files."},
    {"command": "make check", "result": "failed", "summary": "Tool timeout after 300 seconds; full gate completion not established."},
    {"command": "make test", "result": "failed", "summary": "Tool timeout after 180 seconds; focused application/Pi suites completed separately."},
    {"command": "git diff --name-only --cached", "result": "passed", "summary": "No staged files."}
  ],
  "validationOutput": [
    "FORGED_PUBLIC_NONCE_CONVERGED True",
    "NONCE predictable=True restore_snapshot=True Claude_compaction=True",
    "FREEZE: G0 deferred; C1/C2 not deferred; snapshot deferred count 2",
    "DOSSIER_PRUNING: dossier claims []; RestoreRun gating count 1",
    "DOSSIER_STATE_NO_EVIDENCE approved; EvaluateRun Block",
    "LEAF_DIGEST_ORDER: reversing equal-key leaves changes the digest",
    "Budget/stall terminal-to-RestoreRun preserved=False",
    "Audit requirement disappears at unchanged contract revision 1",
    "DOUBLE_VOID_TWO_RESERVATIONS 0",
    "PRESERVED_LOST_RETIREMENT_AND_REVISION ok=True",
    "PI_NONCE_VISIBLE_AFTER_INGEST_FAILURE true"
  ],
  "residualRisks": [
    "No live Claude audit/result-redaction run was performed.",
    "Pi bridge tests use FakePi rather than executing real pi-subagents; installed host code was inspected, not live-dogfooded.",
    "Full make check and make test did not complete within their tool timeouts.",
    "Same-user store/transcript access is not isolated from an author with execution tools.",
    "Additional contract observation-binding and mixed-retirement authority edge cases remain outside the executed probe set."
  ],
  "noStagedFiles": true,
  "diffSummary": "Created only the requested independent review report; no production files or tests edited and no git write commands issued.",
  "reviewFindings": [
    "BLOCKER: application/service.py:316,1336 — public/predictable nonce permits forged audit convergence.",
    "MAJOR: adapters/claude/lifecycle.py:229 — no author-result redaction.",
    "MAJOR: adapters/pi/src/index.ts:287 — failed ingestion/reload can leak nonce and lose correlation.",
    "MAJOR: adapters/pi/src/index.ts:433 — agent alias substituted for model identity; Claude author identity absent.",
    "MAJOR: application/service.py:233 — tuple oracle corrupts dossier claim selection/state.",
    "MAJOR: application/knowledge.py:413 — incomplete sort makes evidence digests order-sensitive.",
    "MAJOR: core/obligations.py:98 — frozen scope inverted.",
    "MAJOR: application/service.py:1052,1082 — synthetic obligations disappear on RestoreRun.",
    "MAJOR: adapters/pi/src/index.ts:332 — ticket denial drops contract; analogous Claude/Codex branches do too.",
    "MAJOR: application/service.py:788 — duplicate void refunds other reservations; denied launches also leak reservations.",
    "MAJOR: lib/obligations/project.py:96 — preservation proof ignores identity/revision/retired history.",
    "MINOR: adapters/pi/src/index.ts:402 — nudge identity/reset policy differs from contract policy."
  ],
  "manualNotes": "Overall request changes. Several security/digest mechanisms are inherited, but ADR-41 newly relies on them for a stronger guarantee they do not provide."
}
```
