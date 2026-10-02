# Recovery reference

Generated from `contracts/empirica/v2/public-contract.json` by `scripts/gen_recovery_reference.py`
(`make empirica-recovery-reference`); do not edit by hand. Reason codes and next actions are the
contract's; the author guidance column is the one sentence to act on. Next actions are shown as the
text view renders them.

| Reason | Next actions | Author guidance |
|---|---|---|
| `run.no_active` | `host: StartRun` | Start a fresh run; nothing from a missing run can be reused. |
| `run.goal_required` | `host: StartRun` | Restate the task as a non-empty goal and start again; no run was created. |
| `run.terminal` | `empirica_read GetRun` | The run is finished: read its residuals with `GetRun` and report them; submit no more actions. |
| `run.corrupt` | `host: StartRun` | Stop using this run and start a fresh one; corrupt state is never repaired. |
| `graph.missing` | `empirica_observe kind=graph` | Submit a claim graph from the supplied context before sizing the run; no investigation is needed first. |
| `graph.invalid` | `empirica_read GetRun` | Read the run, then resubmit a closed, root-connected graph that matches the tool schema; do not change claims inside a frozen scope. |
| `route.required` | `empirica_observe kind=route` | Record the route before any investigation. |
| `investigation.required` | `empirica_observe kind=investigate` | Once the run is approved, record the investigate witness before any native read, command, evidence, or child. |
| `route.late` | `empirica_read GetRun` | The route came after investigation began and cannot be repaired: read the residuals, then stop or start a fresh run. |
| `claim.research_missing` | `empirica_observe kind=research` | Record cited research for the named claim; it may support or refute it. |
| `claim.research_unbound` | `empirica_observe kind=research` | The research cites an earlier claim text: record new research for the current claim text. |
| `claim.spike_missing` | `empirica_observe kind=spike_request` | Request a deterministic spike for the named `needs-experiment` claim. |
| `claim.spike_prerequisite_missing` | `empirica_observe kind=research`; `empirica_observe kind=spike_request` | Record supporting research for the claim first, then request the spike. |
| `claim.spike_stale` | `empirica_observe kind=spike_request` | The spike's inputs changed: request it again against the current research and dependent files. |
| `claim.human_decision` | `human: decision` | Ask the human for the decision; no author action settles a `needs-decision` claim. |
| `claim.refuted` | `empirica_read GetRun` | Active evidence refutes the claim: read the run, then discard or restate the claim in a new graph. |
| `evidence.conflict` | `empirica_read GetRun`; `report_convergence intent=stop` | Supporting and refuting research both exist for the claim's current text, and more research cannot clear it: revise or discard the contradicted claim (a frozen claim cannot be rewritten) and research the revised claim, or stop with the residual; never edit a claim cosmetically to evade adverse evidence. |
| `budget.exhausted` | `empirica_observe kind=configure_run`; `host: StartRun`; `report_convergence intent=stop` | A ceiling is spent: stop honestly with `report_convergence intent=stop`; in deliberative mode you may propose a raise for human approval, in auto mode start a fresh run. |
| `audit.required` | `host: child_reserve` | Launch the packaged auditor as your host's instructions say and never reserve the child yourself; on Codex, end the evidence-complete turn so Stop rejects the unsupported audit. |
| `audit.pending` | `host: child_event` | Do nothing: the host is settling the audit child; never poll or respawn. |
| `audit.unreadable` | `host: child_reserve` | The auditor returned no valid verdict: retry within the audit ceiling (normally once), otherwise stop. |
| `audit.failed` | `empirica_read GetRun`; `host: child_reserve` | Address every finding listed under `Audit:`, then retry within the audit ceiling (normally once); stop if it fails again. |
| `audit.stale` | `host: child_reserve` | The graph or evidence changed after the audit: launch a fresh audit of the current scope. |
| `audit.same_model` | `host: child_reserve`; `report_convergence intent=stop` | The auditor matched a covered producer's model: launch an auditor from a different model, or accept the residual. |
| `audit.independence_unverified` | `host: child_reserve`; `report_convergence intent=stop` | The host could not verify auditor independence: relaunch under a distinct observed model, or accept the residual. |
| `child.terminal` | `host: child_reserve`; `report_convergence intent=stop` | A child ended in a terminal state: retry within the ceiling (normally once), or accept the residual and stop. |
| `host.async_unsupported` | `report_convergence intent=stop` | This host cannot run the audit asynchronously: run it in the foreground, or accept the residual and stop. |
| `host.audit_output_unobservable` | `report_convergence intent=stop` | This host cannot observe the auditor's output, so the audit cannot pass: accept the residual and stop. |
| `host.subagents_missing` | `report_convergence intent=stop` | No external pi-subagents runtime is active: install one supported runtime and restart the host, or accept the residual and stop. |
| `host.subagents_tool_inactive` | `report_convergence intent=stop` | The `subagent` tool is registered but not active: enable it (`subagents_enable`, or `toolActivation: eager`) and retry; a host restart does not change this. Or accept the residual. |
| `host.subagents_owner_unverified` | `report_convergence intent=stop` | The active audit runtime cannot be proven (owner, package, or version unobservable, or a subagent child process): do not retry blindly; restart the host with one supported runtime, or accept the residual. |
| `host.subagents_duplicate_owner` | `report_convergence intent=stop` | Two extensions register the `subagent` tool: remove the duplicate, restart the host, and start a fresh run, or accept the residual. |
| `host.subagents_version_unsupported` | `report_convergence intent=stop` | The active pi-subagents is not a reviewed version or lacks the launch preflight: install a reviewed version and restart, or accept the residual. |
| `host.subagents_launch_unsupported` | `report_convergence intent=stop` | The runtime admits a launch form Empirica cannot correlate: use only the canonical foreground auditor launch, or accept the residual. |
| `host.subagents_provenance_missing` | `report_convergence intent=stop` | The run or receipt lacks the exact pi-subagents version and owner path: recapture a provenance-complete run; this one is not evidence. |
| `freeze.deferred` | `report_convergence intent=stop` | Deferred claims stay residual: accept them when you stop, or investigate them before freezing. |
| `governance.auto_invocation_required` | `host: StartRun` | Auto needs an interactive invocation or operator-set `EMPIRICA_AUTO_DELEGATION=1`: run interactively or have the operator set it, then start fresh. |
| `governance.approval_required` | `empirica_observe kind=configure_run`; `report_convergence intent=stop` | Submit a task-sized `configure_run` (all three ceilings plus a 1-600 character rationale) and wait for host approval; nothing is authorized before it. |
| `governance.revision_required` | `empirica_observe kind=configure_run`; `report_convergence intent=stop` | The configuration changed: resubmit `configure_run` for a new host decision, or stop honestly. |
| `governance.approval_unavailable` | `host: governance_context`; `report_convergence intent=stop` | The host could not obtain a decision: do not retry blindly; have the host refresh its governance context or use an approval-capable host, or accept the residual. |
| `governance.auto_ceiling` | `host: StartRun`; `report_convergence intent=stop` | Auto never raises ceilings after approval (interactive auto included), and a delegated proposal must fit its actual envelope (8/1/2, or narrower if the operator limited it): reduce scope, or start a fresh run with a larger approved size. |
| `governance.budget_contradictory` | `host: StartRun` | StartRun budgets or operator limits exceed the fixed 8/1/2 delegated-auto policy, or a StartRun budget widens an operator limit (budgets may only narrow): correct the offending source and start a fresh run. |
| `governance.revision_exhausted` | `host: StartRun`; `report_convergence intent=stop` | Auto's material-revision allowance is spent: start a fresh run with a larger approved size, or use deliberative mode. |
| `governance.budget_invalid` | `empirica_observe kind=configure_run`; `report_convergence intent=stop` | A proposed ceiling is below what is already used: resubmit `configure_run` with ceilings at least equal to usage. |
| `governance.stale_proposal` | `empirica_observe kind=configure_run`; `report_convergence intent=stop` | The decision was bound to an older revision: read the run, then resubmit the current proposal for a new decision. |
| `governance.receipt_replay` | `empirica_observe kind=configure_run`; `report_convergence intent=stop` | A superseded or conflicting decision was replayed and changed nothing: read the current proposal and act on that. |
| `governance.interaction_limit` | `empirica_observe kind=configure_run`; `report_convergence intent=stop` | The dialog allowance is spent (3 per configuration, 128 per run): stop honestly or start a fresh run. |
| `governance.decision_conflict` | `empirica_observe kind=configure_run` | The submitted control and edited fields disagreed and nothing was approved: use one explicit decision path. |
| `audit.producers_mixed` | `report_convergence intent=stop` | Covered evidence has mixed producers: if only the latest spike differs, re-run it under the research producer, otherwise accept the residual. |
