"""Single-owner constants for persisted transaction-history record shapes."""

OBSERVATION_BASIS_KIND = "observation_basis"
# Every domain-artifact kind a committed manifest may reference (the history ``traverse_history``
# returns): author evidence (``core.evaluation``), host-observed trusted ingress (``transaction``),
# and the observation basis. Readers that enumerate kinds (the observer read model) are held to it.
HISTORY_KINDS = ("graph", "research", "spike_request", "spike", "attribution", "audit_verdict",
                 OBSERVATION_BASIS_KIND)
OBSERVATION_BASIS_VERSION = 1
OBSERVATION_BASIS_KEYS = frozenset({
    "artifact_id", "kind", "version", "sources", "policy_inputs", "clock_inputs",
})
OBSERVATION_SOURCE_KEYS = frozenset({"name", "basis_id", "digest", "observations"})
OBSERVATION_ROW_KEYS = frozenset({"path", "state", "sha256"})
POLICY_INPUT_KEYS = frozenset({
    "contract_id", "contract_version", "contract_digest", "profile_id",
})

MANIFEST_KIND = "transaction_manifest"
MANIFEST_VERSION = 2
MANIFEST_KEYS = frozenset({
    "artifact_id", "kind", "version", "parent", "artifact_ids",
    "observation_basis_id", "observation_digest", "observation_basis_digest",
    "next_state_digest",
})
