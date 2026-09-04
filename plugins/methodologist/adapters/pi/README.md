# `@siffran/methodologist-pi`

Pi ([pi.dev](https://github.com/earendil-works/pi)) adapter for the Methodologist
plugin. It is a host adapter over the host-neutral Methodologist core: it binds
the core's ports to Pi's `ExtensionAPI` and translates Pi invocations into the
`methodologist/v1` contract (ADR-30, ADR-32). It changes no core semantics.

## What it does

| Responsibility (ADR-32) | Pi capability used |
|---|---|
| Register `/think` | `pi.registerCommand("think", …)` |
| Contribute the shared methodology resources | `pi.on("resources_discover")` → `{ skillPaths: [<methodologist/skills>] }` |
| Render phase progress (the `TaskTracker` port) | `ctx.ui.setWidget` — a live phase checklist below the editor |
| Human choice on ambiguity (the `HumanPort` port) | `ctx.ui.select` |

The `/think` invocation is translated to a `SelectMethodology` request; a
`MethodologySelected` response paints the phase widget, and a
`HumanDecisionRequired` response is resolved through `ctx.ui.select` and
re-dispatched as an explicit selection. The adapter never invents a methodology
choice — that judgement stays in the core / the agent.

## Install

This is a standard Pi extension package (a directory with a `package.json`
carrying a `pi` manifest). Point Pi at it:

```bash
mkdir -p ~/.pi/agent/extensions/methodologist
ln -s "$(pwd)/plugins/methodologist/adapters/pi" ~/.pi/agent/extensions/methodologist
```

Pi discovers the extension via the `pi.extensions` entry in `package.json` and
loads `src/index.ts` (TypeScript is resolved by Pi's `jiti`). The package can
also be published to and installed from npm.

## Wiring the core

The adapter reaches the Methodologist core through an injected `dispatch` seam,
so the transport (in-process, subprocess, RPC) is the host's choice and is not
baked in:

```ts
import { createMethodologistExtension } from "@siffran/methodologist-pi";

export default createMethodologistExtension({
  dispatch: myCoreDispatch, // (request: methodologist/v1) => response
  knownMethodologies: ["invariant-analysis", "first-principles", /* … */],
});
```

The **default export** contributes the methodology resources and registers
`/think`, but its `dispatch` throws until a host wires the core — reported to
the user rather than faked (ADR-30: host capability gaps are explicit). One such
gap is deliberate: `HumanPort.ask` (free-text input) has no reliable `ctx.ui`
primitive, so it throws `UnsupportedByHost`; the `methodologist/v1` contract
routes every human decision through `choose` (`ctx.ui.select`) instead.

## No shared or repository state

The adapter holds phase state only in memory for the duration of a turn and
renders it to a widget. It writes nothing under `.pi` or `.claude` and performs
no repository runtime writes.

## Develop

```bash
make methodologist-pi-check   # static/package validation + tests (from repo root)
node --test test/*.test.ts    # tests directly (Node ≥ 22.6, native TS type-strip)
npm run typecheck             # if a TypeScript compiler is installed
```

Tests mock `ExtensionAPI`/`ctx` (see `test/fakes.ts`) and assert against the
shared contract fixture in `contracts/fixtures/`, so a build needs neither the
Pi runtime nor a network install.
