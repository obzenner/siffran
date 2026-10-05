# Empirica UI conventions (draft v0, evolving)

> **Status: draft.** We change it as we learn. Every rule has an id (`T1`, `E3`, …) so that reviews, lint
> messages and commits can cite it. Each rule is marked **[enforced: tool]** or **[review]**. A rule that can be
> checked by a machine must be.
>
> **Enforcement today** (targets of the repository's root Makefile):
> - `make check-ui`, part of `make check`, runs `scripts/audit.mjs` (its limits listed at the top of the file:
>   copy keys — unused, missing, and dynamic families that must be declared in `catalogue.json` —, literal text in
>   markup, icon meanings, inline styles, class values glued to an interpolation, and that every element appears in
>   the gallery) and the node tests: each audit rule's negative control, the maintained graph layout, the filters,
>   the data client, and the coverage of every presentation map by the observer contract (S1, X3).
> - `make ui-browser` (headless Chrome, against the observer's own server) checks focus, the panel, run identity,
>   hints, polling and missing values on the gallery and the app, and holds the view to its specification
>   (`VIEW.md`, requirements R1–R8). It is part of `make ui-build`.
> - `make ui-contrast` checks every colour pair, including the view's graphical marks.
> - Any other **[enforced]** mark below (ESLint, Stylelint, manifest, per-enum gallery coverage, axe-core,
>   snapshots, the no-JS fallback) is **planned** for `make ui-check` and is not wired yet.

## 0. Principles

1. **One source of truth per fact.**
   - The plugin owns *what is true*: trust, states, currency and integrity, published through its observer
     read model (§7).
   - The tokens own *how it looks*. The copy catalogue owns *what it says*.
   - Nothing is re-implemented in a second place.
2. **Declarations over code.** Tokens, copy, icons and the element catalogue are data files. Code only renders
   them.
3. **Calm by default.** The neutral surface is the norm; colour signals state and nothing else; there is room to
   breathe.
4. **Tiny first, more on click** (progressive disclosure, §5).
5. **No runtime build.** Dev-only Node tools produce committed artefacts. The shipped UI is static files served
   from 127.0.0.1 or opened from disk, with no CDN and no network.
6. **No slop.** No dead code, no unused tokens, classes, copy keys or elements, no commented-out code, and no
   "just in case" options. **[enforced: lint + unused-key check]**

## 1. Layout of the tree

```
plugins/empirica/ui/
  tokens/tokens.json          DTCG source, hand-edited, the ONLY place a colour/size/duration value is written
  tokens/build.mjs            Style Dictionary build: dist/tokens.css (values) + dist/theme.css (Tailwind @theme input)
  styles/app.css              Tailwind utilities + generated theme; element display rules; ev-split grid
                              (Basecoat's vendored CSS/JS are linked by each page, not imported here)
  dist/ui.css                 generated, committed (make ui-css)
  dist/tokens.css             generated, committed (make ui-tokens)
  vendor.json                 declares what is vendored (Basecoat styles/js, font files)
  vendor/                     pinned copies: basecoat/, lucide/, fonts/ (+ VERSIONS file with exact versions + sha256)
  copy/en.json                every user-visible string
  icons.json                  meaning -> lucide icon name (one icon per meaning)
  elements/core/              shared helpers (element.js), the view's marks (marks.js), the pure graph layout
                              (layout.js) and the view's layout (view-layout.js)
  elements/layout/ev-*.js     behavioural layout elements (ev-split)
  elements/atoms/ev-*.js      one element per file
  elements/molecules/ev-*.js
  elements/organisms/ev-*.js
  elements/templates/         the detail-panel wording (details.js), shared by the app and the gallery so they
                              cannot drift
  elements/core/rove.js       the roving tab stop the view uses
  elements/index.js           imports every element; pages load it plus their own page script and JSON
  states.json                 state -> tone, icon, copy key (S1)
  catalogue.json              closed lists the UI iterates over (contract lists, tones, the view's filters, …)
  scripts/                    build and audit scripts (dev-only)
  tests/                      node tests (make check-ui) and the browser checks (make ui-browser)
  custom-elements.json        generated Custom Elements Manifest (make ui-manifest; planned)
  app/                        the app: the one view specified in VIEW.md (app.js wires it, filter.js applies the
                              declared filters, client.js talks to the observer server)
  gallery/index.html          every element x every state, on the observer's demo data
package.json, package-lock.json   dev-only, exact versions (no ^ or ~)
eslint.config.mjs, stylelint.config.mjs
```

The UI ships built: `dist/` and `vendor/` are committed, and the observer serves this folder. The `make` targets
(`make help` lists them) are `ui`, `ui-demo`, `ui-install`, `ui-tokens`, `ui-vendor`, `ui-css`, `ui-contrast`,
`check-ui`, `ui-browser` and `ui-build`; `ui-manifest` and `ui-lint` are planned. CI and agents run make targets.
Nobody runs `npx` by hand. **[enforced: Makefile]**

## 2. Tokens (T)

- **T1.** Tokens use the W3C **Design Tokens (DTCG)** format in `tokens/tokens.json`. It is the only place a
  raw value (hex, rem, ms) may appear. **[enforced: stylelint, eslint no-restricted-syntax on hex/px literals]**
  - Exception: layout-algorithm inputs (the view's mark sizes and gaps, `U` in `core/view-layout.js`) are named
    constants, because the layout runs in JS on numbers; the view's SVG scales them all together. Panel widths are tokens (`width.panel`,
    `panel-min`, `main-min`) that `ev-split` reads at runtime.
- **T2. Three tiers:**
  - **primitive** (`gray.600`, `green.600`), not usable by elements;
  - **semantic** (`surface`, `surface-muted`, `text`, `text-subtle`, `border`, `state-verified`,
    `state-failed`, …);
  - **component**, rarely and only when a semantic token isn't enough (`badge-verified-bg`).

  Elements and pages may use semantic and component tokens only. **[enforced: tailwind theme contains only
  semantic/component tokens]**
- **T3. Tailwind defaults are erased**: `--color-*: initial; --spacing-*: initial; --text-*: initial;
  --radius-*: initial; --shadow-*: initial;`. Only our tokens exist, so an off-palette class does not compile.
  **[enforced: no-unknown-classes]**
- **T4a.** **Use a Basecoat component wherever one exists** (`card`, `badge`, `item`, `btn`, `select`, `tabs`),
  adding only tone classes. This keeps our elements thin and lets the Basecoat style pack (shape, roundness, density)
  apply everywhere. **[review]**
- **T4.** **Basecoat is themed only by mapping its shadcn variables** (`--background`, `--foreground`, `--muted`,
  `--muted-foreground`, `--border`, `--ring`, `--primary`, `--radius`, …) to our semantic tokens. It is never
  forked or overridden by selector. **[review]**
- **T5. Scales are short and named:**
  - **space:** `3xs 2xs xs s m l xl 2xl 3xl`, on a 4 px base;
  - **text:** `xs s m l xl`;
  - **weight:** `regular medium semibold`;
  - **radius:** `s m l full`;
  - **motion:** `fast` (120 ms), `base` (200 ms);
  - **elevation:** `0 1 2`.

  New steps need a reason in the PR. **[review]**
- **T6. Two schemes: `light` and `dark`.** Both are full sets of the same semantic tokens and differ only in
  values. **[enforced: style-dictionary build fails on a missing key]**
- **T7. Contrast is a test, not a hope:**
  - text ≥ 4.5:1 and non-text ≥ 3:1 for every semantic foreground/background pair in both schemes;
  - focus ring ≥ 3:1 against both its neighbours.

  **[enforced: contrast check over tokens.json]**

## 3. Colour and visual language (V)

- **V1. Neutral first.**
  - Surfaces, text, borders and chrome are greys.
  - Colour appears only for **state** (§6) and for one interactive accent (`accent`, for links, focus and the
    current selection).
- **V2. Muted state colours.**
  - State colours are low-saturation, mid-lightness tones, not signal colours.
  - A *fill* is a pale tint; the strong tone is reserved for the glyph and the text label.
  - Bright, saturated fills are not allowed. **[review + token values]**
- **V3. Never colour alone.** Every state is glyph + word (or glyph + tooltip at level 0) + colour. The UI stays
  correct in greyscale. **[planned: gallery greyscale snapshot]**
  - In the view (`VIEW.md`), each domain's one *quiet* state (`quiet: true` in `states.json`, e.g. a supported
    claim) is a plain tinted mark and every other state carries its glyph, so the unusual stands out and greyscale
    stays readable. A claim is quiet only when its state *and* its review are their domains' quiet values
    (`core/count.js` `settled`): an approved claim no passing audit reviewed carries its review's glyph, so nothing
    the author alone vouches for looks final. Unknown is a state of its own (its own glyph and word), never a default. The marks live once in
    `elements/core/marks.js`, and
    the view has a key (`ev-legend`) drawn with those same marks, listing the states present with their counts.
    **[enforced: `make ui-browser`]**
  - One target per meaning: a run mark opens its run, a claim mark its claim, a project's label its project; a
    region only encloses. **[enforced: `make ui-browser`]**
- **V4. Shapes have one meaning each** (`VIEW.md` R2).
  - region with a label = project;
  - diamond = run;
  - circle = claim (a thick outline = gating);
  - the audit icon (`icons.json`) = a run's audit;
  - arrowed line = supports; dashed line = next attempt;
  - square = file (detail only: files are not in the view, R7);
  - small dot = a step in a run's history (detail).
  - The same shape is never reused for something else. **[review; listed in icons.json]**
- **V5. Icons:**
  - Lucide only, copied into `vendor/lucide`.
  - One icon per meaning, declared in `icons.json`; an icon is never used for two meanings.
  - Sizes come from the `icon-s` / `icon-m` tokens. **[enforced: unknown icon names fail lint]**
- **V6. Typography:**
  - One vendored pairing, **IBM Plex Sans + IBM Plex Mono** (`font` in tokens). Mono only for digests, commands and paths.
  - At most three text sizes on one screen. **[review]**
- **V7. Room to breathe:**
  - Spacing comes **only** from layout primitives (§4, `L*`); atoms never carry outer margins.
  - The default gap between unrelated groups is `l` or more. **[enforced: restricted classes m-*/mx-*/... inside
    atoms]**

## 4. Elements: atomic design (E) and layout (L)

Hierarchy (Brad Frost): **atoms → molecules → organisms → templates → pages**.

- **E1. Custom elements, light DOM, `ev-` prefix**, one file per element at the right level. There is no shadow
  DOM, so Tailwind and Basecoat apply and the DOM stays inspectable. **[enforced: manifest + path check]**
- **E2. Inputs:**
  - Attributes for scalars (`state`, `kind`, `count`, `label-key`).
  - The `.data` property for structured input (a claim, a graph).
  - Attribute values come from closed vocabularies, declared as enums in JSDoc, so they land in the manifest.
    **[enforced: manifest enum check]**
- **E3. Rendering is a pure function of inputs.**
  - No `fetch`, no timers, no global state, no reading other elements.
  - Re-render on `attributeChangedCallback` and on a `.data` setter. **[review + eslint no-restricted-globals in
    elements/]**
  - Exceptions: `ev-hint` (the page-level hint service) and `ev-split` (Esc) listen at document level, and both
    remove those listeners on disconnect. `ev-hint` also observes the DOM while shown and uses one short grace
    timer, so the hint is hoverable and hides when its target is removed.
  - Listeners on the element itself are attached once, in the constructor, so reconnecting never duplicates them.
  - A re-render gives focus back to the equivalent control. The control is identified by owner: its identified
    ancestors (e.g. `[data-run]`), then its own `data-*` identity, else its position within that owner. Child
    data is handed over in `populate()`, before focus is restored. Selection and pressed state are patched in
    place rather than re-rendered. **[enforced: make ui-browser]**
- **E4. No trust logic in elements.**
  - An element displays the state it is given and never computes `verified`, `current` or `live`.
  - The only permitted mapping is from vocabulary to presentation (token, icon, copy key).
  - **[review; the main source of past bugs]**
- **E5. Events out, never callbacks in.**
  - Interaction emits `CustomEvent`s (`ev-select`, `ev-open`) that bubble and carry ids.
  - Organisms or pages decide what happens. **[review]**
- **E6. Fallback content.** Every element's light-DOM children contain a readable plain-text fallback, so the page
  still makes sense without JS. **[planned: gallery no-JS snapshot; not met today — pages show a `<noscript>` note]**
- **E7. Literal classes only.**
  - Variants come from a `const` lookup map (`{ verified: "text-state-verified" }`).
  - No string-building of class names. **[enforced: no-concatenated-classes]**
- **E8. Accessibility per element:**
  - a role or a native semantic element;
  - an accessible name from the copy catalogue;
  - keyboard reachable if interactive;
  - visible focus.

  **[enforced: axe-core on the gallery]**
- **E9. Atom budget.** Atoms take ≤ 4 attributes, molecules compose atoms and add only layout, and organisms own
  interaction state such as selection and expansion. An element that outgrows its level moves up. **[review]**

Layout:

- **L1.** Spacing is expressed with token utilities (`gap-*`, `p-*`) on `flex`/`grid` containers inside elements,
  never with margins on atoms. A layout *element* exists only when layout needs behaviour: today that is
  `ev-split` (main + resizable side panel). Static wrappers such as stack or cluster elements are not added, because
  the utilities already say it.
- **L2.** Responsiveness is handled by container queries inside elements, not media queries in pages.
  **[review]**

## 5. Progressive disclosure (D)

Every element declares which levels it supports, in JSDoc, recorded in the manifest.

| Level | What | Interaction | Content rule |
|---|---|---|---|
| **0 glance** | glyph / shape / count | none | no words needed; state readable in greyscale |
| **1 hint** | one line | hover or keyboard focus: a `data-hint` attribute, shown by the page's single `ev-hint` in the top layer (never CSS pseudo-element tooltips, which ancestors clip) | plain words, ≤ 60 characters, no ids or hashes |
| **2 detail** | a short card in the side panel | click / Enter | plain sentences, the 3–6 facts a reviewer acts on |
| **3 technical** | ids, digests, commands, raw JSON | "Technical details" disclosure inside the panel | the only place codes and hashes appear |

- **D1.** Level 0 never shows ids, digests, sequence numbers or abbreviations. **[enforced: copy lint on level-0/1
  keys]**
- **D2.** Detail opens in **one non-modal side panel** (`ev-split`) next to the content, never in a sheet that covers
  or blurs it. The main view stays visible and scrollable, and the divider is draggable (and keyboard-movable), with
  its width remembered and re-clamped when the window changes. The divider is a `separator` with its current,
  minimum and maximum width. A new selection replaces the panel's content and always opens it; Esc or the panel's
  close button closes it, clears the selection, and returns focus to where it was. A centred modal dialog is
  reserved for decisions that block everything else. **[review]**
  - Exception: below `panel-min + main-min` there is no room for both, so the panel takes the full width until it
    is closed. Focus moves into the panel when it does, and returns to the invoker on close.
- **D3.** Clicking always reveals more and never navigates away unexpectedly. Navigation is an explicit link.
  **[review]**

## 6. Copy (C) and the state vocabulary (S)

- **C1.**
  - Every visible string is a key in `copy/en.json`; no literals in elements or pages.
  - The copy catalogue also holds the accessible names and the level-1 hints. **[enforced: lint for string
    literals in render output; unused-key check]**
- **C2. Empirica's vocabulary, unchanged.**
  - Labels use the plugin's own terms: claim, claim graph, research, spike, spike request, audit, auditor, author,
    governance, observation, passes, spawns, audit spawns, needs-experiment / needs-decision / ordinary, and the run
    statuses converged / active / stopped (residual, budget, frozen).
  - The UI never invents synonyms ("test", "review", "plan") for a plugin term.
  - Hints (level 1) may *explain* a term in plain words, but the label stays the term.
  - Raw spellings never reach the screen: no `snake_case` or `kebab-case` enum values, no internal codes (`q`, `S`),
    no field names (`causal_seq`). The copy catalogue maps each enum value to its display form
    ("stopped_residual" → "Stopped · residual").
- **C3.**
  - Write sentences in the reader's terms: "Tested on exactly these bytes", not "binding sha256 equal".
  - Numbers come with units: "step 14 of 61", "2 of 10 passes used".
- **S1.**
  - The **closed state vocabulary is defined by the plugin's observer read model** (§7), not by the UI.
  - The UI maps each state to a token, an icon and a copy key in one table (`states.json`).
  - An unknown state renders as a visible "unknown state" atom, never as a default or as green.
  - **[enforced: tests/contract.test.mjs — every states.json domain and contract list in catalogue.json covers
    exactly one contract enum]**

## 7. Data contract (X)

- **X1. The only data input is the plugin's observer read model**: `application/observer.py`, served by
  `adapters/observer/server.py` under the contract `contracts/empirica-ui-view/v1`. States, blockers, audit
  currency and freshness are computed there, once, by the plugin's own code.
- **X2.**
  - The UI does not read `run.json` or `refs/empirica/*` directly, and judges nothing: it joins, counts, filters
    and words what the observer gives.
  - A reason the plugin explains (a blocker, a residual) is shown in the plugin's own words (the contract's
    messages), never re-worded per code in the UI.
  - Runs are always identified by the composite key `project/run/generation`.
