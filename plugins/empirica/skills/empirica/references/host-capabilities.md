# Host capability preflight

Read this file after route acknowledgement. Capability claims come from the registered
profile, its qualified compatibility range, and the live adapter surface—not generic host
documentation or exact equality with one qualification build. Installed-host receipts retain the
exact observed version as provenance.

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
unsupported even when its harness version lies in the compatible range.

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

## Capability failure output

```text
Empirica cannot execute on <exact host profile>.
Missing capability: <typed operation or lifecycle>.
Observed surface: <tools/events actually available>.
Run status: <none | active handle that cannot progress>.
No convergence claim was made.
```
