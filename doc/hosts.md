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

## Empirica 6 spike authority

Empirica **6.0.0** is the plugin major; its public wire remains `empirica/v2`, current run storage is
`empirica.run/3`, and the observer model remains `empirica-ui-view/v1`. See the
[upgrade guide](upgrading-empirica-6.md), [ADR-0071](adr/0071-bind-spikes-to-qualified-docker-authority-per-run.md), and the
[normative Docker support contract](../contracts/empirica-docker-spike/v1/README.md). The support contract's internal `/1` does not add a legacy runtime surface.

An operator separately qualifies one exact local Unix Docker authority after the final tree is clean and committed.
`StartRun` binds either an exact qualified `docker-isolated-v1` or `docker-networked-v1`
qualification and execution digest pair, or
`spikes_unavailable`; existing runs never adopt a later current record. Qualified means bound, not live health. At host
session launch, `EMPIRICA_SPIKE_PROFILE` may be omitted (the core selects isolated) or set exactly to
`docker-isolated-v1` or `docker-networked-v1`; malformed configured values refuse before `StartRun`. This is
host-process launch authority only: author actions, prompts, tool payloads, plans, and children cannot select or
change it, and the chosen profile is retained in the immutable run binding. Pi's `GetArgument` author view and the
loopback observer expose the committed generic request/result evidence as explicitly read-only data: sealed plan and
input identities, bounded flat process/resource facts, model attribution, one natural exit and independent stream digests,
uppercase verdict, cleanup and postflight binding, but never raw output, credentials, host/staging paths, endpoints or
runtime/container identifiers. A closed
unavailable request admits no spike result: narrow or redesign the probe, continue research, or let an operator
requalify for a future new run. Never repeat blindly or use an ambient host-shell fallback. Empirica performs no Docker
installation, pull, daemon/VM start or repair, context/configuration switch, or permission change.

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

After Docker is already running and the operator has acquired a suitable local server-native Linux image, `make generic-docker-qualify-candidate PROFILE=<docker-isolated-v1|docker-networked-v1> IMAGE=<local-ref> DOCKER=<absolute-cli> OUT=<absolute-private-candidate.json> WORK=<absolute-private-work-root>` runs the five bounded probes and writes only the selected mode-0600 candidate. `DOCKER=` may name the normal absolute Docker Desktop symlink; it is accepted only when it resolves to a canonical regular executable. `OUT=`, `WORK=`, and `SANDBOX_HOME=` must be canonical owner-controlled absolute paths and cannot contain comma or quote characters because Docker's mount input is CSV. It never installs a record or pointer. Review that result, then separately run `make generic-docker-install-candidate PROFILE=<same-profile> CANDIDATE=<absolute-candidate.json> SANDBOX_HOME=<absolute-private-sandbox-home>` to revalidate the candidate against current semantic capabilities and implementation identity and atomically publish only the new `/2` record and pointer. The install command never qualifies, acquires an image, or touches a legacy namespace.

Qualification is explicit and profile-specific. Isolated uses Docker network
`none`; networked creates one private non-internal bridge for the single verdict container, allowing
outbound egress while publishing no ports or permitting host ingress. Production proves the exact owned bridge has no endpoints before start and the container is configured to it; qualification alone also proves sole membership while the probe is running. Qualification has five
mandatory probes: sandbox-realization PASS, natural-125 FAIL, output-overflow unavailable,
qualification-only five-second timeout unavailable, and profile networking. A networked `wget` failure has the specific `network-egress-unavailable` reason; OOM qualification is
optional. It requires memory, swap, CPU, and PID enforcement and an already-local server-native Linux image with `/bin/sh`, `cat`, `chmod`, `command`, `head`, `id`, `printf`, `sleep`, `test`, `touch`, and `wget`; it never pulls, installs, repairs, switches
context, or starts/stops the daemon or VM. Implementation, policy, endpoint, semantic capability,
platform, selected profile, image evidence, all five observations, scoped cleanup proof, execution
binding, and record identity are sealed; Docker patch/cgroup diagnostic strings, host-Python architecture, NCPU/MemTotal, optional security options, and CLI/Python bytes
are not installed semantic authority. A changed bound fact requires an explicit new candidate and install, and the new
record applies only to future runs. The profile-owned `DOCKER_CONFIG` directory must remain empty; if a Docker CLI release writes metadata there, ordinary execution refuses with an instruction to inspect and remove the foreign entries, and the affected profile must be qualified and installed again. Whether any supported live Docker release writes such metadata remains a live-only qualification question.

A failed recovery exposes only `cleanup_unavailable` publicly, while the private process diagnostic names the stuck `o-…` operation and reason. Follow the exact-ID operator procedure in the [support contract](../contracts/empirica-docker-spike/v1/README.md#private-stuck-operation-diagnosis): inspect the owner-only journal, act only on its recorded 64-hex IDs, prove exact absence, and retain all evidence when authority remains ambiguous. Never clean by Docker name or label.

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
