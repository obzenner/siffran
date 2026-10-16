# Host support and versions

Which hosts and versions Empirica supports, how Pi admits its audit runtime, and what each surface can do.

## Supported surfaces

Host versions: Pi's range (`>=1.0.0,<2.0.0`) is enforced at `/empirica` start (a Pi outside it, or one whose version cannot be read, is refused with `host.version_unsupported` and nothing starts); Claude Code's range (`>=2.1.278,<2.2.0`) is the documented supported range and is not checked (hook payloads carry no version), so Claude is admitted by observed capability; the pi-subagents range is enforced at runtime.

| Surface | Deliberative | Auto, human present | Auto, delegated (`EMPIRICA_AUTO_DELEGATION=1`) |
|---|---|---|---|
| Claude Code, interactive session | Supported | Supported | n/a |
| Claude Code, non-interactive (`-p`, SDK) | Refused: nobody can approve the configuration | Refused: auto needs the delegation | Supported; nobody can confirm evidence, so URL research stays unconfirmed |
| Pi TUI and RPC | Supported | Supported | Supported |
| Pi print and JSON (`pi -p`, `--mode json`) | Refused: nobody can approve | Refused: auto needs the delegation | Supported; notices go to stderr, the command waits for the run it started, a refusal exits 1; nobody can confirm evidence |
| Codex | Not available | Not available | Not available |

A refusal in print mode is written to stderr and exits 1. Rows qualified only by deterministic tests (Claude Esc mid-turn,
the Pi auditor calling `contact_supervisor`, Pi compaction failure) are listed in
[`.agents/skills/native-qualification/references/scenarios.md`](../.agents/skills/native-qualification/references/scenarios.md), section C.

Pi and Claude async promotion remains separate and unsupported.

The exact Claude Code and Pi foreground profiles were promoted from installed-host observations;
release evidence is the long-running scenarios (same file).

## Pi and pi-subagents

Empirica's independent audit runs through the external
[`pi-subagents`](https://www.npmjs.com/package/pi-subagents) extension; this package does **not**
bundle or load it. The Pi adapter is POSIX-only (macOS and Linux) because runtime provenance records
POSIX paths; Windows is not supported. Qualified spikes execute through the exact Docker backend and
its container-internal `/bin/sh -c` policy—there is no ambient host-shell fallback. Pi's 300-second
stdio deadline for an exact public spike request is only an outer transport margin around the
backend's 210-second operation; it does not extend the 120-second workload hard limit. Install the
runtime first, then the repository (which carries Empirica, the Methodologist, and the Empirica auditor
role):

```sh
pi install npm:pi-subagents@0.76.1
pi install git:github.com/obzenner/siffran
```

At `session_start` the Empirica adapter resolves the single extension that registered the `subagent`
tool from Pi's own tool inventory, reads that package's exact version, and binds its launch preflight.
It refuses to start or configure a run (with a `host.subagents_*` reason) when there is no `subagent`
tool, when the session's slash commands show more than one loaded `pi-subagents` package (Pi reports
only the first `subagent` registration, so a second copy is seen through its commands, not its tool),
or when the owner's package or version cannot be verified. A second copy is detected only while its
extension registers commands; Pi runs and reports the first registrant, and that is the one bound.
Supported `pi-subagents` versions are a range, `>=0.50.0,<1.0.0`, under the policy
`pi-subagents-foreground-audit-v1` (Pi `>=1.0.0,<2.0.0`). A version outside the supported range fails closed:
`/empirica` and `configure_run` are refused with `host.subagents_version_unsupported` before any run is
created. Inside the range a version is admitted by what the adapter observes of it, not by a list: its own
parameter schema must declare every field the audit launch sends, and its live launch preflight must resolve
the packaged auditor, the configured model and a fresh context, and its tool allowlist may hold only the tools the profile's `audit_child_tools` allows. A version in the table below has a checked-in
compat record and is *reviewed*. Any other version in the range is admitted by observation, recorded as `reviewed: false`
and shown on every run view while it stays unreviewed (`host: pi-subagents <version> (not reviewed; preflight passed)`);
because nobody has reviewed its actions, a `subagent` action runs during a run only if some reviewed version has that
name and the classifier calls it read-only (`list`, `status`, `models`, ...); launches are classified as usual. `make empirica-subagents-matrix-update` lists releases newer than the newest
reviewed one; reviewing one means adding its compat record, preflight fixture and classifier cases.

| `pi-subagents` | Pi floor (supported) | Status |
|---|---|---|
| `0.50.0` | `>=1.0.0` | reviewed |
| `0.64.0` | `>=1.0.0` | reviewed |
| `0.74.0` | `>=1.0.0` | reviewed |
| `0.75.0` | `>=1.0.0` | reviewed |
| `0.76.1` | `>=1.0.0` | reviewed; the repository's development pin |

"Requires Pi" is each release's own declared `@earendil-works/pi-ai` peer, raised to the floor of the supported Pi interval (1.0.0, which every reviewed release's peer is below). It is
recorded in [`plugins/empirica/adapters/pi/compat/`](../plugins/empirica/adapters/pi/compat/) and checked against this table.

Install the newest reviewed version and pin the exact
version, as in the commands above; an unpinned install may resolve to a release outside the range, which is
refused, or to an unreviewed one inside it, which runs with only the reviewed read-only `subagent` actions allowed during a run.

## Pi commands and tools

Restart Pi after installation, or run `/reload` in an existing session. Available commands include:

```text
/think <intent>                 # structured Methodologist workflow
/think --simple <intent>        # original single-prompt Methodologist mode
/empirica <goal>                # start a durable Empirica v2 workflow
/empirica-ui                    # open the observer UI for your store
empirica_observe                 # tool: route, graph, configure_run, investigate, research, spike_request, freeze
empirica_read                    # tool: complete RunView, argument, contract
report_convergence               # tool: guarded terminal decision
```

The Pi adapter binds `empirica.empirica-auditor` as a foreground child, correlates
its `tool_result`, and admits trusted facts through non-model-callable private ingress.
It ignores requested result-model metadata and binds identity to the final native assistant
record in the exact host-generated child session when that record produced the admitted verdict.
Missing or ambiguous session evidence blocks. Pi also has no native completion veto; call
`report_convergence` before any status claim.

To update later:

```sh
pi update --extensions
```

For local development, install the checkout instead:

```sh
pi install "$(pwd)"
```
