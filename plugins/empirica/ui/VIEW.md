# The view (agreed 2026-10-05)

One page, one view: every project, run and claim in the store, with their relationships, shown at once. Filters
remove what they exclude. Details open in the side panel on click. This file is the specification; the browser
checks hold the view to it (the requirement ids R1–R8 below). When the view and this file disagree, the view is
wrong.

Every value it shows is the plugin's, through the observer's contract
([`contracts/empirica-ui-view/v1`](../../../contracts/empirica-ui-view/v1/README.md)); the vocabularies below
are that contract's enums, listed there and held to the UI by `tests/contract.test.mjs`.

## What administration needs at a glance

| question | answered by |
|---|---|
| What exists, and how is it grouped? | projects → runs → claims |
| What is finished, what is still going, what got stuck? | run state |
| Can I trust what is finished? | audit state |
| What is settled, and what is not? | claim state |
| What stands in the way of finishing? | the claims the plugin names a blocker for |
| How was each claim backed, and did the audit see it? | evidence, review |
| What needs a human decision? | claims of kind "needs decision", which the plugin holds blocked until a human decides (a filter) |
| How does the reasoning fit together? | which claims support which |
| Did it take more than one attempt? | a run's next attempt |

## Where each fact goes

**View**: seen in the picture without clicking. **Filter**: you can remove things by it. **Detail**: the side
panel, on click.

**Project**

| field | view | filter | detail |
|---|---|---|---|
| name: its repository's folder name ("Project n" when no repository given holds it) | region + label | ✓ | |
| folder, number of runs and claims, runs per state | | | ✓ |

**Run**

