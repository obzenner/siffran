#!/usr/bin/env python3
"""Validate the normative current-only Docker spike support contract and code mappings.

It also owns the one agent-visible limitation set, public-contract clause
``evidence/spike/limits``: the clause text is rendered here from the code constants, and a
mismatch fails. ``--write`` rewrites only that clause in the root public contract (then run
``make vendor-contracts``).

Every check raises explicitly, so ``python -O`` cannot strip it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "contracts" / "empirica-docker-spike" / "v1"
PUBLIC_CONTRACT = ROOT / "contracts" / "empirica" / "v2" / "public-contract.json"
RESPONSE_SCHEMA = ROOT / "contracts" / "empirica" / "v2" / "response.schema.json"
LIMITS_ID = "evidence/spike/limits"
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))

from adapters.sandbox import generic_qualification, planner, probe_image  # noqa: E402
from application import generic_spike  # noqa: E402
from core import spike_plan  # noqa: E402

# One hint per agent-visible spike Fault reason; it must cover the closed set exactly.
REASON_HINTS = {
    "image-not-local": "the image is not in the local daemon; ask the operator to provide it (the host never pulls or builds)",
    "image-reference-ambiguous": "the reference matches more than one local image; use a full image ID or repo@sha256 digest",
    "image-platform-mismatch": "the image is not Linux on the daemon's native architecture; choose a native image",
    "image-declares-volumes": "the image declares VOLUME; choose an image without declared volumes",
    "malformed-required-result": "the host could not read a well-formed image or capture answer; retry once, then redesign",
    "authority-drift": "Docker is unavailable, or Docker, the qualified binding, or the image the reference names changed; pin a full image ID or repo@sha256 and request again, or ask the operator to start Docker or requalify; never use a host shell",
    "capture-invalid": "inputs changed, are missing, or exceed the capture limits; fix inputs and request again",
    "spikes-unavailable": "this run has no qualified Docker backend; spikes need a new run after the operator qualifies one",
    "authority-invalid": "Docker or the host spike runtime was unavailable when the spike started (including another spike holding it); retry once, then ask the operator to start Docker or requalify; never use a host shell",
    "lifecycle-unavailable": "the container did not reach one admissible natural exit (exit 137, OOM, timeout, output overflow, or a staging, network-create, start, inspect, or static-policy failure); narrow the workload, or retry once if the cause was host-side (staging, network create, or static policy)",
    "cleanup-unavailable": "the next spike retries recovery automatically; if it persists, ask the operator (private operation ID)",
    "postflight-changed": "the Docker authority changed during the spike; request again after it is stable",
    # Empirica 7.2 §5.1 environment/qualification reasons (E6b): declared on the wire with no
    # producer yet; the next-call wording mirrors the spec's reason table.
    "environment-required": "a spike was requested with no binding and no approved environment revision; submit an environment proposal",
    "environment-pending": "the environment revision is pending on the human; nothing for the agent to do until they answer",
    "environment-rejected": "the latest environment revision was rejected; submit a revised environment proposal",
    "qualification-required": "an approved revision has nothing bound; request qualification",
    "qualification-stale-implementation": "the loaded release differs from the binding; request qualification again",
    "qualification-stale-daemon": "the Docker endpoint, daemon identity, version or capability differs from the binding; prepare, then request qualification again",
    "image-not-qualified": "the spike's image is not one of the binding's sealed image IDs; submit an environment proposal that names it",
    "probe-image-untrusted": "the local probe image does not match the pinned probe image digest; pull the pinned reference the refusal renders, then request qualification again",
    "qualification-probe-failed": "a qualification probe failed (the reason names it); prepare, then request qualification again",
    "qualification-deadline-exceeded": "the qualification attempt missed the one deadline covering recovery, inspection, the probes, cleanup and publication; nothing bound; request qualification again once the host is steady",
    "network-egress-unavailable": "the qualification could not establish the networked profile's outbound egress; prepare (check the daemon's network access), then request qualification again",
    "docker-unreachable": "no Docker daemon is reachable at the endpoint; prepare, then request qualification again",
    "profile-not-allowed": "the requested profile is above the host ceiling; propose one within the ceiling",
    "qualification-attempts-exhausted": "the run's qualification attempts are spent; continue on the current binding, stop, or start a fresh run",
    "qualification-conflict": "the attempt lost the binding race or another attempt is running; request qualification again",
    "superseded-binding": "the spike request outlived its binding; resubmit the spike request under the new binding",
}


# Phrases of the superseded G10/G11 Docker design; current normative text must never carry them. The
# old probe-image configuration key survived as an assignment (``PROBE_IMAGE=x``): the guarded spelling keeps
# the ``probe_image`` contract field (§4.3 of Empirica 7.2) distinct from the retired configuration form.
STALE_PHRASES = ("generic-sandbox-qualification", "probe_image=", "probe_config", "seven-probe",
                 "seven ordered probe", "600-second", "census equality", "service exits",
                 "declared internal private", "nonzero derives", "R7 resource policy")
STALE_SCAN = ("contracts", "plugins/empirica/vendor/contracts", "doc", "plugins/empirica/skills",
              "plugins/empirica/adapters/codex/README.md", "plugins/empirica/adapters/pi/README.md",
              "README.md")
# Historical records that describe the superseded design on purpose.
STALE_EXEMPT = ("doc/design/specs/empirica-2.0-d5-evidence-workspace-spike.md", "doc/adr/")


class ContractError(Exception):
    pass


def require(condition: object, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load(name: str) -> object:
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


def _mib(value: int) -> str:
    require(value % (1024 * 1024) == 0, f"{value} is not a whole MiB")
    return f"{value // (1024 * 1024)} MiB"


def render_limits() -> str:
    """The `evidence/spike/limits` clause text, derived from the enforced constants."""
    policy = spike_plan.FRAMEWORK_SPIKE_POLICY
    create = " ".join(planner.CONTAINER_CREATE_FLAGS)
    require("--privileged" not in create and "--publish" not in create
            and "--device" not in create and "--volume" not in create,
            "planner admits a never-available create flag")
    require(policy.memory_swap_bytes == policy.memory_bytes, "swap is not disabled")
    require(policy.stdout_bytes == policy.stderr_bytes, "streams differ")
    fault_reasons = tuple(generic_spike.SPIKE_FAULT_REASONS)
    require(set(REASON_HINTS) == set(fault_reasons) and len(fault_reasons) == len(REASON_HINTS),
            "REASON_HINTS must cover the closed spike Fault reasons exactly")
    require(all(re.fullmatch(r"[a-z]+(-[a-z]+)+", name) for name in fault_reasons),
            "spike Fault reasons must use one hyphenated spelling")
    rows = [
        "Agent chooses | any already-local image reference (name:tag, repo@sha256 digest, full "
        "image ID, or unambiguous ID prefix); exact argv, any tool, no shell unless written; "
        f"inputs as repository-relative files or directories; up to {spike_plan.MAX_ENVIRONMENT} "
        "environment overrides, never secrets, HOME and TMPDIR reserved",
        f"Host fixes | one container; uid/gid {spike_plan.FIXED_UID}/{spike_plan.FIXED_GID}; "
        f"{spike_plan.FIXED_WORKDIR} {_mib(policy.work_tmpfs_bytes)} exec tmpfs = HOME = workdir; "
        f"{spike_plan.FIXED_TMPDIR} {_mib(policy.tmp_tmpfs_bytes)} noexec; /dev/shm "
        f"{_mib(policy.shm_bytes)}; read-only rootfs; {spike_plan.FIXED_INPUTS} read-only; "
        f"CPU min({policy.max_cpu_count}, NCPU); {policy.memory_bytes // 1024 ** 3} GiB memory, "
        f"no swap; {policy.pids} PIDs; nofile {policy.open_files}; fsize "
        f"{_mib(policy.max_file_size_bytes)}; core {policy.core_bytes}; "
        f"{policy.workload_timeout_seconds} s workload; {_mib(policy.stdout_bytes)} stdout and "
        f"{_mib(policy.stderr_bytes)} stderr; capture {policy.staging_files} files, "
        f"{_mib(policy.staging_bytes)} total, {_mib(policy.staging_file_bytes)} per file; host-owned profile: docker-isolated-v1 has no network, "
        "docker-networked-v1 is outbound only with no published ports",
        "Never available | image pull, build, load, or login; published ports; devices; host "
        "mounts; privileged mode; the Docker socket; more than one container; cross-architecture "
        "images; images that declare VOLUME",
        "Verdicts | natural exit 0 is PASS; any other natural exit except 137 is FAIL; 137, OOM, "
        "timeout, output overflow, or any ambiguity is unavailable and admits no evidence",
        "Refusals | an unavailable spike Fault carries one closed reason whenever the cause is "
        "attributable; a Fault without a reason is an internal host failure: retry once, then "
        "ask the operator",
        "Environment step | coming: a spike becomes admissible only through the run's own "
        "environment cycle (environment proposes, the human or the approval provenance approves, "
        "prepare, qualify binds) with the attempts a run may start bounded in the run view; until "
        "the cycle lands, every environment and qualify action refuses with the closed reason "
        "environment.not_available and the run keeps its current spike backend",
        "Probe image | the qualification probes run one host-trusted probe image pinned by digest "
        "in the Docker spike support contract (a name:tag@sha256 reference); preparation pulls "
        "it, admission requires the pinned digest in the image's repository digests, and a probe "
        "image that is not the pinned one is refused as untrusted, never silently accepted",
    ]
    rows.extend(f"reason {name} | {REASON_HINTS[name]}" for name in fault_reasons)
    return "\n".join(rows)


def _limits_clause(contract: dict) -> dict:
    clauses = contract["sections"]["evidence/spike"]["clauses"]
    rows = [row for row in clauses if row["id"] == LIMITS_ID]
    require(len(rows) == 1, f"public contract must carry exactly one {LIMITS_ID} clause")
    return rows[0]


def write_limits() -> None:
    raw = PUBLIC_CONTRACT.read_text(encoding="utf-8")
    current = _limits_clause(json.loads(raw))["text"]
    old = json.dumps(current, ensure_ascii=False)
    require(raw.count(old) == 1, "limits clause text is not uniquely located")
    PUBLIC_CONTRACT.write_text(
        raw.replace(old, json.dumps(render_limits(), ensure_ascii=False)), encoding="utf-8")


def check_limits() -> None:
    contract = json.loads(PUBLIC_CONTRACT.read_text(encoding="utf-8"))
    require(_limits_clause(contract)["text"] == render_limits(),
            f"{LIMITS_ID} drifted from the code constants; run make empirica-spike-limits")
    schema = json.loads(RESPONSE_SCHEMA.read_text(encoding="utf-8"))
    fault = schema["$defs"]["fault"]["properties"]["reason"]["enum"]
    require(fault == list(generic_spike.SPIKE_FAULT_REASONS),
            "response.schema.json fault.reason differs from SPIKE_FAULT_REASONS")
    require(set(generic_spike.QUALIFICATION_FAILURE_REASONS)
            <= set(generic_spike.SPIKE_FAULT_REASONS),
            "the closed qualification failure set must stay a subset of the Fault reasons")
    last_failure = schema["$defs"]["qualificationView"]["properties"]["last_failure"]
    failed = next(branch for branch in last_failure["oneOf"]
                  if branch.get("properties") and "reason" in branch["properties"])
    require(failed["properties"]["reason"]["enum"]
            == list(generic_spike.QUALIFICATION_FAILURE_REASONS),
            "response.schema.json qualification.last_failure.reason differs from the closed "
            "attempt-failure set (Empirica 7.2 §5.1: every attempt failure plus the kept deadline, "
            "egress and cleanup reasons, never an admission reason)")


def check_stale_text(root_dir: Path = ROOT) -> None:
    for root in STALE_SCAN:
        base = root_dir / root
        require(base.exists(), f"stale-text scan root {root} is absent")
        paths = [base] if base.is_file() else sorted(
            item for item in base.rglob("*") if item.is_file() and item.suffix in {".md", ".json"})
        for path in paths:
            relative = path.relative_to(root_dir).as_posix()
            if relative.startswith(STALE_EXEMPT):
                continue
            text = path.read_text(encoding="utf-8").lower()
            for phrase in STALE_PHRASES:
                require(phrase not in text, f"{relative} carries stale Docker text {phrase!r}")
    targets = set(re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):", (root_dir / "Makefile").read_text(
        encoding="utf-8"), re.MULTILINE))
    contract = (root_dir / PUBLIC_CONTRACT.relative_to(ROOT)).read_text(encoding="utf-8")
    for target in sorted(set(re.findall(r"\bmake ([a-z][a-z0-9-]+)", contract))):
        require(target in targets, f"public contract names absent Make target {target}")


def check_support_contract() -> None:
    contract = load("support-contract.json")
    schema = load("support-contract.schema.json")
    try:
        jsonschema.Draft202012Validator(schema).validate(contract)
    except jsonschema.ValidationError as exc:
        raise ContractError(
            f"support-contract.json violates support-contract.schema.json at "
            f"{list(exc.absolute_path)}: {exc.message}") from exc

    profiles = contract["host_support"]["profiles"]
    require(profiles == sorted(spike_plan.NETWORK_PROFILES), "profiles differ")
    require(contract["qualification"]["probes"] == list(generic_qualification.PROBE_ORDER),
            "qualification probes differ")
    from core import run as core_run
    require(core_run.QUALIFICATION_PROBE_NAMES == generic_qualification.PROBE_ORDER,
            "core.run's mirrored probe names differ from the qualification probe order")
    require(core_run.QUALIFICATION_PROBE_FAILED_REASON
            == generic_spike.GenericUnavailableReason.QUALIFICATION_PROBE_FAILED.value,
            "core.run's mirrored probe-failure reason differs from its application owner")
    require(generic_qualification.TOTAL_QUALIFICATION_SECONDS
            == contract["qualification"]["deadline_seconds"], "qualification deadline differs")

    # The one trusted probe image is digest-pinned to one OCI index digest (Empirica 7.2 §4.3).
    try:
        pinned = probe_image.parse_probe_image(contract["qualification"]["probe_image"])
    except KeyError as exc:
        raise ContractError("qualification.probe_image is required (Empirica 7.2 §4.3)") from exc
    except probe_image.ProbeImageError as exc:
        raise ContractError(f"qualification.probe_image is invalid: {exc}") from exc
    require(pinned == probe_image.pinned_probe_image(),
            "the vendored Docker support contract pin drifted from contracts/; run make vendor-contracts")

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
        require(policy[policy_key] == fixed[contract_key],
                f"{policy_key}={policy[policy_key]} but contract {contract_key}={fixed[contract_key]}")
    require(policy["stdout_bytes"] == policy["stderr_bytes"] == fixed["stream_bytes"],
            "stream bytes differ")
    require(spike_plan.BACKEND_OPERATION_TIMEOUT_SECONDS == fixed["operation_seconds"],
            "operation seconds differ")
    require((spike_plan.FIXED_UID, spike_plan.FIXED_GID, spike_plan.FIXED_WORKDIR,
             spike_plan.FIXED_INPUTS, spike_plan.FIXED_TMPDIR) == (
                fixed["uid"], fixed["gid"], fixed["workdir"], fixed["inputs"], fixed["tmpdir"]),
            "fixed identity or mounts differ")

    require("--pid" not in planner.CONTAINER_CREATE_FLAGS, "--pid is a create flag")
    require("--uts" not in planner.CONTAINER_CREATE_FLAGS, "--uts is a create flag")
    require({"--ipc", "--cgroupns", "--cidfile"}.issubset(planner.CONTAINER_CREATE_FLAGS),
            "required private namespace or cidfile flags are absent")

    # Every refusal code names a reason the code actually emits.
    emitted = ({item.value for item in generic_spike.GenericPreparationReason}
               | {item.value for item in generic_spike.GenericUnavailableReason}
               | {item.value for item in generic_qualification.QualificationReason})
    for code in contract["image_resolution"]["refusals"]:
        require(code in emitted, f"image refusal {code} is not an emitted reason")

    mappings = contract["adapter_mapping"]
    ids = [row["id"] for row in mappings]
    required = {
        "default-namespaces", "cidfile-authority", "private-bridge", "running-membership",
        "resource-limits", "natural-verdict", "exact-cleanup", "observer-privacy",
    }
    require(len(ids) == len(set(ids)) and set(ids) == required, "adapter mapping ids differ")
    for row in mappings:
        # The observer never refuses: it omits a malformed historical proof from projection.
        if row["id"] == "observer-privacy":
            require(row["refusal_reason"] == "malformed-historical-proof-omitted",
                    "observer-privacy names an omission, not an emitted reason")
            continue
        require(row["refusal_reason"] in emitted,
                f"{row['id']} refusal_reason {row['refusal_reason']} is not an emitted reason")
        evidence = row["evidence"].split("#", 1)[0]
        if evidence.startswith(("plugins/", "contracts/")):
            require((ROOT / evidence).is_file(), f"evidence {evidence} is absent")

    require(contract["observer"]["schema"] == "empirica-ui-view/v1", "observer schema differs")
    require(contract["status"] == "internal-current-only", "status differs")
    require(all(row["status"] == "live-qualification-required"
                for row in contract["release_matrix"]), "a release cell claims qualification")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-limits", action="store_true",
                        help=f"rewrite the root {LIMITS_ID} clause from the code constants")
    arguments = parser.parse_args()
    try:
        if arguments.write_limits:
            write_limits()
            print(f"docker-spike-support-contract: rewrote {LIMITS_ID}")
            return 0
        check_support_contract()
        check_limits()
        check_stale_text()
    except ContractError as exc:
        print(f"docker-spike-support-contract: {exc}", file=sys.stderr)
        return 1
    print("docker-spike-support-contract: valid (8 mapped obligations; refusal codes emitted; "
          f"{LIMITS_ID} current; no stale text; all release cells live-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
