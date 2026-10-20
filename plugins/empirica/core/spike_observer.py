"""Closed historical decoder for flat single-container ``/2`` spike observer facts.

This module deliberately owns immutable protocol literals and broad safety bounds.  It never
consults the current runtime policy object, Docker authority, or filesystem, so an admitted
historical artifact remains readable after current constants change.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .canonical import canonical_digest

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ENVIRONMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PROFILES = {"docker-isolated-v1", "docker-networked-v1"}
_PLAN_FIELDS = {
    "schema", "draft_digest", "image", "argv", "inputs", "captured_files",
    "capture_digest", "environment", "engine_ncpu", "cpu_count", "uid", "gid",
    "workdir", "inputs_mount", "tmpdir", "network_profile", "policy_digest",
}
_POLICY_FIELDS = {
    "schema", "max_cpu_count", "memory_bytes", "memory_swap_bytes", "pids",
    "open_files", "max_file_size_bytes", "core_bytes", "workload_timeout_seconds",
    "stdout_bytes", "stderr_bytes", "staging_files", "staging_bytes",
    "staging_file_bytes", "work_tmpfs_bytes", "tmp_tmpfs_bytes", "shm_bytes",
}
_OPERATION_FIELDS = {
    "operation_digest", "qualification_record_digest", "execution_binding_digest",
    "image_digest", "input_digest", "process_digest", "topology_digest", "timing_digest",
}


def _plain(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _closed(value: object, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} is not a closed object")
    return value


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} is not a digest")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside its historical bound")
    return value


def _text(value: object, label: str, maximum: int = 4096, *, nonempty: bool = False) -> str:
    if type(value) is not str or "\x00" in value or (nonempty and not value):
        raise ValueError(f"{label} is not bounded text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError(f"{label} is not UTF-8") from exc
    if size > maximum:
        raise ValueError(f"{label} exceeds its historical bound")
    return value


def _path(value: object, label: str) -> str:
    text = _text(value, label, 1024, nonempty=True)
    parts = text.split("/")
    if (text.startswith("/") or "\\" in text or ":" in parts[0]
            or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError(f"{label} is not repository-relative")
    return text


def _canonical_architecture(value: object) -> str:
    raw = _text(value, "historical architecture", 128, nonempty=True)
    return {"aarch64": "arm64", "x86_64": "amd64"}.get(raw, raw)


def backend(value: object) -> dict[str, str]:
    row = _closed(value, {"kind", "backend", "profile_id", "qualification_record_digest",
                          "execution_binding_digest"}, "historical spike backend")
    if (row["kind"] != "qualified" or row["backend"] != "docker"
            or row["profile_id"] not in _PROFILES):
        raise ValueError("historical spike backend differs")
    return {
        "profile_id": row["profile_id"],
        "qualification_record_digest": _digest(
            row["qualification_record_digest"], "historical qualification identity"),
        "execution_binding_digest": _digest(
            row["execution_binding_digest"], "historical execution identity"),
    }


def _draft(value: object) -> dict[str, Any]:
    row = _closed(value, {"schema", "image", "argv", "inputs", "environment"},
                  "historical spike draft")
    if row["schema"] != "empirica.spike-plan-draft/2":
        raise ValueError("historical spike draft schema differs")
    image = _text(row["image"], "historical requested image", 255, nonempty=True)
    if type(row["argv"]) not in {list, tuple} or not 1 <= len(row["argv"]) <= 256:
        raise ValueError("historical argv is not bounded")
    argv = [_text(item, "historical argv item", nonempty=index == 0)
            for index, item in enumerate(row["argv"])]
    if type(row["inputs"]) not in {list, tuple} or len(row["inputs"]) > 256:
        raise ValueError("historical input declarations are not bounded")
    inputs = [_path(item[:-1] if isinstance(item, str) and item.endswith("/") else item,
                    "historical input declaration") +
              ("/" if isinstance(item, str) and item.endswith("/") else "")
              for item in row["inputs"]]
    if inputs != sorted(set(inputs)):
        raise ValueError("historical input declarations are not canonical")
    if not isinstance(row["environment"], Mapping) or len(row["environment"]) > 128:
        raise ValueError("historical environment is not bounded")
    environment: dict[str, str] = {}
    for name, item in row["environment"].items():
        if type(name) is not str or _ENVIRONMENT.fullmatch(name) is None or name in {"HOME", "TMPDIR"}:
            raise ValueError("historical environment name differs")
        environment[name] = _text(item, "historical environment value")
    if list(environment) != sorted(environment):
        raise ValueError("historical environment is not canonical")
    return {"schema": row["schema"], "image": image, "argv": argv,
            "inputs": inputs, "environment": environment}


def _policy(value: object, expected_digest: object) -> dict[str, Any]:
    row = dict(_closed(value, _POLICY_FIELDS, "historical spike policy"))
    if row["schema"] != "empirica.spike-plan-policy/2":
        raise ValueError("historical spike policy schema differs")
    scalar = {
        "max_cpu_count": (1, 1024), "memory_bytes": (1, 1 << 50),
        "memory_swap_bytes": (1, 1 << 50), "pids": (1, 1_000_000),
        "open_files": (1, 1_000_000), "max_file_size_bytes": (1, 1 << 50),
        "core_bytes": (0, 1 << 50), "workload_timeout_seconds": (1, 86_400),
        "stdout_bytes": (0, 1 << 50), "stderr_bytes": (0, 1 << 50),
        "staging_files": (0, 1_000_000), "staging_bytes": (0, 1 << 50),
        "staging_file_bytes": (0, 1 << 50), "work_tmpfs_bytes": (1, 1 << 50),
        "tmp_tmpfs_bytes": (1, 1 << 50), "shm_bytes": (1, 1 << 50),
    }
    for name, bounds in scalar.items():
        _integer(row[name], f"historical policy {name}", *bounds)
    if (row["memory_swap_bytes"] != row["memory_bytes"] or row["core_bytes"] != 0
            or row["staging_file_bytes"] > row["staging_bytes"]
            or row["max_file_size_bytes"] != row["staging_file_bytes"]
            or row["work_tmpfs_bytes"] + row["tmp_tmpfs_bytes"] + row["shm_bytes"]
            > row["memory_bytes"]):
        raise ValueError("historical policy relationships differ")
    if _digest(expected_digest, "historical policy identity") != canonical_digest(row):
        raise ValueError("historical policy identity differs")
    return row


def _captured(value: object, policy: Mapping[str, Any], declarations: list[str]) -> list[dict[str, Any]]:
    if type(value) not in {list, tuple} or len(value) > policy["staging_files"]:
        raise ValueError("historical captured inputs are not bounded")
    rows: list[dict[str, Any]] = []
    for item in value:
        row = _closed(item, {"path", "sha256", "bytes"}, "historical captured input")
        path = _path(row["path"], "historical captured path")
        if not any(path == base.rstrip("/") or path.startswith(base.rstrip("/") + "/")
                   for base in declarations):
            raise ValueError("historical captured path has no declaration")
        rows.append({"path": path, "sha256": _digest(row["sha256"], "historical file identity"),
                     "bytes": _integer(row["bytes"], "historical file bytes", 0,
                                       policy["staging_file_bytes"])})
    if [row["path"] for row in rows] != sorted({row["path"] for row in rows}) \
            or sum(row["bytes"] for row in rows) > policy["staging_bytes"]:
        raise ValueError("historical captured input order or aggregate differs")
    if not declarations and rows:
        raise ValueError("historical empty input declaration has files")
    return rows


def request(art: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and project one immutable flat ``/2`` request."""
    draft = _draft(art["draft"])
    draft_digest = _digest(art["draft_digest"], "historical draft identity")
    if canonical_digest(draft) != draft_digest:
        raise ValueError("historical draft identity differs")
    policy = _policy(art["policy"], art["policy_digest"])
    plan = dict(_closed(_plain(art["plan"]), _PLAN_FIELDS, "historical sealed plan"))
    if plan["schema"] != "empirica.spike-plan/2":
        raise ValueError("historical sealed plan schema differs")
    if plan["draft_digest"] != draft_digest or plan["policy_digest"] != art["policy_digest"]:
        raise ValueError("historical plan binding differs")
    image = _closed(plan["image"], {"requested_reference", "id", "raw_os", "os",
                                    "raw_architecture", "architecture", "declared_volumes"},
                    "historical sealed image")
    raw_os = _text(image["raw_os"], "historical image OS", 128, nonempty=True)
    raw_arch = _text(image["raw_architecture"], "historical image architecture", 128,
                     nonempty=True)
    if (image["requested_reference"] != draft["image"]
            or type(image["declared_volumes"]) not in {list, tuple}
            or len(image["declared_volumes"]) != 0
            or image["os"] != raw_os or image["architecture"] != _canonical_architecture(raw_arch)):
        raise ValueError("historical image realization differs")
    image_id = _digest(image["id"], "historical sealed image identity")
    if (list(plan["argv"]) != draft["argv"] or list(plan["inputs"]) != draft["inputs"]
            or dict(plan["environment"]) != draft["environment"]):
        raise ValueError("historical sealed plan differs from its draft")
    captured = _captured(plan["captured_files"], policy, draft["inputs"])
    capture_digest = _digest(plan["capture_digest"], "historical capture identity")
    if canonical_digest(captured) != capture_digest:
        raise ValueError("historical capture identity differs")
    engine_ncpu = _integer(plan["engine_ncpu"], "historical engine CPU count", 1, 1_000_000)
    cpu_count = _integer(plan["cpu_count"], "historical CPU count", 1, policy["max_cpu_count"])
    bound_backend = backend(art["backend"])
    if (cpu_count != min(policy["max_cpu_count"], engine_ncpu)
            or plan["uid"] != 65534 or plan["gid"] != 65534
            or plan["workdir"] != "/work" or plan["inputs_mount"] != "/inputs"
            or plan["tmpdir"] != "/tmp" or plan["network_profile"] != bound_backend["profile_id"]):
        raise ValueError("historical fixed runtime facts differ")
    plan_digest = _digest(art["plan_digest"], "historical plan identity")
    if canonical_digest(plan) != plan_digest:
        raise ValueError("historical plan identity differs")
    operation_timeout = _integer(
        art["operation_timeout_seconds"], "historical operation timeout", 1, 86_400)
    if operation_timeout != 210:
        raise ValueError("historical operation timeout differs")
    identities = {
        "draft_digest": draft_digest,
        "plan_digest": plan_digest,
        "policy_digest": art["policy_digest"],
        "snapshot_basis_id": _digest(art["snapshot_basis_id"], "historical snapshot basis"),
        "snapshot_digest": _digest(art["snapshot_digest"], "historical snapshot identity"),
        "capture_digest": capture_digest,
        "qualification_record_digest": bound_backend["qualification_record_digest"],
        "execution_binding_digest": bound_backend["execution_binding_digest"],
    }
    limits = {name: policy[name] for name in (
        "memory_bytes", "memory_swap_bytes", "pids", "open_files", "max_file_size_bytes",
        "core_bytes", "workload_timeout_seconds", "stdout_bytes", "stderr_bytes",
        "staging_files", "staging_bytes", "staging_file_bytes", "work_tmpfs_bytes",
        "tmp_tmpfs_bytes", "shm_bytes")}
    limits.update(cpu_count=cpu_count, operation_timeout_seconds=operation_timeout)
    projected = {
        "artifact_id": _digest(art["artifact_id"], "historical request artifact"),
        "harness_request_id": _digest(art["harness_request_id"], "historical harness request"),
        "claim_id": _text(art["claim_id"], "historical claim", 64, nonempty=True),
        "draft_schema": draft["schema"],
        "image": {"requested_reference": draft["image"], "sealed_id": image_id,
                  "raw_os": raw_os, "os": image["os"],
                  "raw_architecture": raw_arch, "architecture": image["architecture"]},
        "argv": list(draft["argv"]), "declared_inputs": list(draft["inputs"]),
        "inputs": captured, "input_count": len(captured),
        "input_bytes": sum(item["bytes"] for item in captured),
        "environment_names": sorted(draft["environment"]),
        "profile_id": bound_backend["profile_id"],
        "runtime": {"uid": 65534, "gid": 65534, "workdir": "/work",
                    "inputs_mount": "/inputs", "tmpdir": "/tmp"},
        "limits": limits, "identities": identities,
    }
    return projected, {"backend": bound_backend, "plan": plan, "policy": policy,
                       "projected": projected}


