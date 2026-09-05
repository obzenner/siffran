# Empirica adapter for Codex CLI

This is a thin Codex-native adapter over the existing `empirica/v1` application service. It
translates Codex hook payloads and decisions; it does not reimplement claim, evidence, budget,
audit, or convergence rules. The shared bridge still wires the same operational repository under
`$EMPIRICA_HOME` (default `~/.empirica-plugin`) and the same Git artifact repository under
`refs/empirica/*`. No normal adapter operation writes `.codex` or any other runtime state into the
repository.

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

| Codex surface | Adapter mapping | Enforcement |
|---|---|---|
| explicit `$empirica ...` at prompt start | `StartRun` | activation is best-effort and otherwise inert |
| `PreToolUse`, matcher `Agent` | `ObserveAction(reserve_spawn)`; auditor marker also issues a ticket | cap denial and closed corrupt-state faults deny the spawn |
| `PreToolUse`, matcher `Bash` | first-write route/investigation stamps; recognized CLI actors reserve and record dispatch | route is witnessed; CLI spawn cap is enforced when `cli_exec` is enabled |
| `Stop` | `EvaluateRun(report_convergence)` | `Block` and closed faults return native `decision: block` |
| `SessionStart:compact` | `RestoreRun` | observational, bounded context, never a completion gate |
| `adapters.codex.knowledge` | graph, research, deterministic spike/re-gate, audit ticket/verdict, attribution | shared validation and application service |

After choosing known/unknown, record the route before any investigative Bash command with a
portable no-op. The `PreToolUse:Bash` hook recognizes this marker and submits the route first:

```sh
python3 -c 'pass' -- --empirica-route 'runtime behavior is unknown'
```

Codex 0.146.0 does not put a timestamp in hook stdin. `turn_id` and `tool_use_id` are copied as
review metadata, while the existing service's CAS-assigned monotone sequence is the authoritative
route/action ordering witness.

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
hook-visible when it is a registered tool, but this adapter makes no blanket claim. A research
citation can still be recorded after hosted search, and the independent auditor can re-read it,
but P1 route ordering for that hosted action is **unverified** unless an earlier hook-visible
action already established the investigation stamp. This is a host sensor gap, not evidence of
ordering and not a reason to report convergence.

The audit ticket proves only that a trusted `PreToolUse:Agent` hook witnessed a requested spawn.
It does not authenticate the spawned actor or the unsigned verdict; those remain within Empirica's
documented file-level trust model. Codex plugin bundles also do not load Claude's `agents/`
definitions, so a Codex auditor spawn must include the literal `empirica-auditor` marker in its
dispatcher-visible `agent_type`, `name`, `task_name`, or `message` for the ticket to be issued.

## Validation

```sh
make empirica-codex-check
make codex-live-check CODEX='npx -y @openai/codex@0.146.0'
```

The first target validates manifests, hook shapes, official payload fixtures, and a complete
isolated bridge lifecycle without inference. The second asks the pinned executable to add this
local marketplace, install Empirica into a temporary `CODEX_HOME`, and list the installed bundle;
it exercises the real plugin loader without using credentials or calling a model.