- **X3.** Gallery and test data are the observer's demo fixtures (`vendor/contracts/empirica-ui-view/v1/fixtures`
  in the plugin): runs the plugin itself produced, validated against the contract by `make contract-check`.
  **[enforced: contract-check, check-ui]**
- **X4. Live updates.** The app asks the observer whether the store changed (`/api/view` with an ETag) every
  `poll_ms` and redraws only when it did; a run's detail is fetched when it is selected, once per version. The
  browser never reads the filesystem.

## 8. Gallery and tests (G)

- **G1.** **The gallery is the spec.** Every element in `custom-elements.json` appears in `gallery/index.html` in
  **every** value of every enum attribute, in both schemes, at level 0, plus a level-1 and a level-2 example.
  **[enforced today: every element appears; planned: per-enum coverage from the manifest]**
- **G2.** The checks run on the gallery in headless Chrome:
  - **axe-core:** 0 violations;
  - **visual snapshots:** light, dark, greyscale, no-JS and narrow (360 px).

  **[enforced: ui-check]**
- **G3.** A design change is reviewed on the gallery first and assembled into pages second. **[review]**

## 9. Process

- Changes to tokens, the state vocabulary or the copy tone update this file in the same change.
- Vendored libraries are upgraded only via `make ui-vendor`, which records versions + sha256 in
  `vendor/VERSIONS`. Vendored files are never edited by hand.
- Open questions (to resolve as we iterate):
  1. Exact neutral palette (warm grey vs cool grey). The Basecoat style pack is **nova**, with our own focus
     outline (app.css) because every pack's ring is below 3:1.
  2. Space-scale base (4 px vs 8 px) and whether type should be fluid (Utopia) or fixed.
- The main view is specified in `VIEW.md`; a change to it changes that file first.
