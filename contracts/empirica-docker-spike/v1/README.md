# Empirica Docker spike support contract v1

This directory is the normative, adapter-independent support contract for Empirica 6's **current-only** generic Docker spike implementation. Its `/1` is the version of this internal support contract, not a runtime compatibility promise: runtime artifacts remain flat `/2`, runs remain `empirica.run/3`, public wire remains `empirica/v2`, and the observer remains `empirica-ui-view/v1`; no old runtime loader, migration, fallback, or translation exists.

`support-contract.json` is the closed machine-readable contract and `support-contract.schema.json` validates it. `make empirica-docker-support-contract-check` validates the data, checks its fixed policy/profile/probe mappings against the implementation, and verifies that every cited pinned fixture exists. The independent Docker fixture is owned by `scripts/generate_generic_docker_fixtures.py`; it contains literals from the G11 probe corpus and verbatim Docker CLI/Moby 20.10–29 forms and deliberately imports no production planner code.

## Normative boundaries

1. **Host support.** The daemon must be genuine Docker Engine/Desktop, serve native Linux `amd64` or `arm64` over one canonical local Unix endpoint, expose cgroup namespaces and default/builtin seccomp, and report `MemoryLimit`, `SwapLimit`, `CpuCfsQuota`, and `PidsLimit` true. Client/server patch versions, cgroup diagnostic strings, host-Python architecture, `NCPU`, `MemTotal`, and optional security options are diagnostics, not installed semantic authority. Current `NCPU` only seals the request's `min(2,NCPU)` CPU allocation; ordinary request/reobservation catches relevant drift.
2. **Image resolution.** One already-local image is resolved to the daemon image ID and canonical server-native platform. Pull, build, load, login, image inventory, context mutation, and daemon repair are outside scope; ambiguity, missing image, malformed identity/platform, declared volumes, missing qualification tools, or unusable uid 65534 fail closed.
3. **Sandbox policy.** The immutable numbers and fixed paths in `sandbox_policy` are framework authority. Authors control only image reference, argv, relative input declarations, and environment overrides within the flat `/2` draft.
4. **Lifecycle.** Production proves an exact owned bridge with no live endpoints before start and proves the container is configured to that bridge. Sole running membership is a qualification-only host-capability proof; a fast production exit is safe under the stated same-user threat model because prestart ownership/emptiness plus exact terminal container network configuration remain admitted. Natural exit 0 is PASS; another natural exit except 137 is FAIL; timeout, overflow, OOM, 137, forced/failed start, malformed observation, cleanup ambiguity, or authority drift is unavailable.
5. **Destructive authority.** Only a durably journaled 64-hex full ID authorizes removal. Docker creates the cidfile at a reserved absent path under umask 077; an absent cidfile before successful creation is valid. Exact absence accepts only an anchored message naming that same recorded ID. Ambiguity retains journal and staging for diagnosis; names and labels never independently authorize deletion.
6. **Qualification.** Five ordered probes share one absolute under-60-second deadline. Qualification writes only a private candidate; installation is a separate explicit revalidation and atomic publication. The networked probe must prove outbound HTTP or exit with the specific `network-egress-unavailable` reason and must observe sole membership before attach completion.
7. **Observer.** Public proof is historical and read-only. It exposes environment names, never values/fingerprints/lengths; independent stream counts/digests, never contents; and no host paths, endpoint, credentials, cidfile/journal, or destructive IDs. Authored environment values persist in private content-addressed run artifacts and must never contain secrets.
8. **Release status.** Every listed host/store cell remains `live-qualification-required`. Pinned fixtures and unit tests prove adapter semantics but are not live host evidence.

## Adapter traceability

| Obligation | Docker CLI input | Daemon observation / admitted equivalents | Exact refusal |
|---|---|---|---|
| Default namespaces | `--ipc private --cgroupns private`; omit `--pid`/`--uts` | PID empty/private; IPC empty/private; UTS empty only; cgroupns empty/private | `container-host-namespace-appeared` |
| Cidfile authority | `--cidfile <absent reserved path>`, child umask 077 | absent before/failed create, or regular owner-only file with one full ID | `cidfile-container-ownership-refused` |
| Private bridge | create bridge, bind create to exact full ID | exact ID/labels/bridge/non-internal; prestart `Containers` empty; terminal network ID exact | `private-bridge-identity-or-attachment-differs` |
| Running membership | qualification attach plus concurrent bounded inspect | exactly the verdict full ID while running; production fast-exit uses the safe prestart fallback | `qualification-running-membership-unobserved` |
| Resources | exact memory/swap/CPU/PID flags | four capability flags true and exact realized HostConfig values | `resource-limits-unavailable` |
| Natural verdict | `container start --attach` | attach/terminal exact exit agreement; PASS 0, FAIL nonzero except 137 | `natural-verdict-unavailable` |
| Exact cleanup | remove and inspect the journaled full ID | anchored `No such <resource>: <ID>` or `<resource> <ID> not found` | `exact-id-absence-unproved` |
| Observer privacy | no Docker operation | closed historical `/2` facts only | malformed proof omitted |

## Private stuck-operation diagnosis

A cleanup/recovery failure returns public `cleanup_unavailable` only; the in-process private diagnostic carries `stuck operation o-…: <reason>`. An operator may inspect the owner-only `${EMPIRICA_SANDBOX_HOME:-~/.empirica-plugin/sandbox}/generic-runtime-v2/o-…/journal.json`, verify the exact recorded IDs with read-only Docker inspect, and retry after correcting daemon/config availability. If explicit removal is necessary, remove only the exact full IDs recorded in that journal, prove each exact ID absent, then remove that one owner-only operation directory and its matching staging directory. Never delete by name/label, clear the journal root wholesale, or invent an ID; unresolved ambiguity must stay retained for recovery.