| field | view | filter | detail |
|---|---|---|---|
| state (the contract's statuses; none when its record cannot be read) | colour + glyph | ✓ | |
| audit (the contract's audit states) | a separate audit symbol on the run | ✓ | |
| diagnostic: why it was not read in full (older format, unreadable, history not found, missing or damaged, files unavailable) | its symbol, in the audit symbol's place | | ✓ |
| claims approved of total ("5/6") | number | | |
| goal | | | ✓ (hover: shortened) |
| author, auditor and its independence, governance | | | ✓ |
| budgets (passes, spawns, audit spawns) | | | ✓ |
| what is left (residuals), audit findings | | | ✓ |
| history (steps: kind and outcome) | | | ✓ |
| last changed | order: newest first | | ✓ |
| the files that changed since its spikes ran | | | ✓ |

**Claim**

| field | view | filter | detail |
|---|---|---|---|
| state: approved, open, blocked, discarded | colour + glyph (approved, the quiet state, has no glyph once a passing audit reviewed it; until then it carries its review's glyph and reads "Approved · No audit yet": CONVENTIONS V3) | ✓ | |
| gating (in the run's scope: its approval gates convergence) | thick outline | | ✓ |
| blocker: the plugin's reason, in its words, and the claim it waits on | | ✓ (blocks convergence) | ✓ |
| kind: needs experiment, needs decision, ordinary | | ✓ | ✓ |
| evidence: spike, research only, none | | ✓ | ✓ |
| review: against the latest audit verdict | | ✓ | ✓ |
| text | | | ✓ (hover: shortened) |
| supporting research, passing spikes, spikes run | | | ✓ |
| commands | | | ✓ (technical) |

Files are not in the view. They are a detail of the run whose spiked files changed.

**Relationships**

| relationship | view |
|---|---|
| project contains run | the project's region encloses the run |
| run contains claim | the claims sit under their run |
| claim supports claim | a line with an arrow |
| run → its next attempt | a dashed line |

Support lines never leave their run, so the only line between runs is "next attempt".

## The picture

```
┌ Filter ──────────────┐ ┌──────────────────────────────────────────────────────────────┐ ┌ Detail ─┐
│ 6 projects · 17 runs │ │ key: each mark and line, with how many are showing           │ │         │
│ · 82 claims          │ │ ╔═ Project 1 ════════════════╗  ╔═ Project 2 ══════════════╗ │ │ clicked │
│ [Clear filters]      │ │ ║ ◆✓ 7/7  ⛨                  ║  ║ ◆✓ 5/5 ⛨   ◆⏸ 1/6 ⛨✗     ║ │ │ thing   │
│ Run state  ☐ …       │ │ ║        ●                   ║  ║   ◎          ◎           ║ │ │         │
│ Audit      ☐ …       │ │ ║    ↗   ↑   ↖               ║  ║  ↗ ↖       ↗ ↑ ↖         ║ │ │         │
│ Project    ▾         │ │ ║  ●     ●     ●             ║  ║ ●   ●     ◎  ●  ◎        ║ │ │         │
│ Claim state ☐ …      │ │ ╚════════════════════════════╝  ║   ◆✓ 3/3 ◀┄┄next┄┄ ◆… 0/3║ │ │         │
│ Claim kind ☐ …       │ │ ╔═ Project 3 ══╗  ╔═ Project 4 ═╗  ╚════════════════════╝ │ │         │
│ Claims that          │ │ ║ ◆⏸ 4/5   ⛨✗ ║  ║ ◆…       ⛨  ║                        │ │         │
│ ☐ block convergence  │ │ ╚══════════════╝  ╚═════════════╝                        │ │         │
└──────────────────────┘ └──────────────────────────────────────────────────────────────┘ └─────────┘
```

Marks: `◆` run (its glyph is its state), `⛨` audit symbol, `●` claim (colour = state), `◎` gating claim (thick
outline), `↑` supports (from the supporting claim to the one it supports), `◀┄┄next┄┄` next attempt (from the
earlier run to the later), `╔═╗` project.

## Requirements (held by `make ui-browser`)

- **R1. Everything at once.** Every project, run and claim in the data is in the view at the same time, on one
  screen: no scrolling, no expanding or collapsing. When there are many, the marks get smaller. A run without
  claims is its run mark alone.
- **R2. Each entity type is distinct.** Project = region with a label, run = diamond, claim = circle. No shape is
  used for two entity types.
- **R3. Relationships are drawn.** Containment by enclosure and placement; every support edge as an arrowed line
  between its two claims; every next attempt as a dashed line between the two runs.
- **R4. The view encodings are exactly the table above**: run state (colour + glyph), the diagnostic's symbol, else
  the audit symbol, approved of total, claim state (colour + glyph), gating (thick outline), runs newest first.
- **R5. Filters remove.** What a filter excludes leaves the view, with every line that touches it. Clearing the
  filters brings everything back.
  - Values within one filter are alternatives (OR); different filters must all hold (AND).
  - A run filter (run state, audit, project) keeps the runs that match; the claims of the other runs go.
  - A claim filter (claim state, kind, evidence, review, blocks convergence) keeps the claims that match; while
    one is set, a run with no claim left goes too.
  - A project with no run left goes.
  - The filter panel says what is showing ("6 projects · 17 runs · 82 claims").
- **R6. Details on click.** Clicking a project, run or claim opens its detail in the side panel (the fields marked
  detail above). Nothing in the view moves. A run's detail comes from the observer when it is selected.
- **R7. No files in the view.**
- **R8. The standing rules hold**: CONVENTIONS.md (tokens, copy, no colour alone, keyboard and focus, no page
  errors).

## How it is built (2026-10-05; on the observer 2026-10-06)

Decisions taken while building, each held by a check:

- **Where things go** (`elements/core/view-layout.js`): a run's claims are a tree under it (the root on top,
  supporting claims below); runs flow in rows inside their project, newest first; projects flow in rows. Of the
  row widths it tries, the layout keeps the one that lets the picture be largest in the space, and the picture
  (SVG) scales as a whole, so nothing scrolls (R1).
- **The panel stays open** on a wide screen and says what to click while nothing is selected, so a click only
  changes what it shows (R6). Where the filters, the view and the panel do not fit side by side, the panel opens
  only for a selection and takes the width; Esc or close brings the view back.
- **Filters** are declared in `catalogue.json` (`filters`) and applied by `app/filter.js`. A field filter offers
  the values present in the data, as chips that wrap, so the column fits beside the view without scrolling
  (checked down to 1280 × 720). The flag filter "blocks convergence" keeps the claims whose `blocker` is set.
- **A run's count is its own**: "5/6" stays 5/6 while a filter hides some of its claims.
- **Next attempt** comes from the contract's `previous` field: the observer gives a run the key of the same run's
  previous generation.
- **The store changes under it**: the app asks the observer every `poll_ms` (catalogue.json) whether the store
  changed (an ETag, so an unchanged store costs one 304) and redraws only then. A selection that is unchanged keeps
  its panel as it is; one whose run changed is redrawn; one that left the store goes. An unreachable observer is
  said in the header, and the view stays.
- **The key** above the view draws each mark and line with the view's own drawing code, with how many are showing.
- **The URL** keeps the data source, the filters and the selection; a link in the panel selects in the view.
- **Keyboard**: the view is one tab stop; arrow keys and Home/End move over the marks in reading order; Enter or
  Space opens; the focused or selected mark has a ring. Hover or focus shows a hint of at most 60 characters.
