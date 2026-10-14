# empirica-ui-view/v1: the observer read model

What a human-facing view of the Empirica run store reads: the store summary (`summary.schema.json`, `GET /api/summary`),
pages of one-line rows (`page.schema.json`, `GET /api/runs` and `GET /api/search`), and one run's sheet on demand
(`run.schema.json`, `GET /api/run?key=…`: the run's detail, its own row and every attempt's row, so a side sheet is one
request). `row.schema.json` is a run as the observer indexes it (the demo's `fixtures/rows.json`). The plugin's observer
(`plugins/empirica/application/observer.py`, with the store-wide functions in `application/observer_index.py`) is the
only producer, served on loopback by `plugins/empirica/adapters/observer/server.py` (Host allowlist, CSP, no CORS, GET and
HEAD only, ETag and 304 on the summary and on a sheet; ADR-0065). This is the only contract and its first version: it was
never released, so it is edited in place and is compatible with nothing before it (5.0.0 is unreleased and has no existing runs).

A consumer joins, counts, filters and draws. It derives no state: every fact below has one owner.

## Ownership

| Fact | Owner | How this contract holds it |
|---|---|---|
| `standing` (per run): `unreadable` · `needs_human` · `audit_pending` · `changed_since_audit` · `not_ended` · `proven` · `stopped_residual` · `stopped_budget` · `stopped_frozen`, in that precedence | `core.observer.standing`, over `attention`, the status, the audit's verdict, `current` and `pending`, and drift (the run view's freshness changes) | the generated invariant (`tests/v2/test_observer_agreement.py`): the precedence over the owners' answers (the real host mediator's form, `GetRun`'s audit, status and freshness) for every variant, and every value reached; `known-patterns.test.mjs` refuses a re-derivation in the UI |
| `ask` (per active run): `approve_configuration` · `approve_revision` (any governance ask after the first approval, counted exactly: 5.0.0 does not record whether a revision raised a ceiling, so a raise is not a kind) · `decide_claim` · `confirm_source` · `freeze_scope`; `consent_stop` is declared for `decisions` and is no row's ask (no route through the service makes it one) | `core.observer.ask`, from the report's first human requirement (`report_requirements`): the configuration while it waits on its human (`core.governance.human_wait`: the agent has asked with a sized `configure_run`, a human approves this phase, the host that mediated it can show its dialog, and a presentation is still admissible: the configuration is pending, a revision pending or rejected: the dialog is open, was closed unanswered, or was answered no, and the human says how to go on, as the hosts' turn end settles with `human_wait`; the run view publishes the answer as `governance.human_wait` and both hosts' turn ends read it), its batch (`confirmation_batch`), or the terminal stage (`freeze_scope`). A configuration the agent has yet to ask for, or that nobody can be shown, is the agent's (`not_ended`). A stop's consent is asked and answered inside the stop that reaches it; one shown and left without consent is the human's no (`core.confirmation.terminal_case` `declined`): the agent keeps working, nobody is asked | the invariant: `ask` is what `GetRun` presents; `needs_you` exactly when a real host mediator shows a form at the agent's next step (`HostGovernance` at its next `configure_run`, `HostConfirmation` at its next report); `test_observer.HumanPendingActionTests` (each case through the real service, against `governance.human_wait`, the gate's terminal `case` and Claude Code's turn-end `_human_approval_wait`) |
| `asked_at` | `core.observer.ask` names the artifact that recorded the question (the first of the batch's records or decision claims in history order; none for the terminal stage); its time is that write's (ADR-0070). For a configuration, the write whose timeline entry (ADR-0070) first carries the waiting `plan_revision` and `proposal_digest`, its predecessor's entry known not to. Not a dialog's time and not reset by a re-presentation or a restart | `tests/v2/test_observer_facts.py` (`AskedAtTests`) |
| `audits` (detail) | `core.evaluation.audit_verdicts`, the one selector of every verdict, served **newest first** (the declared order: the first is `latest_audit_verdict`, the one that decides the run). Each verdict's `independence` is `core.evaluation.independence_then`: as it stood when recorded (its auditor against the producers of the evidence in its audit child's launch dossier), never today's evidence; `null` when the run no longer holds that dossier | the invariant (`_audits`): every verdict step, in order, at its step's time, the last `GetRun`'s; `OneOwnerTests` (no second scan) |
| `dropped` (detail) | `core.observer.dropped`, whose why is the gate's own rule, `core.evaluation.claim_state_cause` (the gate's `local_claim_state` is its first element) | the invariant (`_dropped`): exactly what `GetArgument` discards, each with the artifact that discards it; a regraph's removals, a contested removed claim only `removed`; `OneOwnerTests` |
| `decisions` (detail) | `core.observer.decisions`: configuration receipts from a dialog (an `auto` approval is nobody's answer; each at the write whose timeline entry first lists it), then each recorded confirmation and each shown item left for later, in history order. Each is attached to what it decided: `claim` (a claim of the run: the one decided, or the one a confirmed source supports), `source` (a confirmed record's `source_ref`) and `plan` (the configuration's plan revision), each null when the ask has none; there is no free-text subject | the invariant (`_decisions`); `DecisionShapeTests`; "a decision is attached to a claim of its run" in `scripts/validate_ui_view.py` |
| `flags`, `facets`, `aggregates`, `needs`, `marks` | `application.observer_index`: counts of published fields only. Every count is of read runs, with `not_read` beside it, and `aggregates.total` is how many runs the aggregates count (a page never sums them); within `since` a fact is counted by its `at`, and one with no time is `untimed`, never inside the window and never zero. A window is snapped down to its whole UTC hour and summed from per-hour buckets (`Tally`, kept in O(changed runs); a query costs O(hours in the window), never O(runs)). `decisions` counts every human answer but `later` from the one owner (`core.observer.decisions`), and `decisions_by` breaks it down by ask (`approve_configuration`, `approve_revision`, `decide_claim`, `confirm_source`, `consent_stop`, `freeze_scope`); the `decided` flag is any such answer | `v2_problems` in `scripts/validate_ui_view.py`; `AggregateTests`, `DecisionCountTests`; the window's counted cost in `test_observer_incremental.py` and `test_observer_complexity.py` |
| `version` (row, detail), `token` (summary, page) | the observer: cache versions of the **published document** (ETag), which holds the times, so they change when a time does. Not plugin digests (ADR-0070); a function of what was read, never of when (no nonce: unchanged damage keeps its version) | `TwoClockInvarianceTests` (the plugin's digests); `test_unchanged_damage_is_read_again_every_poll_and_publishes_the_same_version` |
| `id` | `application.observer_index.short_id`: a function of the run's key alone, unchanged by other runs and across restarts | `PositionTests`; `v2_problems` |
| `marks.place` (where a run sits on its project's island), and the islands' order | `application.observer_index.places`: a timed run's ordinal among its project's timed runs, each attempt one, in start order (`started`, then key), so a run or attempt started later takes the next place and no place already given moves (the map grows at its edge, never reshuffled); a run with no recorded start takes the next place of the island's **outer band**, from `UNTIMED_FROM` (96) by key, so a new timed run moves it only once the timed runs fill the band (then the untimed follow every timed one); the same on a reload and across restarts; `null` until every run of its project is read, so a cold start never moves a place it gave. The islands are the `projects` in their order, `observer_index.island_order`: by each project's first run start (`since`; none after every timed one), then id, so a new project takes the next slot; a consumer lays them out from that order and these places alone, on a fixed lattice that never rescales with a count, never from what it saw first. **Known limitation (5.0.0):** places and slots are recomputed from the runs as they are, never stored: a deleted run, or a run whose time is recovered late (an untimed run whose timeline is repaired, a project whose earliest run is read late in a cold start), may renumber the places (or slots) after it. Deletion is a rare human act and recovery is rare; durable slots would need a new store fact | `PositionTests` (an earlier-id project appends, a new timed run moves no untimed one until the band fills); `v2_problems` |
| `projects[].standings` | `application.observer_index.Tally.standings`: each project's runs by standing, read or not (they sum to its `runs`): the Universe's project card shows them as they come | `v2_problems` |
| `needs` order, and each bucket's order inside a page (`orders`) | `application.observer_index.asked_order`: the oldest question first (`asked_at`; one with no recorded time after every timed one), then key; never grouped by project. Each bucket of a page declares its order once, `observer_index.ORDERS`, published as the summary's `orders` (by standing: the time it reads, `by`, and which comes `first`): the needs-a-human bucket the needs list's (`asked_at`, oldest first), so the summary's `needs` and `/api/runs?standing=needs_human` agree row for row, across pages; every other bucket the most recent activity first (`updated`, newest first); in every bucket an untimed run after every timed one, then key | `NeedsOrderTests` and `BucketOrderTests` (`tests/v2/test_observer_agreement.py`: the list, the walked pages against it, and each bucket's walked pages in its declared order); `v2_problems` (rows in page order) |
| `coverage.pending`, `coverage.reindexing` | the index: a poll given a time budget leaves the runs it has not reached `pending` (never read) or `reindexing` (read before, found changed: shown as last read and counted as such), so `read` grows against `runs`, the total is never false, and a view the observer knows is stale is never served under its current token (no 304 for it); the indexer keeps going while either is not zero | `ProgressiveTests`, `StaleCoverageTests`; `make observer-smoke` |
| `calendar` (the inbox's projects × days cells) | `application.observer_index.calendar`: per project and **civil day in the viewer's IANA time zone** (the request's `tz`, checked by `tz_of` and refused when unknown; default `UTC`; the summary's token names it), how many runs were last updated that day and the most urgent standing among them; a day is the zone's own (23 or 25 hours across a change of offset) | `CalendarTests` (spring forward, fall back, the zone checked); `v2_problems` |
| `marks.window_flags`, and `/api/runs?flag=F&since=S` | `application.observer_index.flagged`: a flag within a window is its event's own time (`failed_audit`, `refuted`, `decided`: the times the aggregates count; `changed_since_audit`, a state, by the run's `updated`), so the strip's numbers, the runs they link to and the Universe's lit marks are the same runs | `WindowFlagTests` |
| A run's short `id` as a request key | `application.observer_index.resolve`: the full key first, else the one run with that short id; a short id several runs share is `AmbiguousId` (the server's `409 {"error": "ambiguous_id"}`), never the first found | `ResolveTests` |
| Run status, claim kinds and states, audit verdicts and independence, freshness states, artifact outcomes, reason codes and messages (`message` and `ended_message`) | `contracts/empirica/v2/public-contract.json` | `scripts/validate_ui_view.py` requires each schema enum to equal its registry list |
| Governance control mode and approval state | `contracts/empirica/v2/state.schema.json` | the same check |
| History step kinds | `plugins/empirica/application/history_records.py` (`HISTORY_KINDS`) | the same check |
| Claim state, blocker, the audit's four facts (`verdict`, `current`, `pending`, `independence`), residuals, freshness | the plugin's evaluation and projection (`core.evaluation`, `core.projection`) | `plugins/empirica/tests/test_observer.py` compares every run with the plugin's own `GetArgument` and `GetRun` |
| What the run still requires, in the report's order, who acts and where (`outstanding`, `next`, `attention`); whether the gate accepts the audit for a claim (`assurance`) | **one pure projection in the core**: `core.evaluation.report_requirements` and `audit_acceptance`, the very functions the convergence report blocks on and the author view lists as its obligations; `core/observer.py` only shapes them | the generated invariant `test_INVARIANT_the_observer_says_what_the_gate_and_author_view_say` (`tests/v2/test_observer_agreement.py`): runs through the real service, each variant compared with the report's own answer and `GetRun` |
| `evidence`, `review`; a decision's `status`; spike `state` | `plugins/empirica/core/observer.py` (`EVIDENCE_KINDS`, `REVIEW_STATES`, `DECISION_STATUSES`, `SPIKE_STATES`: classifications added here, each derived from the plugin's own evidence and freshness) | the enum check, and the oracle tests in `test_observer.py` (agreement with public `GetArgument`/`GetRun`) |
| What an ended run left (`residuals`, `stop`) | the stop's own record: the observation basis the run's last transaction recorded (`application/snapshot.recorded_observations`), never today's files; a frozen scope never stands in for an unmet audit or budget stage | `HistoricalFactsTests`; `_residuals` of the invariant: the stop left what the report required first immediately before it |
| **Everything current about a run, ended or not** (the audit and its currency, each claim's state, review, assurance and blocker, the freshness of its proof, the files that changed since, `now`: every requirement the report would still make of the run as it is now, whatever its terminal status, from `report_requirements`) | the live snapshot, through the same owners as the author view: **two time bases, named**: the recorded one feeds only the at-stop facts above | the invariant's `_current`: observer = `GetRun` and `GetArgument` on the live snapshot for every variant, including a converged run whose files changed after |
| Whether a human is shown the question (`attention`, a human item's `where`, the decision and record cards) | `core.confirmation` (`presentable`, `ASK_WHERE`): the batch's own `prompt_error`, the predicate both host mediators use; an unobserved surface (the host has not yet looked for a dialog) is its own place, `if_available`, never `next_report`: `ASKABLE` (the gate's and turn end's "may look") and `REPRESENTABLE` (explicit mediation's "re-ask") stay distinct, and only `REPRESENTABLE`'s places make `needs_you`; it is separate from the gate's first blocking reason (which is `outstanding[0]` and what an agent-only run names as `next`) | the invariant's `_attention`: `needs_you` exactly when a real host mediator presents a form at the agent's next step (`HostGovernance` on its next `configure_run`, `HostConfirmation` on its next report); for an unknown surface, an unknown compared with the post-observation owner snapshot, with the dialog present and absent |
| Where a session can be found (`session`) | the host: `invocationProvenance.session` in `contracts/empirica/v2/shared-defs.json`, recorded at run start by the Claude and Pi adapters | `test_host_runtime.py`; absent means unknown, never invented |
| When a run's writes happened: the row's `started`, `updated` and `ended`, each step's `at` | the bridge adapter records each successful run-state write's time in a machine-local timeline beside the run's state (`adapters/state/timeline.py`, ADR-0070), never in the state, an artifact or a Git ref; the observer maps each step to the write whose manifest first committed it and publishes a time only when that write's own entry exists and chains to the entry before it, else `null`. Times are labels (unsigned, may skew); nothing is ordered by them except the needs list (oldest question first), each bucket of a page (the summary's `orders`: the needs-a-human bucket oldest question first, every other the most recent activity first) and an island's places (start order), and no file's modification time is read | `plugins/empirica/tests/v2/test_timeline.py` (two clocks give identical runs; each write's entry removed in turn; unwritable, torn and corrupt timelines; generations; concurrent writers); `FilesystemStoreTests` and `TwoClockFilesystemTests` in `test_observer.py` |
| Project name and repository | a repository given for it (`--repo`, matched by the plugin's identity, `adapters/state/identity.project_id`), else the one the bridge recorded for the project when it created a run (`adapters/state/locations.py`); the name is its folder name; the row's `label` is that name, else "Unnamed project", and `since` is when its earliest run started, so a consumer never numbers projects | `adapters/observer/source.py`, `FilesystemStoreTests` and `LocationsTests` in `test_observer.py` |

Page order is standing precedence, then each bucket's declared order (`observer_index.ORDERS`, published as the
summary's `orders`), then key. The needs-a-human bucket takes the needs list's order (`asked_order`: the oldest question
first), so `/api/runs` filtered to that standing pages exactly the summary's `needs`, in its order (the Inbox's "Needs a
human"; `NeedsOrderTests`). Every other bucket is the most recent activity first (`updated`, newest first), so the
Inbox's other sections lead with what moved last (`BucketOrderTests`). In every bucket a run with no recorded time comes
after every timed one. A cursor resumes after the last row it was given, in the
published view it was given in: it carries that view's mark (its `token`'s first digits) and every page carries the
view's `token`. After any change the view is another one, so an older cursor is answered `409` with
`{"error": "stale_cursor", "token": <current>}` and the walk restarts from the first page; no walk skips or repeats a
row (`CursorTests`).

## What a row and a sheet carry

A run's row carries its `goal`, `session`, `attention` (`needs_you` / `not_ended` / `ended`), `next` (the one obligation the row names: who acts, where, the words; null when nothing is owed), `audit` (the four facts),
`progress` (`{required, approved}`) and per-claim `assurance`; the summary carries `coverage` (runs read, unreadable, partial, pending), and a sheet carries every attempt at the run (`attempts`, its rows by generation). A run's detail also carries, from the
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

## What a pass costs (counted; ADR-0065, 2026-10-13 amendment)

N runs, F indexed (workspace, path) pairs, K runs written since the last pass, M bound files whose content changed, R(M)
the runs bound to them:

| Operation | Per pass |
|---|---|
| State and history reads | K |
| Git processes | only for repositories whose refs moved |
| File hashes | M, plus the racily clean files hashed again |
| Projections | K + \|R(M)\| |
| Count updates | 2(K + \|R(M)\|): a run's old contribution out, its new one in (all-time and in its hour buckets); never a recount |
| A `since` window (a request) | the hour buckets it covers: O(hours in the window), never a recount |
| A summary's marks and calendar (a request) | O(N) once per published view, window and zone, then kept |
| `lstat`s | N + F, nothing read |

Counted by an instrumented source and workspace (`test_observer_incremental.py`, `test_observer_fs.py`), and proven
to grow as stated by counting at n and 2n (`test_observer_complexity.py`: 100 and 200 heterogeneous runs in memory,
50 and 100 on the filesystem; in `make check-observer`); the incremental view equals a from-scratch observer after
generated writes and edits.

## Budgets (`make observer-smoke`, a sanity check in `make verify`)

At 1,000 runs of a filesystem store across 20 repositories (bridge-driven runs with spikes over distinct, overlapping
files, ended and active, a share drifted; cloned with valid, loose refs) served as `empirica-observer serve` serves it,
indexed in the background (`test_observer_smoke.py`, timed once):

| Budget | Limit |
|---|---|
| A summary and a 304 (and, on the filesystem, an indexer pass with nothing changed) | ≤ 100 ms |
| One run changed (on the filesystem: the indexer pass that publishes it) | ≤ 300 ms |
| `/api/summary` on the wire (gzip) | ≤ 512 KB |
| A runs page | ≤ 200 rows |
| Search | ≤ 300 ms |
| Cold start | progressive, and always honest about coverage; on the filesystem every summary after the first within 100 ms while it runs |

## Fixtures

`fixtures/` holds the summary, rows, details and scenario table of a golden selection of the runs the real service
builds (`observer_store.FIXTURE_SCENARIOS` of `build_store`: one per standing and per ask kind a human can be shown, one
of them with two attempts; 14 runs), for the docs and the built-in demo. `make contract-fixtures` (`scripts/regen_ui_view_fixtures.py`, the
one generator) writes them here and, byte for byte, to the plugin's built-in demo
(`plugins/empirica/vendor/contracts/empirica-ui-view/v1/fixtures`). Both copies are tracked. `make contract-check` fails
on any byte difference between the copies and on drift from a fresh render. Which values the contract can hold is not
the fixtures' to show: generators derived from these schemas (Hypothesis, `scripts/ui_view_strategies.py`; fast-check,
`plugins/empirica/ui/tests/schema-arbitrary.mjs`) produce every declared enumeration value, each document they make
validates, and the UI renders every generated row and sheet with its standing word from `standing` alone
(`scripts/tests/test_ui_view_generators.py`, `ui/tests/generated.test.mjs`). That the observer says what the core says
is a stateful test of the real service (`tests/v2/test_observer_stateful.py`: random sequences of public actions, the
invariant after every step); the rest of `build_store` serves the tests that need named stores.
