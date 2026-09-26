# Host capability preflight

Read this file after route, proposal approval, and investigation acknowledgement. Capability claims come from the registered
profile, its qualified compatibility range, and the live adapter surface—not generic host
documentation or exact equality with one qualification build. Installed-host receipts retain the
exact observed version as provenance.

Governance 4.0.0 requires client-advertised MCP form elicitation on Claude and UI on Pi.
Neither path reads or transports a configured model catalog. Codex deliberative approval is
unavailable; auto also blocks for currently unmapped active slugs such as `gpt-5.1-codex`.
Dated-snapshot simulated tests do not establish installed support;
even a mapped author cannot fix the separate unobservable auditor identity limit.
See [governance.md](governance.md). These new UI flows have deterministic simulated-host
coverage, not installed/native human approval qualification.

## Required surface

A complete host reads the public RunView, submits public author actions, obtains the audit
argument, binds and observes an independent auditor, admits its output privately, and requests a
guarded terminal decision. Trusted evidence, attribution, child events, and verdicts are never
model-callable.

## Claude Code capability profile (`>=2.1.278,<2.2.0`)

Required surfaces:

- active and trusted Empirica activation/route/spawn/Stop hooks;
- MCP tools `empirica_observe`, `empirica_read`, and `report_convergence`;
- canonical `empirica:empirica-auditor` async child with native completion notification.

The host mutates the auditor prompt with the typed dossier, binds the async native ID at
`SubagentStart`, and admits the first terminal output only through `SubagentStop`. While that exact
audit is pending, the Stop hook lets the parent turn settle without terminalizing the run or
suggesting another spawn; Claude's native task notification resumes the parent after completion.
The generic child tier remains `foreground_only`; the registered audit execution mode is `async`.
`PostModelSwitch` refreshes observed author identity through the lifecycle hook and revokes current
approval on a material change. The decision deadline environment must reach the Claude
process and inherited MCP server. This mechanism is tested via lifecycle subprocesses, not native
model switching or proof that Claude honors every Bedrock-prefixed `Agent.model` override.

## Pi capability profile (`>=0.84.1,<0.85.0` + `pi-subagents@0.50.0`)

Required surfaces:

- `/empirica` has injected an opaque run handle;
- `empirica_observe`, `empirica_read`, and `report_convergence` are registered;
- `pi-subagents@0.50.0` provides the structured `subagent` tool and its `tool_result` event;
- the packaged `empirica.empirica-auditor` role is executable.

The adapter forces the canonical auditor to foreground execution, correlates by `toolCallId`,
redacts the verdict before its first await, and uses adapter-private ingress. It ignores the
configured result model and binds identity to the final native assistant record in the exact
host-generated child session only when that record's verdict equals the admitted result. Missing
or ambiguous session evidence remains unverified and blocks. Pi has no native completion veto;
call `report_convergence` before any status claim. Bare Pi without the subagent surface is
unsupported even when its harness version lies in the compatible range. Governance dialog API
signatures were checked on installed Pi 0.87.1 only; that is not qualification of the pinned
0.84.1 surface or a native human approval receipt.

## Codex CLI observational profile (`>=0.146.0,<0.147.0`)

Required surfaces:

- explicit activation injected an opaque handle;
- MCP tools `empirica_observe`, `empirica_read`, and `report_convergence` are present;
- the Empirica Stop hook is enabled and trusted;
- `codex exec` and the configured auditor model are available.

The profile was qualified on Codex 0.146.0, which cannot observe an arbitrary native child's final
output. Its adapter therefore
owns a bounded foreground `codex exec` auditor at Stop, records lifecycle, privately admits the
exact final verdict, and re-evaluates before completion. The process argv is configuration, not an
observed resolved model, so auditor identity remains unverified and convergence blocks. Do not
spawn an ordinary auditor child. Async remains unsupported.

Hosted WebSearch may be invisible to `PreToolUse`; that sensor gap never proves research ordering.

## Fail-closed project identity boundary

If a `.git` directory/file marker exists but Git project identity cannot be resolved (including
`safe.directory` / dubious ownership refusal, unavailable Git, or empty/error output), hooks
block even an apparently inactive session. Without stable project identity they cannot safely
rule out an active Empirica run. This accepted fail-closed behavior is not a `no_run` fallback;
repair project access outside the governed author session. No global Git setting change is part
of approval or qualification.

## Capability failure output

```text
Empirica cannot execute on <exact host profile>.
Missing capability: <typed operation or lifecycle>.
Observed surface: <tools/events actually available>.
Run status: <none | active handle that cannot progress>.
No convergence claim was made.
```
