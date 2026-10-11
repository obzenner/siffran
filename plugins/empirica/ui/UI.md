# The Empirica observer UI

The one document for the UI. Why it exists and what it may rely on: ADR-0065. What it reads: the contract
[`empirica-ui-view/v1`](../../../contracts/empirica-ui-view/v1/README.md).

## What it is

A read-only page for people, served on 127.0.0.1 by the plugin's observer (`adapters/observer/server.py`). It never
writes, needs no network (no CDN, no runtime build) and reads one contract. Decisions happen in your Claude Code or Pi
session; the page says so in its header.

## Who owns what

| Fact | Owner |
|---|---|
| Trust, scope, obligations, next steps, reason text, currency, governance | the plugin's **core** |
| Store reading, coverage, project identity and labels, attempt families | the **observer read model** |
| Where a session can be found (host and locator) | the **host**, recorded at StartRun |
| Words, layout, navigation, grouping by a category the core published | the **UI** |

The UI decides no domain fact. `tests/known-patterns.test.mjs` is a regression guard against the derivations earlier versions took in JavaScript (it proves nothing about new ones; `plugins/empirica/tests/v2/test_observer_agreement.py` proves the observer says what the gate says)
(finality, "blocks", a stale pass as a failure, unread as zero, attempt order by time). A sentence the core or the
registry words (a reason, a blocker, a finding) is shown as given; it is not in `copy/en.json`.

## The screen

- **List** (alone, across the width, until a run is selected; then a column beside the workspace, or alone below 60 rem): a header saying the page is read-only, the data source and when the
  observer last answered; a search over goals, claim text, projects and sessions; the runs under the core's
  `attention`: **Needs a human**, **Not ended**, **Ended**, then runs **not read in full**. Not ended is all the core knows;
  liveness is never inferred. A row: goal, project and attempt, outcome, audit, what it owes next (who acts, where,
  the core's words) and the session with a copy button.
- **Workspace** (right, drawn only once a run is selected): **Now** (what is owed, in the order the report asks it: an
  item that waits for the ones before it says "after the steps above") or **Outcome** (that the run ended, its scope,
  what it left unresolved *as recorded when it stopped*, or that the record cannot say; and, only if the files have
  changed since, **Unresolved against the workspace now** under its own heading), kept apart from **Changed since** for
  files; **Claims**, each one line (text, Required or Deferred, state, assurance, blocker) that opens in place to its
  sources and quotes, the human's answer, each spike's command, result, bound files and whether it still holds (the
  output is not kept), review, assurance and independence; **how the claims support each other** (an indented list from the root); governance and audit;
  **Attempts** with previous and next and a side-by-side; **History**.
- Unread data is unknown, not zero: "Not read in full: n", "Claims not read", "Claim text of n unread runs was not
  searched". Proposed ceilings show only while a proposal waits on the human.

## The URL

`?data=store|demo&q=<search>&run=<key>&claim=<id>`. The run and the claim are the only selections. Following a link
adds a history entry; Back, Forward and reload restore both. Esc leaves a claim, then the run; focus goes back to the
claim or the row it came from.

## Words, states and icons

- Nothing past or future names the viewer: "a human", never "you". The ask is the core's `who` and `where`, once.
- The comparison of attempts wraps every cell (a long goal or word included) and scrolls sideways before it lets one attempt's text run into the next. The session fact has a closed disclosure of what its host recorded (interactive, delegated, unknown staying unknown; never whether it is running). A human who may be asked only if the host finds a dialog is told so ("if the host finds a dialog, when the agent next reports") and is not under **Needs a human**. A cell the observer could not fill says **Not read**; one still being asked for says Loading; one that failed says
  it could not be loaded, with Retry. Nothing unknown is shown as none.
- A state is its **word** (`copy/en.json`, `<domain>.<value>.label`); an icon may only repeat a word. There are no
  hints and no glyph-only marks.
- `states.json` gives each contract value a tone, an icon and a copy prefix. **The value lists are generated**:
  `enums.json` comes from the contract's schemas (`make ui-enums`); `tests/contract.test.mjs` regenerates it and fails
  on drift, and holds every vocabulary in `states.json` equal to its list. Words for the lists with no tone (who, where,
  mode, approval, independence, kind) are copy families the audit expands from `enums.json`.
- Every user-visible string is in the copy catalogue (`t()`); `scripts/audit.mjs` fails on literal text, unused or
  missing keys, undeclared dynamic families and placeholders not supplied; it also keeps tokens, raw values and
  navigation honest. It does not police how files import one another or which icon a word uses.

## Tokens, styles, vendor

Raw values (colours, sizes, durations) live only in `tokens/tokens.json` (`make ui-tokens`); Tailwind generates only
token utilities (`make ui-css`); no arbitrary values, no inline styles, no class glued to an interpolation. Basecoat,
fonts and a Lucide subset are vendored (`make ui-vendor`) and `dist/` is committed, so the UI runs offline without Node.
Colour pairs are checked for contrast in both schemes (`make ui-contrast`).

## Elements and layers

Light-DOM custom elements, one per file: `atoms/` (`ev-state`, `ev-meter`, `ev-more`, `ev-copy`), `molecules/`
(`ev-record`), `organisms/` (`ev-runs`, `ev-run`), `templates/` (pure HTML builders shared by the elements and tested
on their own), `core/` (shared helpers; `tree.js`). An element
re-renders on `.data` or an observed attribute and puts focus back on the equivalent control.

## Freshness and errors

`app/client.js` asks `/api/view` with the ETag (304 when unchanged) and `/api/run` once per (run, version), and reports
every outcome as a value. `app/poll.js` asks every `poll_ms`, **not while the tab is hidden** (it asks at once when the
tab is shown), and after each failed answer twice as late up to a cap. The header shows when the observer last
answered. A run's document that failed is said in the workspace and asked again with backoff, and always by **Retry**.

## Accessibility and safety

Words, keyboard (Tab, Enter, Esc), visible focus (one solid outline), headings and landmarks, native `details` for
claims. All store text goes through `esc`; the page's CSP allows only its own origin; the server serves GET and HEAD
only, from an allowlist.

## Checks

| Command | What it holds |
|---|---|
| `make check-ui` | `scripts/audit.mjs` and the node tests: audit negative controls, contract and enum equality, the client, the poller, search, routes, the support tree, templates built from the real-service fixtures, and the no-domain-decision patterns |
| `make ui-browser` | headless Chrome journeys T1–T7 at 1440×900 and 1024×768 on the demo and on a live store (click budgets stated per task), then keyboard and focus, history and reload, safe text, recovery, hidden tab, overflow, the stacked layout and the empty store |
| `make ui-contrast` | WCAG contrast of every colour pair |
| `make ui-enums` | rewrite `enums.json` from the contract |

The operator's tasks the journeys hold the page to:

| Task | Answer on screen | Clicks |
|---|---|---|
| T1 what needs me, and where | the Needs a human group's rows: who acts, where, what, the session | 0 |
| T2 what is owed next | every run's row shows its next step; the run lists each obligation | 0, then 1 |
| T3 the proof behind a claim | sources, quotes, commands, review, assurance, independence | 2 |
| T4 why it stopped, what was left | the Outcome section and the Deferred claims | 1 |
| T5 attempts side by side | attempts 1·2·3, the comparison, previous | 1, then 1 |
| T6 find a remembered goal | search (typing) | 0 |
| T7 same-named projects | distinct labels on the list | 0 |
