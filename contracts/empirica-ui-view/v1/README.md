# empirica-ui-view/v1: the observer read model

What a human-facing view of the Empirica run store reads: every project, run and claim the
observer can read (`view.schema.json`), and one run's detail on demand (`run.schema.json`,
additive to its row). The plugin's observer (`plugins/empirica/application/observer.py`) is the
only producer, served on loopback by `plugins/empirica/adapters/observer/server.py` (ADR-0065).
A consumer joins, counts, filters and draws. It derives no state.

## Ownership

| Fact | Owner | How this contract holds it |
|---|---|---|
| Run status, claim kinds and states, audit verdicts and independence, freshness states, artifact outcomes, reason codes and messages (`message` and `ended_message`) | `contracts/empirica/v2/public-contract.json` | `scripts/validate_ui_view.py` requires each schema enum to equal its registry list |
| Governance control mode and approval state | `contracts/empirica/v2/state.schema.json` | the same check |
| History step kinds | `plugins/empirica/application/history_records.py` (`HISTORY_KINDS`) | the same check |
| Claim state, blocker, the audit's four facts (`verdict`, `current`, `pending`, `independence`), residuals, freshness | the plugin's evaluation and projection (`core.evaluation`, `core.projection`) | `plugins/empirica/tests/test_observer.py` compares every run with the plugin's own `GetArgument` and `GetRun` |
| What the run still requires, in the report's order, who acts and where (`outstanding`, `next`, `attention`); whether the gate accepts the audit for a claim (`assurance`) | **one pure projection in the core**: `core.evaluation.report_requirements` and `audit_acceptance`, the very functions the convergence report blocks on and the author view lists as its obligations; `core/observer.py` only shapes them | the generated invariant `test_INVARIANT_the_observer_says_what_the_gate_and_author_view_say` (`tests/v2/test_observer_agreement.py`): runs through the real service, each variant compared with the report's own answer and `GetRun` |
| `evidence`, `review`; a decision's `status`; spike `state` | `plugins/empirica/core/observer.py` (`EVIDENCE_KINDS`, `REVIEW_STATES`, `DECISION_STATUSES`, `SPIKE_STATES`: classifications added here, each derived from the plugin's own evidence and freshness) | the enum check, and the oracle tests in `test_observer.py` (agreement with public `GetArgument`/`GetRun`) |
| What an ended run left (`residuals`, `stop`) | the stop's own record: the observation basis the run's last transaction recorded (`application/snapshot.recorded_observations`), never today's files; a frozen scope never stands in for an unmet audit or budget stage | `HistoricalFactsTests`; `_residuals` of the invariant: the stop left what the report required first immediately before it |
| **Everything current about a run, ended or not** (the audit and its currency, each claim's state, review, assurance and blocker, the freshness of its proof, the files that changed since, `now`: every requirement the report would still make of the run as it is now, whatever its terminal status, from `report_requirements`) | the live snapshot, through the same owners as the author view: **two time bases, named**: the recorded one feeds only the at-stop facts above | the invariant's `_current`: observer = `GetRun` and `GetArgument` on the live snapshot for every variant, including a converged run whose files changed after |
| Whether a human is shown the question (`attention`, a human item's `where`, the decision and record cards) | `core.confirmation` (`presentable`, `ASK_WHERE`): the batch's own `prompt_error`, the predicate both host mediators use; an unobserved surface (the host has not yet looked for a dialog) is its own place, `if_available`, never `next_report`: `ASKABLE` (the gate's and turn end's "may look") and `REPRESENTABLE` (explicit mediation's "re-ask") stay distinct, and only `REPRESENTABLE`'s places make `needs_you`; it is separate from the gate's first blocking reason (which is `outstanding[0]` and what an agent-only run names as `next`) | the invariant's `_attention`: `needs_you` exactly when the real `HostConfirmation` mediator presents a form on the next report; for an unknown surface, an unknown compared with the post-observation owner snapshot, with the dialog present and absent |
| Where a session can be found (`session`) | the host: `invocationProvenance.session` in `contracts/empirica/v2/shared-defs.json`, recorded at run start by the Claude and Pi adapters | `test_host_runtime.py`; absent means unknown, never invented |
| Project name and repository | a repository given for it (`--repo`, matched by the plugin's identity, `adapters/state/identity.project_id`), else the one the bridge recorded for the project when it created a run (`adapters/state/locations.py`); the name is its folder name; the row's `label` is that name, else "Unnamed project", and `since` is the time its oldest run last changed, so a consumer never numbers projects | `adapters/observer/source.py`, `FilesystemStoreTests` and `LocationsTests` in `test_observer.py` |

A run's row carries its `goal`, `session`, `attention` (`needs_you` / `not_ended` / `ended`), `next` (the one obligation the row names: who acts, where, the words; null when nothing is owed), `audit` (the four facts),
`progress` (`{required, approved}`) and per-claim `assurance`; the view carries `coverage` (runs read, unreadable,
partial) and, per project, `families` (the attempts at one run, by generation). A run's detail also carries, from the
ADR-0067 evidence model: per claim `records[]` (source, kind, quote, outcome, `class`, `status`, `wait`, `retrieve`,
`spike_unavailable`, the human's `note`) and `decision` (`{status, outcome, answer, where}`); for the run
`outstanding[]` (what it still owes: who acts, where, which claims), `residuals[]` (identical rows grouped), `governance`
(mode, approval, ceilings used, the proposed ceilings only while a proposal waits on the human, interactions left) and history `steps[]` (`subject`, `outcome`, `terminal`,
`standing`, `bookkeeping`), per claim `proof[]` (each spike: `command`, `outcome`, `exit_code`, `gate`, bound `files`, `state` current / stale / superseded, `output` not retained), and, for a run that ended, `stop` (`basis` recorded or unavailable, `status`, `frozen`, `required`, `deferred`) and `now` (what it would leave unresolved against today's workspace; null unless the files changed). An `outstanding` item whose `where` is `later` waits for the items before it. Every message in it is the registry's template with its typed parameters filled in once, by the observer
(`core.observer.reason_message`, wording an ended run's residuals with `ended_message`); a consumer never sees a `{name}`.

A `null` is unknown, never a default: a run that was not read has no claims, progress or audit (`null`), which is not
zero, and `coverage` says how many were not read. A run the observer cannot fully read still appears with a
`diagnostic` that says why (the schema's `run.diagnostic` lists them). Only the facts it could
read are shown. A state that is not exactly the current state document (by the plugin's own
identifiers and state validator) is `unreadable`: nothing is read from it.

## Fixtures

`fixtures/` is generated by `scripts/regen_ui_view_fixtures.py`, which builds real runs with the
plugin's v2 service (`plugins/empirica/tests/v2/observer_store.py`) and projects them with the
observer. `scenarios.json` maps each scenario name to its run key. A byte-identical copy ships in
`plugins/empirica/vendor/` for the UI's demo mode. `make contract-fixtures` regenerates both;
`make contract-check` fails when either is stale or invalid.
