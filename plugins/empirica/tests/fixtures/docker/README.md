# Docker semantic fixtures

`generic-linux-v27.json` contains production-code-independent Docker Engine/Desktop capability,
image-inspect, full static-container inspect, terminal-state, endpoint-timing, cidfile, resource-flag,
and exact-absence variants. Static shapes derive from the preserved G11 probe corpus; the
`network <full-id> not found`, `No such container: <full-id>`, and alternate anchored forms are
pinned Docker 24–29 outputs/source forms, and the cidfile behavior is pinned from Moby's exclusive
Docker-created idfile implementation. The fixture's `provenance` names each source class. It covers
Linux rootful/rootless, Docker Desktop macOS arm64/amd64, architecture aliases, seccomp
`default`/`builtin`, missing/null/empty optional maps, natural 125, exit 137, failed start, and
running-unavailable states.

Regenerate it with `make empirica-docker-fixtures`; the generator intentionally imports no
Empirica planner, admission, or runtime module. Tests hydrate only request-specific immutable values
(image, argv, environment, labels, operation IDs) into the independently pinned static shape and
must mutate the shape rather than generate Docker semantics from `planner.py`.