def _stream(value: object, label: str, maximum: int) -> dict[str, Any]:
    row = _closed(value, {"bytes_seen", "sha256"}, f"historical {label} stream")
    return {"bytes_seen": _integer(row["bytes_seen"], f"historical {label} bytes", 0, maximum),
            "sha256": _digest(row["sha256"], f"historical {label} identity")}


def result(art: Mapping[str, Any], request_context: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and project one admitted flat result, excluding destructive/private facts."""
    request_view = request_context["projected"]
    request_backend = request_context["backend"]
    facts = _closed(_plain(art["result_facts"]), {
        "run_id", "backend", "plan_digest", "policy_digest", "snapshot_digest",
        "operation_facts", "stdout", "stderr", "natural_exit", "forced_termination",
        "exit_code", "verdict", "cleanup_proven", "postflight_identity_digest",
        "harness_result_digest",
    }, "historical spike result")
    if _IDENTIFIER.fullmatch(_text(facts["run_id"], "historical execution run", 64,
                                   nonempty=True)) is None:
        raise ValueError("historical execution run identity differs")
    result_backend = backend(facts["backend"])
    if (result_backend != request_backend or art["backend"] != facts["backend"]
            or facts["plan_digest"] != request_view["identities"]["plan_digest"]
            or facts["policy_digest"] != request_view["identities"]["policy_digest"]
            or facts["snapshot_digest"] != request_view["identities"]["snapshot_digest"]):
        raise ValueError("historical result request binding differs")
    operation = dict(_closed(facts["operation_facts"], _OPERATION_FIELDS,
                             "historical operation facts"))
    for name in _OPERATION_FIELDS:
        operation[name] = _digest(operation[name], f"historical operation {name}")
    if (operation["qualification_record_digest"] != request_backend["qualification_record_digest"]
            or operation["execution_binding_digest"] != request_backend["execution_binding_digest"]
            or operation["input_digest"] != facts["snapshot_digest"]):
        raise ValueError("historical operation authority differs")
    stdout = _stream(facts["stdout"], "stdout", request_view["limits"]["stdout_bytes"])
    stderr = _stream(facts["stderr"], "stderr", request_view["limits"]["stderr_bytes"])
    exit_code = _integer(facts["exit_code"], "historical natural exit", 0, 255)
    verdict = "PASS" if exit_code == 0 else "FAIL"
    if (exit_code == 137 or facts["natural_exit"] is not True
            or facts["forced_termination"] is not False or facts["verdict"] != verdict
            or facts["cleanup_proven"] is not True
            or facts["postflight_identity_digest"] != request_backend["execution_binding_digest"]):
        raise ValueError("historical result verdict or postflight differs")
    harness_result_digest = _digest(
        facts["harness_result_digest"], "historical harness result identity")
    digest_body = dict(facts)
    digest_body.pop("harness_result_digest")
    if canonical_digest(digest_body) != harness_result_digest:
        raise ValueError("historical harness result identity differs")
    outcome = "pass" if verdict == "PASS" else "fail"
    if (art["outcome"] != outcome or art["spike_gate"] != verdict
            or art["exit_code"] != exit_code
            or art["request_artifact_id"] != request_view["artifact_id"]
            or art["harness_request_id"] != request_view["harness_request_id"]
            or art["plan_digest"] != facts["plan_digest"]
            or art["policy_digest"] != facts["policy_digest"]
            or art["snapshot_digest"] != facts["snapshot_digest"]):
        raise ValueError("historical result artifact differs from admitted facts")
    return {
        "artifact_id": _digest(art["artifact_id"], "historical result artifact"),
        "request_artifact_id": request_view["artifact_id"],
        "harness_request_id": request_view["harness_request_id"],
        "outcome": outcome, "gate": verdict, "exit_code": exit_code,
        "profile_id": request_backend["profile_id"],
        "realization_image_digest": operation["image_digest"],
        "stdout": stdout, "stderr": stderr, "natural_exit": True,
        "cleanup_proven": True, "postflight_proven": True,
        "postflight_identity_digest": facts["postflight_identity_digest"],
        "harness_result_digest": harness_result_digest,
        "identities": {
            "operation_digest": operation["operation_digest"],
            "request_artifact_id": request_view["artifact_id"],
            "plan_digest": facts["plan_digest"], "policy_digest": facts["policy_digest"],
            "snapshot_digest": facts["snapshot_digest"],
            "qualification_record_digest": operation["qualification_record_digest"],
            "execution_binding_digest": operation["execution_binding_digest"],
            "input_digest": operation["input_digest"],
            "process_digest": operation["process_digest"],
            "topology_digest": operation["topology_digest"],
            "timing_digest": operation["timing_digest"],
        },
    }
