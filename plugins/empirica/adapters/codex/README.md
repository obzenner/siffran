# Empirica adapter for Codex CLI

This is a thin Codex-native adapter over the shared `empirica/v2` composition bridge. It translates
Codex 0.146.0 hook payloads and decisions; it does not reimplement claim, evidence, budget,
audit, or convergence rules. The shared bridge composes the host-neutral `application.v2` service
with a fixed exact registry profile and a no-location run port; it owns no host default and
accepts no `cwd`.

## Pinned host contract

The adapter and conformance fixtures target **Codex CLI 0.146.0**. The primary sources are the
tagged Codex implementation and its generated schemas:

- [generated command-hook schemas](https://github.com/openai/codex/tree/rust-v0.146.0/codex-rs/hooks/schema/generated)
- [`PreToolUse` input and decision handling](https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/hooks/src/events/pre_tool_use.rs)
- [`Stop` blocking and continuation handling](https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/hooks/src/events/stop.rs)
- [`SessionStart` sources and context injection](https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/hooks/src/events/session_start.rs)
- [bundled-plugin hook discovery and trust](https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/hooks/src/engine/discovery.rs)
- [canonical tool names and the `Agent` compatibility alias](https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/core/src/tools/hook_names.rs)

Codex hook config uses a shell `command` string. The plugin manifest therefore points to
`hooks/codex.json`, rather than reusing Claude's `hooks/hooks.json`, whose `command` plus `args`
shape is not the Codex 0.146.0 bundled-hook contract.

| Codex surface | Adapter mapping | Behaviour |
|---|---|---|
| explicit `$empirica ...` at prompt start | `StartRun` | best-effort activation, fail open; renders exact Fault code when unsupported |
| `PreToolUse` (Agent, Bash) | `ResolveRun` through strict shell | inert when unresolved (D6); no pre-resolution denial |
| `Stop` | `ResolveRun` through strict shell | inert when unresolved (D6) |
| `SessionStart:compact` | `ResolveRun` through strict shell | inert when unresolved (D6) |

Codex 0.146.0 does not put a timestamp in hook stdin. `turn_id` and `tool_use_id` are retained as
a correlation hint inside the request id; `observed_at` is omitted (a numeric timestamp is never
fabricated).

## Observational truth (D6-C C2b)

Codex is an **observational** host (`codex-cli@0.146.0`, tier `observational`). The adapter
reaches the shared bridge with the fixed exact profile and **no `cwd`**; correlation is exact
`empirica/v2`. The retained public request builders (`StartRun`, `ResolveRun`) each produce a
`contracts/empirica/v2/request.schema.json`-valid envelope.

Run identity/location is D7-owned. At D6 the bridge's no-location run port reports every opaque
ID unresolved, so `ResolveRun` returns `unsupported`/closed and no run handle is resolved. The
four hook entrypoints (PreToolUse, Stop, restore) `ResolveRun` through the strict bridge shell
and return inert when unresolved, without pre-resolution denial. Route, investigation, dispatch,
child-reserve, EvaluateRun, RestoreRun, and response-mapping builders are **deleted**: these
operations are D7-D10-owned and unavailable in the D6 strict shell. The adapter does not
fabricate an active run handle, a trusted capability reference, audit/nonce admission, D7 state,
D8 child admission, D9 projection, or D10 policy. The `adapters.codex.knowledge` module and its
graph/research/spike/regate/audit-ticket/verdict/attribution builders are deleted; Codex owns
local translators and helpers with **zero** `adapters.claude` imports.

## Trust boundary and visibility limits

Installing the plugin does **not** trust its command hooks. Unmanaged hooks are enabled but do not
execute until the user reviews and trusts each normalized command hash in Codex's `/hooks` UI;
changing the handler makes it `modified` and requires another review. The automation-only
`--dangerously-bypass-hook-trust` flag is suitable only for an isolated smoke test that has already
vetted the source. Therefore Empirica enforcement is conditional on the relevant hooks being
trusted and enabled. A disabled, untrusted, or modified hook is visible in Codex but is not an
enforcement boundary.

Hosted Responses API WebSearch is not dispatched through Codex's ordinary tool registry in
0.146.0, so `PreToolUse` cannot observe, stamp, or deny it. Standalone/extension search may be
hook-visible when it is a registered tool, but this adapter makes no blanket claim. This is a host
sensor gap, not evidence of ordering and not a reason to report convergence.

### Auditor round-trip limitation (Codex 0.146.0)

Codex's documented `PreToolUse` hook output has no `updatedInput` field, and its `Stop` payload
contains only the parent `last_assistant_message`, not a spawned child's final output or a child
transcript reference. Therefore this adapter can neither inject a `GetArgument` dossier into a
child nor host-record its verdict. The audit obligation honestly remains open until a future
Codex payload carries a mutable child input plus child final output (or a documented child
transcript path). The adapter does not fabricate a nonce, a trusted `audit_verdict`, or capability
admission to work around this gap.

## Validation

```sh
make check-codex
make codex-live-check CODEX='npx -y @openai/codex@0.146.0'
```

The first target validates manifests, hook shapes, official payload fixtures, and the bounded
adapter suite without inference. The second asks the pinned executable to add this local
marketplace, install Empirica into a temporary `CODEX_HOME`, and list the installed bundle; it
exercises the real plugin loader without using credentials or calling a model.
