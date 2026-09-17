# Host capability preflight

Read this file after route acknowledgement. Capability claims come from the exact registered
profile and live adapter surface, not generic host documentation.

## Required surface

A complete host reads the public RunView, submits public author actions, obtains the audit
argument, binds and observes an independent auditor, admits its output privately, and requests a
guarded terminal decision. Trusted evidence, attribution, child events, and verdicts are never
model-callable.

## Claude Code `claude-code@2.1.270`

Required surfaces:

- active and trusted Empirica activation/route/spawn/Stop hooks;
- MCP tools `empirica_observe`, `empirica_read`, and `report_convergence`;
- canonical `empirica:empirica-auditor` foreground child.

The host mutates the auditor prompt with the typed dossier and observes the final output through
its native subagent completion hook. The profile is `foreground_only`; async is unsupported.

## Pi `pi@0.84.1+pi-subagents@0.50.0`

Required surfaces:

- `/empirica` has injected an opaque run handle;
- `empirica_observe`, `empirica_read`, and `report_convergence` are registered;
- `pi-subagents@0.50.0` provides the structured `subagent` tool and its `tool_result` event;
- the packaged `empirica.empirica-auditor` role is executable.

The adapter forces the canonical auditor to foreground execution, correlates by `toolCallId`,
redacts the verdict before its first await, and uses adapter-private ingress. The pinned native
surface exposes the requested child model but not an independently observed resolved model, so
auditor identity remains unverified and true convergence blocks. Pi has no native completion veto;
call `report_convergence` before any status claim. Bare `pi@0.84.1` without the subagent surface is
unsupported.

## Codex CLI `codex-cli@0.146.0`

Required surfaces:

- explicit activation injected an opaque handle;
- MCP tools `empirica_observe`, `empirica_read`, and `report_convergence` are present;
- the Empirica Stop hook is enabled and trusted;
- `codex exec` and the configured auditor model are available.

Codex 0.146.0 cannot observe an arbitrary native child's final output. Its adapter therefore
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
