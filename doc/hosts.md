# Host support and versions

Which hosts and versions Empirica supports, how Pi admits its audit runtime, and what each surface can do.

## Supported surfaces

Operator-side governance facts (the review document, decision timeout, reviewer configuration) are in [governance-operations.md](governance-operations.md).

Host versions: Pi's range (`>=1.0.0,<2.0.0`) is enforced at `/empirica` start (a Pi outside it, or one whose version cannot be read, is refused with `host.version_unsupported` and nothing starts); Claude Code's range (`>=2.1.278,<2.2.0`) is the documented supported range and is not checked (hook payloads carry no version), so Claude is admitted by observed capability; the pi-subagents range is enforced at runtime.

| Surface | Deliberative | Auto, human present | Auto, delegated (`EMPIRICA_AUTO_DELEGATION=1`) |
|---|---|---|---|
| Claude Code, interactive session | Supported | Supported | n/a |
| Claude Code, non-interactive (`-p`, SDK) | Refused: nobody can approve the configuration | Refused: auto needs the delegation | **Broken** ([#103](https://github.com/obzenner/siffran/issues/103)): Claude Code's default permission mode refuses Empirica's own tools, so the run starts but cannot take a step, and the process exits 0. Use an interactive session |
| Pi TUI and RPC | Supported | Supported | Supported |
| Pi print and JSON (`pi -p`, `--mode json`) | Refused: nobody can approve | Refused: auto needs the delegation | Supported; notices go to stderr, the command waits for the run it started, a refusal exits 1; nobody can confirm evidence |
| Codex | Not available | Not available | Not available |

Claude Code's own *auto permission mode* (a server-side classifier reviews tool calls; `/status` shows `Auto mode server: Enabled`) is unrelated to Empirica's auto control mode above. From Empirica 7.0.2 the audit launches under it in every Claude control mode; 7.0.0 and 7.0.1 could not (see the [upgrade guide](upgrading-empirica-7.md#claude-code-auto-mode-the-audit-launch-702)).

A refusal in print mode is written to stderr and exits 1. Rows qualified only by deterministic tests (Claude Esc mid-turn,
the Pi auditor calling `contact_supervisor`, Pi compaction failure) are listed in
[`.agents/skills/native-qualification/references/scenarios.md`](../.agents/skills/native-qualification/references/scenarios.md), section C.

Pi and Claude async promotion remains separate and unsupported.

The exact Claude Code and Pi foreground profiles were promoted from installed-host observations;
release evidence is the long-running scenarios (same file).

Claude Code 2.1.304 shows only the first three lines of an elicitation message — the rest is
folded — while rendering field descriptions in full, so every Claude form front-loads the
heading, the bound document's `Details:` line and its copy surface, and each field's
description repeats what is being decided in full: the quote, the decision claim's text and the
terminal consent's residual count ([ADR-0072](adr/0072-bind-human-decisions-to-a-core-owned-immutable-hitl-document.md), PR 19); Pi is unchanged. Environment (Empirica 7.2, E8):
a pending environment revision is the same write-first word — the private fetch leg returns the
revision's core-rendered `empirica.hitl/1` `environment` document, the host publishes it once through
the one sidecar helper — refused before any write unless it names this run, the environment kind and the
revision the admitting transaction recorded for the action (carried privately, never in the response, and
required again on a re-present) — and mediates a short buttons-only approve/reject form: heading, the profile
with the networked call-out (`NETWORK EGRESS ENABLED`) and the document's exact `File:` path inside the three
visible lines, then the goal and the image count (never a decision link while the observer projects no
environment decision, E11); the agent's plan, rationale, image references and the installed binding live in the
document under their unverified labels. Accept approves, Decline is an explicit rejection (spec §5), and Esc,
cancel or a timeout leave the revision pending; a digest the host cannot recompute refuses with its own closed
reason and the host re-presents the same revision once. The mediation is composed only for a profile whose
approval ingress is the transport's own (MCP elicitation), and the private fetch and decision read nothing and record nothing for a host whose ingress is
not the one the run recorded.
Pi: the exact `environment` action is admitted in the private bridge's own process through the same admitting
dispatch, so the admission rides beside the public response; the private bridge writes the admitted revision's
document through the same environment mediation and records one present receipt, then the configuration dialog's
shared frame shows the document's own facts — the profile with the networked call-out, the image references, the
agent's plan labelled not verified, the rationale, the installed binding, the short digest and the exact path (no
Details link). Approve and Reject decide with a receipt naming the written document's digest and the admitted
revision; Esc, a timeout or an abort leave the revision pending. The mediation is composed only where the profile's
registered approval ingress is the Pi dialog's own. The exact `qualify` action gets Pi's long outer transport margin
beside the spike request; the core's own deadline bounds the attempt.

## Empirica 7 spike authority

Empirica **7.0.0** is the plugin major; its public wire remains `empirica/v2`, current run storage is
`empirica.run/4`, and the observer model remains `empirica-ui-view/v1`. See the
[upgrade guide](upgrading-empirica-7.md) (and [the 6 guide](upgrading-empirica-6.md)), [ADR-0071](adr/0071-bind-spikes-to-qualified-docker-authority-per-run.md), and the
[normative Docker support contract](../contracts/empirica-docker-spike/v1/README.md). The support contract's internal `/1` does not add a legacy runtime surface.

Two paths coexist until spike admission moves to the run-scoped one (Empirica 7.2).
The **legacy spike execution path** is operator-installed: an operator separately qualifies one exact local Unix
Docker authority after the final tree is clean and committed, and the spike harness reads that installed `/2`
record and pointer. The **run qualification path** is the run's own: `StartRun` binds the run's own `unqualified`
reference and never reads machine-wide authority, and a run binds its exact qualified `docker-isolated-v1` or
`docker-networked-v1` qualification and execution digest pair only through its own approved environment revision and
`qualify` attempt (a charged attempt, compare-and-set into the run). `qualify` takes the shared runtime lock without
waiting before it reserves anything and holds it until the outcome is recorded: a request that cannot take it refuses with
`qualification-in-progress` and is never charged, and an open attempt row seen under the lock belongs to a dead attempt. The one 90-second bound of a `qualify` starts when it takes the lock and is shared by the recovery of a dead row and the attempt that follows. On Claude Code, `qualify` is one MCP tool call like `spike_request` and asks the human nothing itself; the server declares no Claude Code per-server `timeout`, so Claude Code applies no per-call limit below its own default ([Claude Code MCP documentation](https://code.claude.com/docs/en/mcp), "Time limits on MCP tool calls": `MCP_TOOL_TIMEOUT`, about 28 hours), and the core's own 90-second deadline bounds the attempt.
The host composes the run qualification port wherever the guarded generic Docker composition succeeded, never for an
unsupported profile, and a run whose host composition did not bind reads `spikes_unavailable`; existing runs never adopt a later current record. Qualified means bound, not live
health. At host
session launch, `EMPIRICA_SPIKE_PROFILE` may be omitted (the core selects isolated) or set exactly to
`docker-isolated-v1` or `docker-networked-v1`; malformed configured values refuse before `StartRun`. This is
host-process launch authority only: author actions, prompts, tool payloads, plans, and children cannot select or
change it, and the chosen profile is retained in the run's start binding as a ceiling: the run's environment revisions may propose that profile or a lower one, never a higher one. Pi's `GetArgument` author view and the
loopback observer expose the committed generic request/result evidence as explicitly read-only data: sealed plan and
input identities, bounded flat process/resource facts, model attribution, one natural exit and independent stream digests,
uppercase verdict, cleanup and postflight binding, but never raw output, credentials, host/staging paths, endpoints or
runtime/container identifiers. A closed
unavailable request admits no spike result: narrow or redesign the probe, continue research, or let an operator
requalify for a future new run. Never repeat blindly or use an ambient host-shell fallback. Empirica performs no Docker
installation, pull, daemon/VM start or repair, context/configuration switch, or permission change.

That containment is the inner spike's: the harness enforces it for the one verdict container it starts, and nothing
else. It is not whole-agent isolation. The host agent's own shell, Docker CLI and other tool bridges stay outside the
harness, and the skill's and this page's prohibitions on using them for experiments (the contract's
`evidence/spike/isolation` and `evidence/spike/outcomes` clauses) are guidance to the agent, not a security
boundary. Running the whole agent inside a VM is not a feature of this release: there is no reusable launcher, and
Empirica neither needs nor depends on Lima or any other VM.

The public `spike_request` carries exactly `kind`, `claim_id`, and a closed
`empirica.spike-plan-draft/2`; it contains exactly a familiar already-local image reference, exact
nonempty argv, repository-relative file/directory inputs, and a bounded environment map. It carries
no profile, resource, timeout, user, workdir, network, topology, service, alias, command string, host
path, Docker option, or framework policy. The host resolves immutable image ID and the Docker server's canonical native
platform (host-Python architecture is not an authority predicate), expands one captured `/inputs` tree, and owns the single verdict container plus every
security/resource/network decision. Never put credentials or secrets in environment values: request
artifacts persist values internally, and content-addressed plan identities can confirm guessed
low-entropy values although public views expose names only. Natural exit zero is `PASS`; nonzero is
`FAIL` except 137, which is unavailable. Timeout, output limit, OOM, identity, cleanup, or postflight
ambiguity also admits no result. The framework seals the exact 210-second whole-operation bound into
every immutable request artifact; historical projection reads that persisted fact rather than a
later framework constant.

The local-image resolver, capability authority, single-container Docker compiler, semantic static and terminal admission, immutable snapshot staging, bounded effects, reconstructing journal, exact-ID cleanup/recovery, production harness, and five-probe qualification are `/2`-native. Qualification and installation are deliberately separate operator commands. Ordinary production execution only reads a current exact `siffran.empirica.docker-spike-qualification-record/2` record through an exact `siffran.empirica.docker-spike-qualification-pointer/2`; an absent, legacy, malformed, or drifted record freezes spikes unavailable and is never repaired or migrated.

After Docker is already running and the operator has acquired a suitable local server-native Linux image, `make generic-docker-qualify-candidate PROFILE=<docker-isolated-v1|docker-networked-v1> IMAGE=<local-ref> DOCKER=<absolute-cli> OUT=<absolute-private-candidate.json> WORK=<absolute-private-work-root>` runs the five bounded probes and writes only the selected mode-0600 candidate. `DOCKER=` may name the normal absolute Docker Desktop symlink; it is accepted only when it resolves to a canonical regular executable. `OUT=`, `WORK=`, and `SANDBOX_HOME=` must be canonical owner-controlled absolute paths and cannot contain comma or quote characters because Docker's mount input is CSV. `OUT=` names a new file: it must not exist yet, and its parent must already exist as a real directory (not a symlink) that you own with mode exactly `0700`; the command does not create it. The check runs when the candidate is written, after all five probes, so create the private directory first or a full qualification ends without a candidate. It never installs a record or pointer. Review that result, then separately run `make generic-docker-install-candidate PROFILE=<same-profile> CANDIDATE=<absolute-candidate.json> SANDBOX_HOME=<absolute-private-sandbox-home>` to revalidate the candidate against current semantic capabilities and implementation identity and atomically publish only the new `/2` record and pointer. The install command never qualifies, acquires an image, or touches a legacy namespace.

Qualification is explicit and profile-specific. Isolated uses Docker network
`none`; networked creates one private non-internal bridge for the single verdict container, allowing
outbound egress while publishing no ports or permitting host ingress. Production proves the exact owned bridge has no endpoints before start and the container is configured to it; qualification alone also proves sole membership while the probe is running. Qualification has five
mandatory probes: sandbox-realization PASS, natural-125 FAIL, output-overflow unavailable,
qualification-only five-second timeout unavailable, and profile networking. A networked `wget` failure has the specific `network-egress-unavailable` reason; OOM qualification is
optional. It requires memory, swap, CPU, and PID enforcement and an already-local server-native Linux image with `/bin/sh`, `cat`, `chmod`, `command`, `head`, `id`, `printf`, `sleep`, `test`, `touch`, and `wget`; it never pulls, installs, repairs, switches
context, or starts/stops the daemon or VM. Implementation, policy, endpoint, semantic capability,
platform, selected profile, image evidence, all five observations, scoped cleanup proof, execution
binding, and record identity are sealed; Docker patch/cgroup diagnostic strings, host-Python architecture, NCPU/MemTotal, optional security options, and CLI/Python bytes
are not installed semantic authority. Discovery scrubs `DOCKER_HOST`, so a rootless operator selects the rootless daemon with `docker context use rootless` (not `export DOCKER_HOST=unix:///run/user/$UID/docker.sock`) before qualifying and running; the qualified endpoint is the context's local Unix socket. A changed bound fact requires an explicit new candidate and install, and the new
record applies only to future runs. The profile-owned `DOCKER_CONFIG` directory must remain empty; if a Docker CLI release writes metadata there, ordinary execution refuses with an instruction to inspect and remove the foreign entries, and the affected profile must be qualified and installed again. Whether any supported live Docker release writes such metadata remains a live-only qualification question.

An unavailable spike returns a Fault whose closed `reason` (for example `lifecycle-unavailable` or `cleanup-unavailable`) is explained, with every other agent-visible limit, in public-contract clause `evidence/spike/limits` (GetContract section `evidence/spike`). An unfinished operation is recovered automatically before the next spike on the same Docker daemon (same endpoint, server OS, and architecture), from the data its owner-only journal persisted, even after a plugin or engine upgrade or a requalification. A journal recorded under another endpoint fails closed, because its IDs belong to another daemon: after an endpoint switch every spike returns `cleanup-unavailable` until that journal is recovered on its own endpoint or resolved by the procedure below. When recovery stays stuck, follow the exact-ID operator procedure in the [support contract](../contracts/empirica-docker-spike/v1/README.md#private-stuck-operation-diagnosis), which covers recorded-ID, cidfile-only, and no-ID journals; nothing private is logged. Never clean by Docker name or label alone.

## Pi and pi-subagents

Empirica's independent audit runs through the external
[`pi-subagents`](https://www.npmjs.com/package/pi-subagents) extension; this package does **not**
bundle or load it. The Pi adapter is POSIX-only (macOS and Linux) because runtime provenance records
POSIX paths; Windows is not supported. Qualified spikes execute through the exact generic Docker backend
and preserve the plan's declared executable/argv without ambient host-shell fallback. Pi's 300-second
stdio deadline for an exact public spike request is only an outer transport margin around the
backend's 210-second operation; it does not extend the 120-second workload hard limit. Install the
runtime first, then the repository (which carries Empirica, the Methodologist, and the Empirica auditor
role):

```sh
pi install npm:pi-subagents@0.84.0
pi install git:github.com/obzenner/siffran
```

At `session_start` the Empirica adapter resolves the single extension that registered the `subagent`
tool from Pi's own tool inventory, reads that package's exact version, and binds its launch preflight.
It refuses to start or configure a run (with a `host.subagents_*` reason) when there is no `subagent`
tool, when the session's slash commands show more than one loaded `pi-subagents` package (Pi reports
only the first `subagent` registration, so a second copy is seen through its commands, not its tool),
or when the owner's package or version cannot be verified. A second copy is detected only while its
extension registers commands; Pi runs and reports the first registrant, and that is the one bound.
Supported `pi-subagents` versions are a range, `>=0.74.0,<1.0.0`, under the policy
`pi-subagents-foreground-audit-v1` (Pi `>=1.0.0,<2.0.0`). A version outside the supported range fails closed:
`/empirica` and `configure_run` are refused with `host.subagents_version_unsupported` before any run is
created. Inside the range a version is admitted by what the adapter observes of it, not by a list: its own
parameter schema must declare every field the audit launch sends (flat, as before 0.77.0; or compact, as from 0.77.0: `agent`, `task`, `model` and `async` at the top level and an `options` object that carries the rest), and its live launch preflight must resolve
the packaged auditor, the configured model and a fresh context, and its tool allowlist may hold only the tools the profile's `audit_child_tools` allows. A version in the table below has a checked-in
compat record and is *reviewed*. Any other version in the range is admitted by observation, recorded as `reviewed: false`
and shown on every run view while it stays unreviewed (`host: pi-subagents <version> (not reviewed; preflight passed)`);
because nobody has reviewed its actions, a `subagent` action runs during a run only if some reviewed version has that
name and the classifier calls it read-only (`list`, `status`, `models`, ...); launches are classified as usual. `make empirica-subagents-matrix-update` lists releases newer than the newest
reviewed one; reviewing one means adding its compat record, preflight fixture and classifier cases.

From 0.77.0 `pi-subagents` publishes the compact `subagent` schema: `agent, task, workflow, args, async, model, cwd, worktree, output, action, id, message`
at the top level and every other field inside one `options` object. For a package that does, the record names where each audit-launch parameter is declared
(`launch_parameter_placement`, read from the package's own `SUBAGENT_OPTION_KEYS`), and the audit launch writes each parameter in that one place and never
in both: `task`, `model`, `async` at the top level, `agentScope`, `context`, `acceptance`, `timeoutMs` and `toolBudget` inside `options`. An author-supplied
`options`, or a launch form hidden in `options` (`script`, `workflowScript`, `resume`, ...), is refused during a run. Versions `0.74.0`-`0.76.1` are unchanged.

| `pi-subagents` | Pi floor (supported) | Status |
|---|---|---|
| `0.74.0` | `>=1.0.0` | reviewed |
| `0.75.0` | `>=1.0.0` | reviewed |
| `0.76.1` | `>=1.0.0` | reviewed; the repository's development pin |
| `0.77.0` | `>=1.0.0` | reviewed |
| `0.78.0` | `>=1.0.0` | reviewed |
| `0.79.0` | `>=1.0.0` | reviewed |
| `0.79.1` | `>=1.0.0` | reviewed |
| `0.79.2` | `>=1.0.0` | reviewed |
| `0.80.0` | `>=1.2.0` | reviewed |
| `0.81.0` | `>=1.2.0` | reviewed |
| `0.82.0` | `>=1.2.0` | reviewed |
| `0.82.1` | `>=1.2.0` | reviewed |
| `0.83.0` | `>=1.2.0` | reviewed |
| `0.83.1` | `>=1.2.0` | reviewed |
| `0.84.0` | `>=1.2.0` | reviewed |

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
