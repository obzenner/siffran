# The Empirica observer UI

The one document for the UI. Why it exists and what it may rely on: ADR-0065. What it reads: the contract
[`empirica-ui-view/v1`](../../../contracts/empirica-ui-view/v1/README.md) (there is no other).

## What it is

A read-only page for people, served on 127.0.0.1 by the plugin's observer (`adapters/observer/server.py`). It never
writes, needs no network (no CDN, no runtime build) and reads one contract. Decisions happen in your Claude Code or Pi
session; the page says so in its header.

## Who owns what

| Fact | Owner |
|---|---|
| Trust, scope, obligations, next steps, reason text, currency, governance, a run's immutable **spike backend**, its **standing** and what it **asks** | the plugin's **core** |
| Store reading, coverage, project identity and labels, attempt families, the facets and their counts, the search, the calendar's cells (counted by civil day in the viewer's IANA time zone, the `tz` the UI sends), which runs carry a flag within a window (by the event's own time) | the **observer read model** |
| Where a session can be found (host and locator) | the **host**, recorded at StartRun |
| When a run's writes happened (`started`, `updated`, `ended`, each step's `at`) | the **bridge**, which records each write's time beside the run (ADR-0070); the observer publishes a time only for a write whose own entry exists, else `null`. Labels only: the UI renders times and orders nothing by them |
| Words, layout, navigation, which days the calendar shows (the viewer's last civil days), windowing a long list | the **UI** |

The UI decides no domain fact. `tests/known-patterns.test.mjs` is a regression guard against derivations earlier versions
took in JavaScript (it proves nothing about new ones; `plugins/empirica/tests/v2/test_observer_agreement.py` proves the
observer says what the gate says). A sentence the core or the registry words (a reason, a blocker, a finding) is shown as
given; it is not in `copy/en.json`. **The UI shows only facts the contract serves**: what the contract cannot serve is not
drawn (see the future list in the research notes), never faked.

## The screen

Three panes: the **rail**, the **main** area and the **sheet**. The header's switch is **Universe ⇄ Inbox**: the same place
on the other side (the zoom, the highlight and the open run are kept, and Back restores either side). The data menu names
each source as the server says it serves it (`/api/about`: your store, a generated demo store, the fixture runs).

- **Universe** (the home, `app/universe.js` for the layout, a pure function of one summary, `app/geometry.js` for every
  place, size and box, `elements/organisms/ev-universe.js` only drawing what they return): every run a mark on one canvas,
  each project an **island**, in C's ("atlas") look. **Marks on a sunflower:** a mark's place is the observer's
  (`marks.place`: a timed run's ordinal in its project's start order, an untimed run's a place of the outer band from 96;
  null until the project is read in full, and then not drawn), and place k sits at `mark_c`·√(k+½) at angle k·φ (the
  golden angle) from the island's centre, so the oldest is at the centre and any two places are at least 1.546·`mark_c`
  apart (a mark is drawn under half that). One fixed lattice (`latticePlace`): a place depends on its number alone, so an
  island grows and never rescales; past `slot_places` later places fall between the first ones, inside the slot. An
  island is `mark_c`·√n plus `island_margin` across half, n its project's runs (the observer's count) or its furthest
  place, up to `slot_places`. **Islands in slots:** island k (the observer's project order: by its first run's start,
  then id, so a new project appends) sits in slot k (`islandSlots`): equal discs of the
  smallest of `slot_tiers` that holds the largest island (`slotTier`), with the band under them for a name (`label_world`),
  packed `island_gap` apart at the free place nearest the centre (`packCircles`, an ellipse `pack_aspect` wide). For one
  tier slot k depends on k alone, so the same summary lays out the same on every load and reload, a later one moves
  nothing an earlier one held, and no two islands come closer than `island_gap` (UI2-spec §5); when the largest island
  crosses a tier the same arrangement spreads wider, once. A store of a few small islands is therefore packed close and
  the store's camera (at most `island_scale_max`, as an island's) shows them centred and large. **Known limitation (6.0.0):** the observer recomputes places and slots from the runs as
  they are and stores none, so a deleted run, or a run whose time is recovered late, may renumber the places (or slots)
  after it, and the marks there move once (contract README, `marks.place`). The look: each island a soft
  glow of its own hue (`island-1`…`island-8`, tokens) inside a dashed shore, its **name** bold in that hue with "N runs ·
  K need you" under it (the observer's `projects[].runs` and `.needs`); small dense dots of each standing's colour at the
  store (`dot_share`), each mark its standing's **shape** from an island's zoom on (`states.json` standing.shape, outlined
  like a sticker, a failed audit or refutation a red dot). Human is gold; a stopped run is grey. **Semantic zoom**, in the
  URL: the store, an island (`zoom=`), a run (`run=`: its claims in **orbit** on their support edges, the run in the middle;
  its proof in the sheet). A run's zoom is its own scene: the islands and every other mark fade to a background
  (`graph_dim`), the run is drawn large with its standing's word under it, its claims are a node-link graph around it
  (`claimGraph`: rings by depth, each claim its state's shape, labelled where a label fits clear, the `SupportedBy` edges
  as lines), and its attempts a short chain under it (`attemptChain`, the only relation between runs 6.0.0 records); the
  sheet's Claims section draws the same graph small. An island's zoom shows its **card** top left (name, repository, runs,
  and its runs by standing: the observer's `projects[].standings`). A disc zooms; an island's **name** is a link to the
  work view of its project. **The camera** fills the part no bar, key or card covers with `fit_margin_px` to spare
  (`fitView`, `viewInset`), at most `island_scale_max` px a world unit on an island; it flies only when the place changes
  (a navigation), and a load, a reload, a new summary or a resize puts it there at once. **Callouts** (an amber outline, the ask and the host): at most `callouts` (three) runs that need a human, chosen across the islands (each
  island's most urgent first, the islands in turn: `callouts`), each in the nearest free box around its mark
  (`placeCallouts` over `calloutBoxes`: the free box with the shortest leader, inside the view, never on an island's disc,
  another callout, a mark, an island's name or the key; its leader crosses no name, no callout, no other leader and no
  other island's disc, is at most `callout_reach` times its island's radius long and leaves its own island the short way,
  inside it at most `callout_slack` times the shortest way out; each island's runs nearest its rim are tried first,
  `rimFirst`; a run with no such box gets no callout and the next takes its turn), then "+N more need a human" (the observer's count less those drawn, centred in the top bar). A
  pulse ring is drawn under the marks and stops short of every name (`pulseRadius`). **The bars:** the breadcrumb (All N
  runs › project › run) top left, the gold "N need you" **pill** top right (a link to all of them); at the bottom the
  **period** chips, then the **trophy chips** (a failed audit, refuted, a human decided, changed since the audit: outlined,
  their words muted, numbers from the observer's `aggregates`) that light the runs holding the fact and dim the rest (in the Inbox the same
  facts are filters). **The key** bottom right says each standing's shape in words with the observer's count; it starts folded to
  a small Key button in the bottom bar, which opens it, and its × folds it again (in memory only, never in the URL). `Esc` steps out. Motion is for
  live state and re-orientation only: the camera flies at most `--duration-fly` (480 ms, `flyCamera`: the scale eased on a
  log scale), a run that needs a human pulses (at an island's zoom, and under each callout), an audit-pending ring turns;
  everything is still under `prefers-reduced-motion` (the element says which in `data-motion`). The canvas has no words of
  its own: the overlay (names, callouts, orbit, key, chips) is HTML, and an accessible **mirror** lists the canvas's items
  as links (every island; at an island its runs, up to `mirror_cap`, then "All N runs in the list").

- **Rail** (the Inbox's): Everything, one bucket per standing the store has runs in (each with its count), then the
  projects by name. Each is a link to the work view filtered by it. The counts are the summary's.
- **Inbox** (`view=inbox`): the **value strip** (what the period held: runs, the observer's `aggregates.total`, human decisions, failed
  audits, claims refuted; each number a link to the runs it counts, in the same window; 7 days, 30 days or all time; one quiet
  line says the audits, the decisions by kind, and the runs not read in full or with no recorded time, which are in no count), the **calendar** (projects × the viewer's last civil days; its cells are the observer's `calendar`, counted by civil day
  in the viewer's time zone, so a 23- or 25-hour day is one day; a cell takes the colour of the
  most urgent standing among its runs and a day that needs a human is also taller; it is a link to that project's runs since
  that day's first moment (the observer's `start`); its count of runs with no time is the strip's, the observer's `aggregates.untimed_runs`: a run not read in full
  has no time either and is counted as not read), then the **groups**: runs by standing, **Needs a human** first, each capped with *Show all*.
- **Work view:** one line per run, filtered and counted by the observer, drawn a window at a time (never more than
  `max_rows` row nodes, whatever the list holds) and read in pages. Chips say which filters made the list and remove them.
  A row: the standing glyph (its word on hover; **Aa** or `?` puts a word beside every glyph), the goal, what a human is asked
  and in which session (only for a run filed under Needs a human; its id's first `session_short` characters, the whole id
  on hover and in the sheet), an attempt after the first ("Attempt 2"), the project and
  the time.
- **Sheet** (beside the list or the map, over it below 64 rem; a divider between them, `ev-divider`, drags, moves with the
  arrow, Page and Home/End keys, resets on a double-click and is a separator saying the width; the width is remembered and
  clamped between `width.sheet-min` and what leaves the main area `width.main-min`, below both the sheet is the whole width,
  and the map refits beside it): one line of where the run is (project, attempt, claims, last
  change; the repository and the full time on hover), the session to copy, then its immutable spike capability (`unavailable`, or
  `docker-rust-v1` with abbreviated qualification and execution digests and an explicit "not live health" note), then **Now** or **Outcome** (what is owed, in the order the report asks it,
  or that the run ended, its scope, what it left unresolved *as recorded when it stopped* and, only if the files changed,
  **Unresolved against the workspace now**); **Audit** (every verdict, newest first as the observer serves them, with its auditor and how
  independent it was; whether the latest still covers the argument is said on it, once); **Claims** as the support graph (the root, each claim's supporters nested
  beneath it, a claim that supports several named again where it recurs), each one line that opens in place to its sources and
  quotes, the human's answer, each spike's command, result and bound files, review and independence, and the decisions made on
  it; **Dropped claims** with the gate's own reason; **Human decisions**, each attached to what it decided (the claim, a link to
  it; the source; or the plan); **Attempts** side by side; **History** with the files
  that changed since; **Governance and audit**.
- Unread data is unknown, not zero: "Not read in full: n", "Claims not read", "Claim text of n unread runs was not
  searched". Proposed ceilings show only while a proposal waits on the human.

## The URL

`?data=store|demo&view=inbox|work&zoom=<project>&hl=<flag>&standing=&ask=&project=&host=&mode=&audit=&flag=&period=7|30|all&since=&q=&run=<id>&claim=<id>&words=1`
(no `view`: the Universe).
The place is the URL (`app/route.js`): defaults are left out. A person's move adds a history entry; Back, Forward and reload
restore it. The period is the strip's window only; a list is windowed only by a chosen day (`since`), so the rail and the
list count the same runs.

## Keyboard

`j`/`k` the next and previous row (focus follows), `Enter` opens it, `/` the search, `g` then `n` what needs a human, `?`
words beside every glyph, `Esc` closes the claim, then the run, then (in the Universe) the island. On the map's canvas the
arrows move to the nearest island, mark or claim in their direction (a live region says it in words), `Enter` or `+` opens or
zooms in, `−` steps out. Typing in a field is never a command (`app/keys.js`).

## Words, states and icons

- Nothing past or future names the viewer: "a human", never "you". The ask is the core's `who` and `where`, once.
- A state is a **glyph and its word** (`copy/en.json`, `<domain>.<value>.label`); in a list the word is the glyph's title and
  its name for screen readers, and the person can ask for it beside every glyph. Glyphs carry motion only for states that are
  not over (a run still asking breathes), and only when the person accepts motion.
- `states.json` gives each contract value a tone, an icon and a copy prefix. **The value lists are generated**:
  `enums.json` comes from the contract's schemas (`make ui-enums`); `tests/contract.test.mjs` regenerates it and fails
  on drift, and holds every vocabulary in `states.json` equal to its list.
- Every user-visible string is in the copy catalogue (`t()`); `scripts/audit.mjs` fails on literal text, unused or
  missing keys, undeclared dynamic families and placeholders not supplied; it also keeps tokens, raw values and
  navigation honest.

## Tokens, styles, vendor

Raw values (colours, sizes, durations) live only in `tokens/tokens.json` (`make ui-tokens`); Tailwind generates only
token utilities (`make ui-css`); no arbitrary values, no inline styles, no class glued to an interpolation. Basecoat,
fonts, a Lucide subset and idiomorph's ES module (0BSD, its LICENSE beside it) are vendored (`make ui-vendor`, versions and
SHA-256 in `vendor/VERSIONS`) and `dist/` is committed, so the UI runs offline without Node.
Colour pairs are checked for contrast in both schemes (`make ui-contrast`).
The type is thin and small (V2): IBM Plex Mono Light (300) for the body, 400 and 500 for emphasis and nothing heavier
(`weight` tokens, also emitted as runtime variables so Basecoat's components follow, and its text scale is mapped onto
ours in `styles/app.css`); UI text 11–12 px, small text 10 px, an island's name and its sub-line 9 px (the floor). The header's brand is the favicon's own file
(`app/favicon.svg`, the page's icon link) and the word `app.brand`.

## Elements and layers

Light-DOM custom elements, one per file: `atoms/` (`ev-meter`, `ev-more`, `ev-copy`, `ev-divider`), `organisms/` (`ev-rail`, `ev-inbox`,
`ev-work`, `ev-sheet`), `views/` (pure HTML builders the elements share, tested on their own from the real-service
fixtures), `core/` (shared helpers; `tree.js`). A state is a string builder (`stateHtml`), not an element: a list draws
hundreds. An element redraws on `.data` by morphing (`morph` in core/element.js, idiomorph): nothing at all when its HTML
is the one it drew last, else only the nodes that differ, so a poll that changed one count changes one text node, and
every node that stays is the same node (focus, scroll and a child's own state kept). A child custom element is kept and
takes only its new attributes (it redraws itself); the `style` attribute is the CSSOM's (`setStyle`, set only where it
differs), never markup's. If focus was lost anyway it goes back to the equivalent control. Only a host emptied on
purpose or a scaffold drawn once is written with `innerHTML`; the `poll-stable` journey holds the rest.

## Freshness and errors

`app/client.js` asks `/api/summary` with its ETag (304 when unchanged), pages `/api/runs` and `/api/search` with a cursor,
and `/api/run?key=` for a run's sheet (its detail, its own row and every attempt's row, one request), and reports every
outcome as a value. A cursor from an older published view is refused (409); `app/list.js` starts the walk again as far as it
had read and swaps the rows in whole, so none is missed or doubled. `app/poll.js` asks every `poll_ms`, **not while the tab
is hidden** (it asks at once when the tab is shown), and after each failed answer twice as late up to a cap. The header shows
when the observer last answered. A sheet that failed is said, asked again with backoff (`app/sheets.js`), and always by **Retry**.

## Accessibility and safety

Words, keyboard, visible focus (one solid outline), headings and landmarks, native `details` for claims, `prefers-reduced-motion`
off for every animation. All store text goes through `esc`; the page's CSP allows only its own origin; the server serves GET
and HEAD only, from an allowlist.

## Checks

| Command | What it holds |
|---|---|
| `make ui-test` | while building: `vitest related --run` on `FILES=app/x.js,…` or on the UI files changed in the worktree: the tests that import them, directly or through another module, computed from the imports (`scripts/imports.mjs`), never declared; then the journeys that also hold them (`tests/journeys.json`, the one list kept by hand: a journey loads the page, which no import shows). `tests/imports.test.mjs` is the guard: every module under `app/` and `elements/` is imported by at least one Vitest test, every `tests/*.journeys.mjs` is declared, no helper in `tests/` is orphaned |
| `make mutate` | mutation testing of the changed lines (Stryker on its Vitest runner: each mutant runs only the tests that cover it among those that import its file; incremental, `reports/stryker-incremental.json`, local and gitignored). Each surviving or uncovered mutant is a line `file:line  <original> → <mutant>  survived (tests ran: N)` or `… no test covers it`: write the test that notices it |
| `make check-ui` | `scripts/audit.mjs` and every unit test under Vitest (`vitest run`, `vitest.config.mjs`: no watch, no coverage, fast-check from one fixed seed, `tests/setup.mjs`): audit negative controls, contract and enum equality, the client, the poller, the list walk and window, the sheets, the keys, the routes, the views built from the real-service fixtures (the claims as their support graph, decisions attached to what they decide, no core sentence echoed), the **word budget** (`tests/word-budget.json`: the Inbox and the sheets at the fixtures may not grow more than 10%), the Universe's geometry (`tests/geometry.test.mjs`: seeded fast-check properties of every function in `app/geometry.js`: the sunflower's least gap, packing append-only and apart, fit-to-view inside the inset, names and callouts clear, leaders crossing no disc, name, callout or other leader, the camera's inverse and flight; `tests/universe.test.mjs`: the layout's append-only memory, the callouts' choice, the mirror, one shape per standing), the rows (`tests/rows.test.mjs`), the no-domain-decision patterns, and in happy-dom the custom elements (`tests/elements.test.mjs`: what each atom and organism shows from the fixtures; the Universe by what it tells a recording canvas to draw, checked against `app/geometry.js`'s own places, sizes and camera, `tests/dom.mjs`) and the page booted on `app/index.html` against a fake observer (`tests/app.test.mjs`: what it asks, what each view shows, the keys, the poll); the journeys stay the proof of real rendering |
| `make ui-browser` | headless Chrome journeys, each once at fixture size (the demo, a live store the real service built, the empty store): what needs me, narrowing and the numbers that go with it, one run's proof, why it stopped, the attempts, search, the keyboard, history, safe text, recovery, hidden tab, motion, the narrow layout, and journeys 1–8 through the **Universe**, each with a click budget, its mirror, keyboard, motion (and none under reduced motion) and Back/Forward across the switch; then the layout invariants in light and dark at 1440×900 and 1024×768: nothing scrolls sideways, no overlay box (callouts, island names, chips, the bars, the key) covers another, no heading, chip or name is clipped without its full title, focus stays in the window, and alignment: a list's rows share their left and right edges and their glyph, goal, project and time columns, a row's parts share one middle line, a bar's chips one line; the Universe's leaders cross no name or callout, each island's name says the observer's runs and needs, the key hides and returns, the bars line up |
| `make ui-smoke` | one generated store of 1 000 runs (in `make verify`): the first render within a second of the summary, ≤ 1,500 DOM nodes at the store's and an island's zoom, a zoom's frames ≤ 16 ms on average, the list's bounded row nodes and reachable end, the calendar of many project-days, a refused cursor |
| `make ui-shots` | ten deterministic PNGs of the demo for a person to look at (frozen clock, reduced motion, light and dark), written outside the repository; nothing compares pixels |
| `make check-ui` (generated) | `tests/generated.test.mjs`: every row and sheet the contract's schemas allow (fast-check arbitraries from `tests/schema-arbitrary.mjs`, fixed seed) renders without a hole or missing copy, its standing word from `standing` alone; every declared enumeration value is produced |
| `make ui-contrast` | WCAG contrast of every colour pair |
| `make ui-enums` | rewrite `enums.json` from the contract |
| `make ui-demo N=1000` | serve a generated store of N runs (runs driven through the real service with seeded, varied goals, claims and projects, copied and shifted in time so that none is after now) and print the URL |
