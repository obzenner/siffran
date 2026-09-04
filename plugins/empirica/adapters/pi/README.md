# `@siffran/empirica-pi`

Pi ([pi.dev](https://github.com/earendil-works/pi)) adapter for the Empirica
plugin. It is a host adapter over the host-neutral Empirica core: it maps Pi's
native events into the `empirica/v1` contract (ADR-30, ADR-32) and maps the typed
decision back onto Pi's enforcement and UI. It changes no core semantics — the
convergence rules stay in the core, reached through an injected transport.

## What it does

| Responsibility (ADR-32) | Pi capability used | empirica/v1 request |
|---|---|---|
| Start / resume a run | `pi.registerCommand("empirica", …)` | `StartRun` |
| Report a run's status | `pi.registerCommand("empirica-status", …)` | `GetRun` |
| Convergence gate (command) | `pi.registerCommand("report-convergence", …)` | `EvaluateRun(report_convergence)` |
| **Convergence gate (enforced)** | `pi.on("tool_call")` → `{ block, reason }` | `EvaluateRun(report_convergence)` |
| Best-effort settled nudge | `pi.on("agent_settled")` → `pi.sendMessage` | `EvaluateRun(continue)` |
| Contribute the shared skill | `pi.on("resources_discover")` | — |

### The convergence gate is the trust boundary

A run may report convergence only through `EvaluateRun(intent: "report_convergence")`
(ADR-32). The **enforced** gate is `tool_call` interception: when the model calls
the `report_convergence` tool, the adapter evaluates the run and returns
`{ block: true, reason }` unless the core returns `Allow`. It fails **closed** — a
`Block`, a closed `Fault`, or an unavailable transport all deny the call. Non-gated
tool calls are never round-tripped to the core.

`/report-convergence` is the same check surfaced as a human command.

### `agent_settled` is observational, not a gate

Pi's settled lifecycle cannot veto completion (ADR-32). At `agent_settled` the
adapter evaluates the run and, if it is active with outstanding work, enqueues a
**best-effort** follow-up via `pi.sendMessage({ deliverAs: "followUp" })`. The
message names itself a reminder, never blocks, and never throws. An agent can end
without invoking the report tool; the run then remains explicitly active and
non-converged — never silently certified.

## Install

A standard Pi extension package (a directory with a `package.json` carrying a
`pi` manifest). Point Pi at it:

```bash
mkdir -p ~/.pi/agent/extensions/empirica
ln -s "$(pwd)/plugins/empirica/adapters/pi" ~/.pi/agent/extensions/empirica
```

Pi discovers the extension via the `pi.extensions` entry and loads `src/index.ts`
(TypeScript resolved by Pi's `jiti`). The package can also be published to npm.

## Wiring the core (the transport)

The adapter reaches the core through an injected `dispatch` seam, so the transport
is the host's choice and is not baked into the domain logic:

```ts
import { createEmpiricaExtension } from "@siffran/empirica-pi";

export default createEmpiricaExtension({
  dispatch: myCoreDispatch, // (request: empirica/v1) => response
});
```

The **default export** wires the production transport: a JSON stdio bridge
(`stdio-transport.ts` → `bridge.py`) that runs the host-neutral
`application.EmpiricaService` against the real persistence adapters. Operational
state lives only under `$EMPIRICA_HOME` (default `~/.empirica-plugin`, ADR-31) and
knowledge artifacts under Git shadow refs; the TypeScript carries **no** domain
rules, run identity, or persistence — it moves JSON and obeys the decision.

## No shared or repository state from the adapter

The adapter's only per-session state is the active run's opaque handle, held in
memory. It writes nothing under `.pi`, `.claude`, or the working tree — all state
goes through the bridge to `~/.empirica-plugin` and Git.

## Spike-pending (ADR-32)

Two Pi behaviours are usability mechanisms until an executable spike confirms
them, and are **not** claimed as trust guarantees:

- whether a blocked-tool `reason` is surfaced to the model's context, and
- whether an `agent_settled` follow-up reliably starts another turn.

The enforceable guarantee that *is* claimed: no successful `report_convergence`
tool call without an `Allow` decision.

## Develop

```bash
make empirica-pi-check        # static/package validation + tests (from repo root)
node --test test/*.test.ts    # tests directly (Node ≥ 22.6, native TS type-strip)
npm run typecheck             # if a TypeScript compiler is installed
```

Tests mock `ExtensionAPI`/`ctx` (see `test/fakes.ts`) and assert against the
shared contract fixture in `contracts/fixtures/`, so a build needs neither the Pi
runtime nor a network install.
