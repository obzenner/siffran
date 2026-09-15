# Host capability preflight

Read this file at activation. Capability claims come from the exact registered
profile and live adapter surface, not from generic host documentation.

## Required execution surface

A host can run Empirica to convergence only when it can expose the complete
public run view, submit public author actions, obtain the audit argument, bind and
observe an independent child, admit its verdict privately, and request a guarded
terminal decision. Missing capabilities remain typed and visible.

## Claude Code `claude-code@2.1.270`

Claude Code supplies the lifecycle hooks used by the current full author path:
activation, route/dispatch/spawn interception, convergence gating, restoration,
and auditor completion capture. Use only the builders and hook paths shipped by
the adapter. Do not submit trusted payloads directly.

The profile remains `foreground_only`; asynchronous execution must not be claimed
or silently downgraded. The host observes audit delivery, but model independence
is reported rather than guaranteed.

## Pi `pi@0.84.1`

The shipping v2 adapter exposes:

- `/empirica <goal>` to report the typed capability gap and refuse before
  `StartRun`, creating no run;
- `empirica_status` to resolve and render only run ID and status;
- `report_convergence` as a fail-closed guarded operation;
- opaque handle restoration across session compaction.

It does **not** expose `ObserveAction`, `GetRun`, `GetArgument`, author knowledge,
or a bound audit lifecycle. Executable subagent launches are denied while a run
handle is active. Pi has no completion-veto lifecycle, so a turn can finish
without calling `report_convergence`.

Therefore this profile cannot execute the convergence workflow. If activation has
already created an active handle, explain that it cannot be progressed and do not
invent a graph, evidence record, or auditor result. The tool returning only
`status=active` is not a resume contract.

The candidate profile `pi@0.84.1+pi-subagents@0.50.0` must not be promoted until
its named live probe demonstrates private lifecycle observation and the registry
is updated.

## Codex CLI `codex-cli@0.146.0`

The current profile is observational. Activation can reach the strict bridge
shell, but route, investigation, author observations, child admission, audit, and
convergence completion are unavailable without a resolved lifecycle. Hosted
WebSearch ordering is not observed by `PreToolUse`.

Treat a Codex invocation as unsupported for full convergence. Do not imply that
hook registration alone provides the missing lifecycle.

## Capability failure output

Use this shape:

```text
Empirica cannot execute on <exact host profile>.
Missing capability: <typed operation or lifecycle>.
Observed surface: <tools/events actually available>.
Run status: <none | active handle that cannot progress>.
No convergence claim was made.
```
