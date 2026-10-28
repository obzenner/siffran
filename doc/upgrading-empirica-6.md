# Upgrading to Empirica 6

Empirica `6.0.0` uses public wire `empirica/v2`, persisted state `empirica.run/3`, and observer view
`empirica-ui-view/v1`. Install the matching Claude, Codex, or Pi package as a unit; do not mix plugin sources or vendored
contracts from different builds.

## Start fresh runs

Empirica 6 reads only exact `empirica.run/3` documents. Documents with any other state discriminator are rejected as
corrupt and are not rewritten. Start a fresh run after installing 6.0.0; do not copy operational state, backend bindings,
request artifacts, or mutable pointers between generations.

A new run binds spike authority once at `StartRun`:

* `{ "kind": "spikes_unavailable" }`; or
* an exact generic qualified binding for `docker-isolated-v1` or `docker-networked-v1`, with immutable
  qualification-record and execution-binding SHA-256 identities.

Qualified means bound, not live health. Every operation resolves and revalidates the run’s historical record. A later
qualification install is eligible only for a future new run.

## Public spike actions are closed plans

Replace every command-shaped or unreleased multi-container request with exactly:

```json
{
  "kind": "spike_request",
  "claim_id": "C0",
  "plan": {
    "schema": "empirica.spike-plan-draft/2",
    "image": "python:3.12",
    "argv": ["python", "/inputs/probe.py"],
    "inputs": ["probe.py", "tests/"],
    "environment": {"PYTHONDONTWRITEBYTECODE": "1"}
  }
}
```

There is no `/1` loader or migration shape. The five plan fields are exact: one already-local image
reference, one nonempty argv vector, zero or more canonical repository-relative file/directory inputs,
and at most 128 environment overrides. `HOME` and `TMPDIR` are reserved. Authors cannot provide
profile, resources, timeout, user, workdir, topology, services, aliases, Docker options, host paths,
credentials, image acquisition, or derived capture facts.

The host resolves immutable local image ID and canonical native platform, expands one captured input
tree, and owns one verdict container plus every security/resource/network fact. Defaults are uid/gid
65534, `/work`, `/tmp`, `/inputs`, `min(2, NCPU)` CPU, 2 GiB memory, 256 PIDs, fixed ulimits,
independent 1-MiB streams, 256-file/64-MiB capture, and a 120-second workload inside the sealed
210-second operation. Natural zero is `PASS`; nonzero is `FAIL` except 137, which is unavailable.
Every substrate ambiguity also admits no result. Environment values persist internally and must never
contain secrets because content-addressed plan identity can confirm guesses even when public views show
names only.

## Configure and qualify Docker authority

`EMPIRICA_SPIKE_PROFILE` belongs to the host process that launches Empirica. Leave it absent for
`docker-isolated-v1`, or set exactly `docker-isolated-v1` or `docker-networked-v1` before `StartRun`. A malformed value
refuses before run creation. The action and plan cannot change it.

Use an already-local, server-native Linux image that has the qualification tools. Qualification never pulls or changes
Docker, and it is separate from installation:

```text
make generic-docker-qualify-candidate PROFILE=docker-isolated-v1 IMAGE=<local-ref> \
  DOCKER=<absolute-docker-cli> OUT=<absolute-private-candidate.json> WORK=<absolute-private-work-root>
make generic-docker-install-candidate PROFILE=docker-isolated-v1 \
  CANDIDATE=<absolute-private-candidate.json> SANDBOX_HOME=<absolute-private-sandbox-home>
```

Repeat with `PROFILE=docker-networked-v1` if needed. The qualify command writes only a private mode-0600 candidate; the
install command revalidates it against the current semantic authority and implementation and atomically publishes the
record and pointer. One 60-second deadline covers five ordered probes, `realization`, `natural-125`, `output-overflow`,
`timeout`, and `network-profile`, each through the production lifecycle with exact-ID cleanup. Any failure leaves no
installed record. See [hosts](hosts.md) for the exact variables, and public-contract clause `evidence/spike/limits`
for every limit an agent sees.

After changing plugin code, contracts, host adapters, the selected local Unix endpoint, or semantic Docker capabilities,
qualify and install again. The new pointer affects future runs only. An unfinished spike operation from an older plugin
build is still recovered by the new build on the same endpoint.

## Admission and audit behavior

Empirica commits the sealed request before external effects, then admits a result in a second CAS after complete
revalidation. A result-CAS retry does not rerun the harness, but a crash or separate call may execute the same sealed
request again. Design probes to be deterministic, bounded, safe under at-least-once execution, and independent of
unqualified ambient services. Isolated-profile probes have no outbound egress; networked-profile probes may use outbound
Internet access as part of the sealed experiment.

Observer and author surfaces show generic request/result facts as read-only untrusted evidence. A request with unavailable
effects remains `requested` with no result; it is never rendered as `FAIL`. These surfaces expose identities, byte
counts/digests, termination, verdict, cleanup, and attribution—not raw output, credentials, host paths, runtime IDs,
Docker endpoints/config/socket data, journals, inspect/attach data, or current pointers.
