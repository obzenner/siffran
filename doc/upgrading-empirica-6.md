# Upgrading to Empirica 6

Empirica **6.0.0** is a plugin major. It still speaks the public **`empirica/v2`** wire protocol, stores current runs as
**`empirica.run/3`**, and serves the observer's **`empirica-ui-view/v1`** read model. Those identifiers describe different
contracts and do not track the plugin package version.

## Start a fresh run

Empirica 6 does not load or migrate `empirica.run/2`. A run/2 document has no immutable spike authority and is treated as
unsupported/corrupt without being changed. There is no compatibility loader, schema, migration, or mutation branch.
Use an explicit fresh `StartRun` (normally `/empirica <goal>`) to create a new run/3 generation. Existing run bytes,
content-addressed artifacts, and refs remain untouched; they are not copied into the new run.

Existing valid run/3 generations keep the exact backend they selected when they started. Publishing a new qualification
record never changes them.

## Qualified spikes are operator-installed authority

A model session cannot install or repair spike execution. Outside the run, an operator qualifies one exact local
Unix-socket Docker authority: canonical Docker CLI and bytes, endpoint/runtime, already-present digest-pinned image and
config, execution policy, environment, and security-relevant implementation/contract bytes. Empirica never installs
Docker or dependencies; pulls an image; starts, stops, or repairs a daemon or VM; changes context or configuration; or
changes socket/filesystem permissions.

At `StartRun`, the run binds exactly one public `spike_backend`:

* `{kind: "spikes_unavailable"}`; or
* qualified `docker-rust-v1` plus immutable qualification-record and execution-binding digests.

Qualified means **bound**, not "healthy right now". Execution revalidates the run's exact immutable record and Unix
endpoint and fails closed when it cannot. There is no ambient host `/bin/sh`, host subprocess, or weaker fallback.
Requalification can authorize only a **future new run**.

The 6.0 source and public-contract cutover changes implementation authority, so qualification records installed for an
earlier build cannot authorize it. Perform the final operator qualification only after the reviewed tree is clean and
committed. Qualification is intentionally not an upgrade-script side effect.

## Request and result admission

A spike uses two compare-and-set transactions:

1. seal and commit the request, exact run backend, prerequisites, command, and dependent files;
2. after external execution, revalidate the current bindings and separately admit the immutable result.

A conflict while admitting the result retries only admission within that call; it does not rerun that call's harness.
A crash after execution or a separate concurrent call can execute a sealed request more than once. The durable guarantee
is therefore **at least once across crashes/separate calls**, and probes must be bounded, offline, deterministic, small,
and safe under duplicate execution. An unavailable substrate outcome admits no spike result evidence.

See [ADR-0071](adr/0071-bind-spikes-to-qualified-docker-authority-per-run.md), the
[host boundary](hosts.md), and the [evidence guide](../plugins/empirica/skills/empirica/references/evidence.md).
