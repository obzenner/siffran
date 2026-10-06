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
| label: its repository's folder name, else "Unnamed project" (the observer's, the same wherever the project stands in the view) | region + label | ✓ | |
| folder, first activity (what tells unnamed projects apart), number of runs and claims, runs per state | | | ✓ |

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
| what is left (residuals), audit findings: each in the plugin's words and, where it is about a claim, a link to that claim by its text | | | ✓ |
| what waits for the human (ADR-0067): the claims whose research or decision awaits a confirmation, titled by where the confirmation goes | | | ✓ |
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
| sources: each research record, with how it is verified (checked against the file, confirmed or rejected by the human, or pending and where it goes: the auditor, the human, nobody on this host); the quote, the kind of source, why no spike could establish an observation, and the human's note, on click | | | ✓ |
| decision (a `needs-decision` claim): approved or rejected with the answer, or waiting and where | | | ✓ |
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

- **R1. Everything at once.** Every project, run and claim in the data is in the view at the same time: no
  expanding or collapsing, and no scrolling while the picture fits at its smallest scale. When there are many, the
  marks get smaller, down to 8 px; past that the picture scrolls inside its box rather than become unreadable
  (Balance below). A run without claims is its run mark alone.
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

## Navigation

The selection is one value (`app/route.js`): `none | project | run | claim | step | file`, where claim, step and
file are children of a run. It lives in the URL (`project=`, or `run=` with at most one of `claim=`, `step=`,
`file=`) next to the data source and the filters, and `navigate` in `app/app.js` is the only thing that changes it.
A disclosure such as "Technical details" is not a level. `catalogue.json` `route` declares the levels, the parent of
each, and every edge between them; `make ui-browser` takes each edge in a fresh page by keyboard and holds it to:

- **One entry per move.** A move the user makes (a mark, a link, a breadcrumb, Esc, the close button) adds exactly
  one history entry, unless it changes nothing. A poll, a restore or a correction (a step that is gone) replaces the
  current one. Back and Forward return to the panel and the filters of that entry; a reload restores the exact state.
- **Links keep the filters.** Every link in the panel names the selection and the filters in force; one to something
  the filters hide clears them, because following it must show its target.
- **A way up.** Below the top level the panel opens with a breadcrumb of human labels (`atlas › Active · 3 of 8 › Step 2 · Graph`,
  `… › Claim`, `… › module.py`: a run by its state and progress, a step by its number and kind, a file by its name):
  ancestors are links, the last is the page you are on. The close button leaves the
  whole selection.
- **Esc leaves one layer.** A hint, then an open key, then up one level (step, claim or file → run → project → none),
  then the panel itself; an Esc already handled, or made in a select, is left alone (`elements/core/dismiss.js`).
- **Focus goes back to what was left**, found by data attributes, not by node: the claim's mark after Esc from a claim,
  the project's after the last Esc, and, from a step or a file (which have no mark), the mark of the nearest level
  above them that has one: their run (`catalogue.json` `route.marked`); where the picture is hidden behind a full-width panel, the panel's title.
- **Lines are not navigable.** Support and next-attempt lines take no pointer, focus or role; a next-attempt line runs
  clear of the marks and the project's label (down the margin of a stacked pair).

## Balance

Measured in screen pixels, at 1440 × 900, 1280 × 720, 1920 × 1080, 1024 × 768 and 800 × 600, on the demo data, a
dense store (about 60 runs) and a single run (`tests/browser.layout.mjs`):

- **The picture** scales within bounds (`catalogue.json` `picture`): a mark at least 8 px, text 11 to 16 px (text is
  sized apart from the topology). The scale is the largest that fits the picture's box, capped only where the
  labels reach 16 px; marks grow with the text, so the picture covers at least 60% of its area's width or height
  unless that cap binds, every mark has a transparent target of at least 24 px. Only at the smallest scale
  does it scroll, inside its own box.
- **The key** is one line (the totals per group); a click opens the full key as a layer over the picture, so it never
  takes more than a fifth of the view, and Esc closes it.
- **Columns**: from 1440 px a 16rem filter sidebar, then the picture, then the panel at `clamp(20rem, 31vw, 28rem)`;
  from 1024 to 1439 the filters fold into one line ("Filters · 2 set") above the picture and the panel stays beside
  it only while the picture keeps 40rem; below that the detail takes the full width. The sidebar never scrolls.
  Breakpoints are tokens.
- **The panel fills its column** (it is a flex item told to): only a `<pre>` scrolls sideways, Close sits at its inner
  right edge, and facts are a grid of `minmax(7rem, 1fr) 2fr`, for every kind of detail.
- **Hints** are anchored to a proxy box set to the target's rectangle (an SVG element is not a reliable anchor), at any
  pixel ratio and scroll position.
- **Two cues that read apart**: focus is the page-wide 2 px solid outline in the `ring` colour, offset 2 px (`styles/app.css`);
  selection is drawn inside the mark, a filled backing with a solid outline in the text colour (`elements/core/marks.js`). A
  mark that is both focused and selected shows both. The separator also answers Home, End, PageUp and PageDown and says
  its width.

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
  Space opens; the focused mark has the focus outline and the selected mark its filled backing and outline. Hover or focus shows a hint of at most 60 characters.
