# Scenario ledger

Record every row as `PASS`, `FAIL`, `BLOCKED` or `NOT_REACHED` with an evidence pointer. On Claude the Stop never
pauses for evidence: it blocks and asks for `report_convergence`, and the human is asked inside that call. Pi presents
in-process.

## A. Primary Empirica scenario (one run, in order)

| Row ID | When | Human/author action | Expected observation | Evidence |
|---|---|---|---|---|
| `settings_env_and_entrypoint` | Before activation | Inspect the retained Claude hook/MCP log, or Pi host context. | Claude settings `env` reaches hooks and MCP; the recorded entrypoint is `cli`. Pi records `ctx.mode=tui\|rpc` with `interactive=true`. | Hook log or host context |
| `activation_verbatim_goal` | Start | Human invokes Claude `/empirica:empirica <goal>` or Pi `/empirica <goal>`. | User-command expansion, exact verbatim goal, trusted invocation provenance, and fresh run binding. A model-invoked skill/read or ordinary pasted chat does not activate. | RunView and transcript |
| `configuration_pending` | After route and graph | Route, propose the graph from supplied context, then in the same turn submit `configure_run` with budgets `8/0/1` so the host opens the configuration dialog. Human CANCELS that first dialog. | Human review shows goal read-only and configuration edits only; graph and reviewer fields are absent; the cancellation records a dismissal (presentation count visible), the run stays pending and non-terminal with zero investigation usage, and nothing is approved. | Pending RunView and dialog |
| `sentinel_blocked_before_approval` | While configuration is still pending after the dismissal | Attempt one benign, uniquely named write in the evidence directory. | Host blocks before execution; operator-side inspection confirms the file is absent. Unexpected execution is FAIL: preserve evidence and stop. | Denial plus operator file listing |
| `locked_approval_at_6` | After the sentinel, the author re-proposes `configure_run` to reopen the dialog | Human edits passes `8 → 6` and confirms the host-owned locked summary. | One FINAL CONFIRMATION occurs in the same call, read-only, with no author action between forms. Effective limit is 6 and digest, configuration epoch, and human provenance match. Host decision timeout is read-only, not a total run deadline. | Dialog transcript and RunView |
| `graph_change_keeps_configuration_approval` | After approval | Amend the claim graph from supplied context. | Configuration approval remains current and no configuration dialog opens. | RunView before/after |
| `research` | After investigation admission | Record investigation and cited research. | Source, citation, result, and lossless obligations are retained; nothing is invented. | RunView and evidence |
| `current_author_after_model_switch` | Between research and the spike | Human switches the main model A→B, allows one turn that records no evidence, switches back to A. | Every artifact's producer is the model that served it (A); nothing is attributed to B; the reviewer is derived from the current author A, not from B or the earliest model. | RunView attribution and host model-switch signal |
| `spike` | After the model switch returns to A | Record one deterministic spike. | Command, input bindings, exit status, result, and lossless obligations are retained; nothing is invented. | RunView and evidence |
| `served_reviewer_and_producers_distinct` | When audit is owed | Request one canonical auditor through the host profile. | Package path/scope is canonical; the verdict-bearing served model is observed separately from requested configuration; independence classification follows the [audit guide](../../../../plugins/empirica/skills/empirica/references/audit.md#independence-reporting). | Child transcript and RunView |
| `audit_stales_after_graph_change` | Optional, after a passing audit | Amend the graph, then re-evaluate. | Prior audit coverage becomes stale while configuration approval remains current; a fresh bound audit is required. | RunView before/after |
| `restore_after_compaction` | After a current passing audit, before completion | Human triggers host compaction (`/compact`). | The restored run has the same status, obligations, usage, and audit coverage; blocked actions remain blocked; no progress is fabricated. | RunView before/after compaction |
| `snapshot_completion` | After restore | Call the guarded completion path. | Exact `Allow(converged=true)` and matching durable terminal state. The terminal RunView keeps Required/Observed/Missing and offers only `run.inspect` guidance, no mutation actions. Prose, child pass alone, or `Allow(converged=false)` is not convergence. | Decision and terminal RunView |
| `installed_transport_latency` | Throughout | Operator records the slowest Empirica tool/hook round trip from the retained log. | No host transport timeout, retry, or dropped tool result occurred. | Debug/host log timings |
| `context_economy` | Throughout the primary run | Operator reviews every Empirica tool result and host warning in the retained transcript/log, then extracts the ordered Empirica tool results into a newline-delimited JSON slice and runs `make empirica-context-economy-report FILE=<slice>`. | No request for the removed full public-contract projection occurs (that GetContract target is refused as `invalid_request`) and no host "Large MCP response" warning fires. The measured object is each Empirica tool's rendered model-visible **text** (host `content` text), counted as-is in Unicode code points without JSON-decoding it, over the primary run from activation to terminal; other tools are excluded. Record maximum single/total characters and the text-only normalized envelope bytes separately. Claude/Codex MCP RunView results contain no duplicate `structuredContent`; Pi JSON remains host-internal `details`. Every author step was completed from tool input schemas and the rendered status/reasons/obligations/next calls. | Transcript/host log sizes and `empirica-context-economy-report` output |

## B. ADR-0067 evidence rows (each keeps its transcript, dialog and RunView; a converged run also its v3 receipt)

| Row ID | Setup and operator steps | Evidence to keep |
|---|---|---|
| `ev_verbatim_only` | Fresh run, approved. Record a workspace-path (verbatim) citation; request audit and completion. | RunView with `audit.required` and no confirmation batch; transcript. |
| `ev_url_confirm` | Record URL research. At the end of the pass call `report_convergence` (Claude) or let the gate present (Pi); the human confirms. | Dialog transcript showing the source and claim; RunView; v3 receipt if converged. |
| `ev_url_with_open_claim` | URL evidence plus another unresolved claim; report; the human answers the batch after the claim-leading block. | RunView showing the open claim's reason leading while the human is asked. |
| `ev_later_cancel_timeout` | A fresh run per branch: present once and choose Later, cancel, or let the timeout expire. Do not retry the same derivation. | `prompt_error: already_presented`, a blocked Stop or gate (Claude blocks with the real reason, never a pause), RunView. |
| `ev_reject_with_note` | Present a URL record and reject it with a note. | The obligation row with the fenced note and `claim.research_missing`; partial evidence retained; no receipt. |
| `ev_needs_decision_only` | A graph whose only open claim is a `needs-decision`; answer it in the batch, then complete the required audit. | The decision answer; `human_owned` only after the observed auditor identity; audit transcript; v3 receipt. |
| `ev_audit_fails_after_approval` | Approve evidence, then observe a real audit failure only if it occurs naturally. Otherwise `NOT_REACHED`. | The answer stands and `audit.failed` shows; no invented failure; no receipt. |
| `ev_route_unavailable` | A fresh delegated, non-interactive or no-form run (or Codex); provide no dialog. | `route: unavailable`, `prompt_error: unavailable`, nobody asked, only `residual.accept` remains. |
| `ev_over_64_items` | Create more than 64 pending items in a fresh run and present. | RunView and dialog: the first 64 only; the rest after a change. |
| `ev_missing_or_stale_spike` | URL evidence plus a missing or stale spike on one claim; report. | `claim.spike_missing` or `claim.spike_stale` leads; no pause; transcript and RunView. |
| `ev_long_quote` | A URL citation with a long verbatim quote; present and read the whole text. | The dialog shows the whole quote, untruncated. |
| `ev_later_then_fresh_get_run` | After Later, call `get_run`. | The author's view says it was shown and only a changed derivation asks again. |

## C. Injected and unreachable rows: covered by deterministic tests, never claimed live

| Situation | Deterministic test (never claimed live) |
|---|---|
| Interactions exhausted (`prompt_error: interaction_limit`, no dialog, the real reason). The 128-interaction ceiling is a fixed contract constant, not an operator or host setting. Native qualification does not exercise it; exhausting the production ceiling would take 128 shared interactions. | `tests/v2/test_evidence_scenarios.py::test_D_exhausted_interactions_are_a_prompt_error_not_an_answerable_wait`; `tests/v2/test_evidence_confirmation.py::test_the_real_interaction_limit_is_128` and `::test_the_human_interaction_budget_is_one_count_shared_with_governance` |
| A record joins between the host's read and its presentation (interleaving race) | `tests/v2/test_a1_boundaries.py` (presentation key, `conflict`, one reread) |
| The host cannot record its surface (typed Fault or exception, no UI) | `tests/v2/test_a1_boundaries.py` `FailedObservationFailsClosed`; Claude Stop in `adapters/claude/tests/test_claude_adapter.py`; Pi `test/gate.test.ts` |
| Overlapping observations, unwritable or unreadable storage | `tests/v2/test_a1_boundaries.py` (every Claude Stop exits 2) |
| A path spelled with `..`, `//`, a trailing slash or a colon directory | `tests/v2/test_a1_boundaries.py` `SpellingIsRefusedNotInterpreted`; `tests/test_citations.py` |
| A failure after reservation (refunded `launch_rejected`, relaunch) | `make empirica-host-integration` |
| Receipt omission, rolled-back or wrong repository | `scripts/tests/test_empirica_live_receipts.py` |
| Synthetic reviewer mismatch | `tests/test_governance.py` (mixed producers, same class reviewer) |

## D. Required refusal checks (each in a separate fresh run)

| Row ID | Human/operator action | Expected observation | Evidence |
|---|---|---|---|
| `empty_goal_refusal` | Invoke without a goal and, where the host permits it, with whitespace-only goal input. | Visible structural `Block` and no run/handle creation. | Host output and run census |
| `noninteractive_auto_refusal` | Invoke auto through a non-interactive surface without the configured delegation environment variable. | Claude records `sdk-cli` from env or transcript. Pi records provenance signal `ctx.mode=print\|json` with `interactive=false`. Both return a visible structural `Block`, create no run, and name interactive invocation or delegation as remedies. | Hook log or RunView refusal |
| `delegated_auto_admission` | Optional: only when the operator explicitly prepared `EMPIRICA_AUTO_DELEGATION=1`, repeat non-interactive auto. | Provenance records delegation and StartRun is admitted. This does not qualify audit or convergence. | RunView |
| `cancellation_and_timeout_nonterminal` | Human cancels the first configuration dialog; at the next presentation lets it expire. Launch this run with a short `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` (for example `60`) prepared by the operator. | Each settles as dismissal, not rejection: the run stays active and non-terminal, configuration is not approved, investigation remains blocked, and the presentation count is visible. End with an honest stop. | Dialog transcript and RunView |
| `mixed_producers_block` | Human switches the main model after research so the spike is served by a second model, then the author requests the audit and the guarded completion. | The audit may launch and complete, but the verdict is classified `mixed` and completion is refused with `audit.producers_mixed`; never `Allow(converged=true)`. End with an honest stop. | RunView independence/blockers and decision |
| `reviewer_preflight_refusal` | Pi required; Claude optional. Author runs on the model the host would choose as reviewer (Pi: the effective `subagents.defaultModel`), records evidence, then requests the audit. | Refused before launch as same identity class or unavailable; no child is reserved and audit usage is unchanged. End with an honest stop. | RunView and host denial |
| `codex_negative` | Optional: invoke `$empirica <goal>` in Codex. | Visible unsupported/refusal result; no convergence claim. | Codex transcript |

## E. Methodologist sanity

After Empirica is terminal, use the host's Methodologist entry (see the host reference), choose a named methodology as the
human, and verify the six-phase plan comes from this checkout. Where MCP selection is exposed, retain one normal
`methodologist_select` result. On Codex use `$think` or the implicit native simple mode. Codex Empirica establishes only
its expected fail-closed refusal, never convergence. Large-scope viewport accessibility is an optional separate run.
