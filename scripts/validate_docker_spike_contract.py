#!/usr/bin/env python3
"""Validate the normative current-only Docker spike support contract and code mappings."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "contracts" / "empirica-docker-spike" / "v1"
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))

from adapters.sandbox import generic_qualification, planner  # noqa: E402
from core import spike_plan  # noqa: E402


def load(name: str) -> object:
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


def main() -> int:
    contract = load("support-contract.json")
    schema = load("support-contract.schema.json")
    jsonschema.Draft202012Validator(schema).validate(contract)

    profiles = contract["host_support"]["profiles"]
    assert profiles == ["docker-isolated-v1", "docker-networked-v1"]
    assert contract["qualification"]["probes"] == list(generic_qualification.PROBE_ORDER)
    assert generic_qualification.TOTAL_QUALIFICATION_SECONDS == contract["qualification"]["deadline_seconds"]

    policy = spike_plan.FRAMEWORK_SPIKE_POLICY.to_data()
    expected_policy = {
        "max_cpu_count": "cpu_max", "memory_bytes": "memory_bytes",
        "memory_swap_bytes": "memory_swap_bytes", "pids": "pids",
        "open_files": "nofile", "max_file_size_bytes": "fsize_bytes",
        "core_bytes": "core_bytes", "workload_timeout_seconds": "workload_seconds",
        "staging_files": "capture_files", "staging_bytes": "capture_bytes",
        "staging_file_bytes": "capture_file_bytes", "work_tmpfs_bytes": "work_tmpfs_bytes",
        "tmp_tmpfs_bytes": "tmp_tmpfs_bytes", "shm_bytes": "shm_bytes",
    }
    fixed = contract["sandbox_policy"]
    for policy_key, contract_key in expected_policy.items():
        assert policy[policy_key] == fixed[contract_key], (policy_key, policy[policy_key], fixed[contract_key])
    assert policy["stdout_bytes"] == policy["stderr_bytes"] == fixed["stream_bytes"]
    assert spike_plan.BACKEND_OPERATION_TIMEOUT_SECONDS == fixed["operation_seconds"]
    assert (spike_plan.FIXED_UID, spike_plan.FIXED_GID, spike_plan.FIXED_WORKDIR,
            spike_plan.FIXED_INPUTS, spike_plan.FIXED_TMPDIR) == (
                fixed["uid"], fixed["gid"], fixed["workdir"], fixed["inputs"], fixed["tmpdir"])

    assert "--pid" not in planner.CONTAINER_CREATE_FLAGS
    assert "--uts" not in planner.CONTAINER_CREATE_FLAGS
    assert {"--ipc", "--cgroupns", "--cidfile"}.issubset(planner.CONTAINER_CREATE_FLAGS)

    mappings = contract["adapter_mapping"]
    ids = [row["id"] for row in mappings]
    required = {
        "default-namespaces", "cidfile-authority", "private-bridge", "running-membership",
        "resource-limits", "natural-verdict", "exact-cleanup", "observer-privacy",
    }
    assert len(ids) == len(set(ids)) and set(ids) == required
    for row in mappings:
        evidence = row["evidence"].split("#", 1)[0]
        if evidence.startswith(("plugins/", "contracts/")):
            assert (ROOT / evidence).is_file(), evidence

    assert contract["observer"]["schema"] == "empirica-ui-view/v1"
    assert contract["status"] == "internal-current-only"
    assert all(row["status"] == "live-qualification-required"
               for row in contract["release_matrix"])
    print("docker-spike-support-contract: valid (8 mapped obligations; all release cells live-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
