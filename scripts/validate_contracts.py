#!/usr/bin/env python3
"""Validate contract documents and substrate-neutral fixture envelopes.

The single static contract entrypoint. ``main()`` performs all filesystem I/O
and schema dispatch and passes explicit data + error collectors to pure check
functions. No runtime contract engine is created here.

Validated:
  * every ``*.schema.json`` under ``contracts/<protocol>/<version>/``;
  * v1 substrate-neutral fixtures under ``contracts/fixtures/``;
  * the v2 public contract registry, host profiles, and v2 fixtures:
    referential integrity, closed values, exact key sets, child transitions,
    host profile facts, response reason/residual parameters and exact ordered
    next-action/section lists, child recovery actions, schema↔registry mirror,
    banned v1 fields, Allow.converged==status==converged, required digest,
    GetContract target correlation and materialization;
  * in-memory negative cases: one mutation each asserting an expected
    diagnostic substring so a case cannot pass for the wrong rejection reason.
"""
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:  # Structural gates still validate required wire fields.
    Draft202012Validator = None
    Registry = Resource = DRAFT202012 = None

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
V2 = CONTRACTS / "empirica" / "v2"

# --------------------------------------------------------------------------- #
# SSOT: the canonical JSON registries are the single source of truth. Mirror
# checks derive commands/decisions/statuses/actions/child states+transitions/
# reason IDs/next-action IDs/D2A enums+delimiters/mode fields from the loaded
# registry (public-contract.json) and compare them to the request/response
# schemas; no Python vocabulary table duplicates the registry. The registry and
# host-profiles releases are frozen by one compact reviewed SHA-256 digest each
# (a deliberate, reviewed edit is required to drift). Only algorithmic/
# structural constants are retained below.
# --------------------------------------------------------------------------- #
# Compact reviewed digests of the canonical registries (D2A §8/§9). Changing a
# canonical value requires updating the matching digest deliberately.
REVIEWED_REGISTRY_DIGEST = "sha256:bcee507e2daefd5b94cf955ccf3e2a21a9870403f737455f59086f9ee8ca37a9"
REVIEWED_HOST_PROFILES_DIGEST = "sha256:aad8b0b5cad21532a426a564d911c89ee64e4e94a750a1341b6adbbd5a22bebb"
# Structural identity constants (truly frozen, not registry-derived vocabularies).
REGISTRY_ID = "empirica/public"
REGISTRY_VERSION = "2.0.0"
PROTOCOL = "empirica/v2"
# Structural D1 constants that have no canonical-JSON vocabulary of their own: edge
# types and research source kinds. Artifact kind/outcome/spike-gate vocabularies ARE
# canonical-JSON (artifact_kinds/artifact_outcomes/spike_gates in the registry) and are
# derived from the loaded registry, never duplicated here as EXPECTED_* tables.
EDGE_TYPES = {"SupportedBy"}
EVIDENCE_SOURCE_KINDS = {"docs", "code", "runtime", "web"}
# Strict normalized repo-relative POSIX path (shared schema/check). Rejects leading
# slash, drive/colon, backslash/UNC, empty/repeated separators, dot/dotdot segments.
RELATIVE_POSIX_RE = re.compile(
    r"^(?!(?:\.(?:/|$)))(?!(?:\.\.(?:/|$)))(?!.*/\.(?:/|$))(?!.*/\.\.(?:/|$))"
    r"[^\\/:]+(?:/[^\\/:]+)*$")
RANGE_OPS = re.compile(r"[\^~><=]|\*| - |(?:^|[.\d])x(?:[.\s]|$)")
REDACTED_RE = re.compile(r"^<redacted.*>$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# v1/internal fields that must never appear in a public v2 response.
BANNED_RESPONSE_KEYS = {
    "revision", "contract_artifact_id", "nonce", "phase",
    "audit_ticket", "consume_audit_ticket", "void_spawn", "reserve_spawn",
    "ticket", "tickets", "artifact_path", "cas_revision", "workspace_hash",
    "transcript_path", "private_capability", "reservation_id", "reservation_seq",
    "obligation_contract_revision", "obligation_contract_pointer",
    "obligation_contract_history", "obligation_contract_id",
    # D2A §3: no native child id, spent/refunded, first-terminal fingerprint,
    # full contract, or operational counters in any RunView.
    "native_child_id", "native_id", "spent", "refunded",
    "first_terminal_fingerprint", "full_contract", "public_contract",
    "operational_counters", "pass_count", "spawn_count", "derivation_passes",
}


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _as_set(value: Any, where: str, errors: list[str], name: str) -> set:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        errors.append(f"{where}: {name} must be an array of strings")
        return set()
    return set(value)


def registry_digest(registry: dict) -> str:
    """Deterministic canonical SHA-256 of the registry (placeholder; D9 owns runtime digest)."""
    canonical = json.dumps(registry, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def materialize_contract_result(registry: dict, target: str, section_id: str | None = None) -> dict:
    """Build the expected GetContract contract_result from the canonical registry.

    Each closed branch carries the canonical PublicContract digest (computed by the
    existing ``registry_digest`` algorithm) so on-demand discovery can be correlated
    with RunView contract identity. The digest is not embedded inside PublicContract
    itself; it is derived here from the loaded registry, never duplicated as a constant.
    """
    digest = registry_digest(registry)
    if target == "index":
        return {"target": "index", "digest": digest, "index": {
            "id": registry["id"], "version": registry["version"],
            "sections": [{"id": sid, "title": s["title"]} for sid, s in registry["sections"].items()],
            "reasons": [{"code": rc, "sections": r["sections"]} for rc, r in registry["reasons"].items()],
            "next_actions": [{"id": aid, "description": a["description"]}
                              for aid, a in registry["next_actions"].items()],
        }}
    if target == "section":
        s = registry["sections"][section_id]
        return {"target": "section", "digest": digest, "section_id": section_id,
                "section": {"id": section_id, "title": s["title"], "summary": s["summary"],
                            "clauses": s["clauses"]}}
    return {"target": "full", "digest": digest, "full": registry}


def check_getcontract_digest(contract_result: dict, registry: dict, errors: list[str], where: str) -> None:
    """GetContract contract_result.digest must equal the canonical PublicContract digest.

    The digest is computed by the existing ``registry_digest`` algorithm over the loaded
    registry and is exact-equal to RunView ``contract.digest``, so on-demand discovery
    correlates with run identity. The raw schema enforces digest presence/format; this
    procedural check enforces the registry-equality contract_result (fixture correlation).
    The digest value is never duplicated as a constant — it is always derived here.
    """
    expected = registry_digest(registry)
    actual = contract_result.get("digest")
    if actual != expected:
        errors.append(f"{where}: contract_result.digest {actual!r} != registry digest {expected!r}")


# --------------------------------------------------------------------------- #
# Pure v2 checks — each takes explicit (data, errors, where).
# --------------------------------------------------------------------------- #
def check_identity(contract: dict, errors: list[str], where: str) -> None:
    """Structural identity constants (id/version/protocol); vocabulary values are frozen by the
    reviewed digest and mirrored by check_schema_mirror, not by a Python table."""
    for field, want in (("id", REGISTRY_ID), ("version", REGISTRY_VERSION), ("protocol", PROTOCOL)):
        if contract.get(field) != want:
            errors.append(f"{where}: {field} must be {want!r}, got {contract.get(field)!r}")
    for key in ("commands", "decisions", "statuses", "host_tiers", "actions"):
        if key not in contract:
            errors.append(f"{where}: {key} is required")
    actions = contract.get("actions", {})
    if isinstance(actions, dict):
        for sub in ("author", "host", "trusted"):
            if sub not in actions:
                errors.append(f"{where}: actions.{sub} is required")


def check_registry_digest(contract: dict, errors: list[str], where: str) -> None:
    """Freeze the reviewed PublicContract release by one compact canonical SHA-256.

    A deliberate registry edit must update REVIEWED_REGISTRY_DIGEST; this replaces every
    per-vocabulary EXPECTED_* table with a single reviewed assertion.
    """
    actual = registry_digest(contract)
    if actual != REVIEWED_REGISTRY_DIGEST:
        errors.append(f"{where}: registry digest {actual} != reviewed "
                      f"{REVIEWED_REGISTRY_DIGEST}")


def check_key_sets(contract: dict, errors: list[str], where: str) -> None:
    """Structural presence of the keyed registries; exact key sets are frozen by the reviewed
    digest and reason IDs are mirrored by the schema reasonPayload (see check_schema_mirror)."""
    for key in ("next_actions", "reasons", "sections"):
        if not isinstance(contract.get(key), dict) or not contract[key]:
            errors.append(f"{where}: {key} must be a non-empty object keyed by ID")


def check_referential(contract: dict, errors: list[str], where: str) -> None:
    next_actions = contract.get("next_actions", {})
    sections = contract.get("sections", {})
    reasons = contract.get("reasons", {})
    if not isinstance(next_actions, dict):
        errors.append(f"{where}: next_actions must be an object keyed by ID")
        next_actions = {}
    if not isinstance(sections, dict):
        errors.append(f"{where}: sections must be an object keyed by ID")
        sections = {}
    if not isinstance(reasons, dict):
        errors.append(f"{where}: reasons must be an object keyed by ID")
        reasons = {}
    next_ids, section_ids = set(next_actions), set(sections)
    for rid, reason in sorted(reasons.items()):
        if not isinstance(reason, dict):
            errors.append(f"{where}: reasons.{rid} must be an object")
            continue
        for action in reason.get("next_actions", []) or []:
            if action not in next_ids:
                errors.append(f"{where}: reasons.{rid}.next_actions references unknown action {action!r}")
        for section in reason.get("sections", []) or []:
            if section not in section_ids:
                errors.append(f"{where}: reasons.{rid}.sections references unknown section {section!r}")
        if not (reason.get("next_actions") or []):
            errors.append(f"{where}: reasons.{rid} must list at least one next action")
        if not (reason.get("sections") or []):
            errors.append(f"{where}: reasons.{rid} must list at least one section")
    # D2C artifact vocabulary self-consistency: spike_gates is a subset of
    # artifact_outcomes so the per-kind derivation (research = outcomes minus gates,
    # spike = gates) is well-defined, and the spike outcome equals the gate vocabulary.
    artifact_outcomes = _as_set(contract.get("artifact_outcomes", []), where, errors,
                                "artifact_outcomes")
    spike_gates = _as_set(contract.get("spike_gates", []), where, errors, "spike_gates")
    if spike_gates and artifact_outcomes and not spike_gates.issubset(artifact_outcomes):
        errors.append(f"{where}: spike_gates {sorted(spike_gates)} must be a subset of "
                      f"artifact_outcomes {sorted(artifact_outcomes)}")
    if not _as_set(contract.get("artifact_kinds", []), where, errors, "artifact_kinds"):
        errors.append(f"{where}: artifact_kinds must be a nonempty array")
    if not _as_set(contract.get("claim_kinds", []), where, errors, "claim_kinds"):
        errors.append(f"{where}: claim_kinds must be a nonempty array")



def check_clauses(contract: dict, errors: list[str], where: str) -> None:
    sections = contract.get("sections", {})
    if not isinstance(sections, dict):
        return
    seen: dict[str, str] = {}
    for sid, section in sorted(sections.items()):
        if not isinstance(section, dict):
            continue
        for clause in section.get("clauses", []) or []:
            if not isinstance(clause, dict) or not isinstance(clause.get("id"), str):
                errors.append(f"{where}: sections.{sid} has a malformed clause")
                continue
            cid = clause["id"]
            if not cid.startswith(sid + "/"):
                errors.append(f"{where}: clause {cid!r} must be slash-qualified under section {sid!r}")
            if cid in seen:
                errors.append(f"{where}: clause id {cid!r} is not globally unique (also in {seen[cid]!r})")
            seen[cid] = sid


def check_child_lifecycle(contract: dict, errors: list[str], where: str) -> None:
    cl = contract.get("child_lifecycle", {})
    if not isinstance(cl, dict):
        errors.append(f"{where}: child_lifecycle must be an object")
        return
    states = _as_set(cl.get("states", []), where, errors, "child_lifecycle.states")
    terminal = _as_set(cl.get("terminal_states", []), where, errors, "child_lifecycle.terminal_states")
    if not states:
        errors.append(f"{where}: child_lifecycle.states must be nonempty")
    if not terminal.issubset(states):
        errors.append(f"{where}: terminal_states must be a subset of states")
    actual: set[tuple[str, str]] = set()
    for item in cl.get("transitions", []) or []:
        if not isinstance(item, dict) or "from" not in item or "to" not in item:
            errors.append(f"{where}: child transition must have from/to")
            continue
        pair = (item["from"], item["to"])
        if pair in actual:
            errors.append(f"{where}: duplicate child transition {pair}")
        actual.add(pair)
        if item["from"] not in states or item["to"] not in states:
            errors.append(f"{where}: child transition {pair} references unknown state")


def _check_section_list(arr: Any, sections: set, where: str, errors: list[str],
                       require_nonempty: bool = False) -> None:
    """A presentation_selector section list is an ordered array of nonempty, unique,
    registered section IDs. The schema enforces nonempty strings and uniqueItems; this
    procedural check additionally names unresolved and duplicate sections deterministically.
    """
    if not isinstance(arr, list):
        errors.append(f"{where}: must be an array")
        return
    if require_nonempty and not arr:
        errors.append(f"{where}: must be nonempty")
    seen: list[str] = []
    for s in arr:
        if not isinstance(s, str) or not s:
            errors.append(f"{where}: section entries must be nonempty strings, got {s!r}")
            continue
        if s not in sections:
            errors.append(f"{where}: section {s!r} does not resolve to a registered section")
        if s in seen:
            errors.append(f"{where}: duplicate section {s!r}")
        else:
            seen.append(s)


def check_presentation_selector(contract: dict, errors: list[str], where: str) -> None:
    """D2E: validate the canonical presentation_selector registry object.

    The JSON registry (public-contract.json presentation_selector) is the executable SSOT for
    the deterministic presentation selector. This checks structural closure, exact
    operation-context key parity, that every referenced section resolves to a registered
    section, ordered/unique nonempty arrays, and that the terminal status vocabulary
    (statuses minus the non-terminal 'active', per D1 §12) is well-defined. It does NOT
    implement the selector algorithm and does NOT duplicate the registry as a Python
    mapping; reason→section/action referential integrity is owned by check_referential.
    """
    ps = contract.get("presentation_selector")
    if not isinstance(ps, dict):
        errors.append(f"{where}: presentation_selector must be an object")
        return
    expected_keys = {"context_sections", "terminal_sections", "unknown_reason_sections"}
    if set(ps.keys()) != expected_keys:
        errors.append(f"{where}: presentation_selector keys {sorted(ps.keys())} != "
                      f"{sorted(expected_keys)}")
    operation_contexts = set(contract.get("operation_contexts", []))
    sections = set(contract.get("sections", {}))
    contexts = ps.get("context_sections")
    if not isinstance(contexts, dict):
        errors.append(f"{where}: presentation_selector.context_sections must be an object")
        contexts = {}
    if set(contexts.keys()) != operation_contexts:
        errors.append(f"{where}: presentation_selector.context_sections keys "
                      f"{sorted(contexts.keys())} != operation_contexts {sorted(operation_contexts)}")
    for ctx, arr in sorted(contexts.items()):
        _check_section_list(arr, sections,
                            f"{where}: presentation_selector.context_sections[{ctx}]", errors)
    _check_section_list(ps.get("terminal_sections"), sections,
                        f"{where}: presentation_selector.terminal_sections", errors,
                        require_nonempty=True)
    _check_section_list(ps.get("unknown_reason_sections"), sections,
                        f"{where}: presentation_selector.unknown_reason_sections", errors,
                        require_nonempty=True)
    # The terminal status vocabulary is derived from the registry (statuses minus the
    # non-terminal 'active'), never a duplicated Python constant; it must be nonempty so
    # the algorithm's registered-terminal-status branch is well-defined.
    statuses = set(contract.get("statuses", []))
    if "active" not in statuses:
        errors.append(f"{where}: statuses must contain the non-terminal 'active' to derive "
                      f"terminal statuses")
    elif not (statuses - {"active"}):
        errors.append(f"{where}: terminal status vocabulary (statuses minus active) must be nonempty")



def check_host_profiles(profiles_doc: dict, contract: dict, known_fixture_ids: set,
                         errors: list[str], where: str) -> None:
    """Load host profiles as canonical data and check them structurally/referentially; the
    complete reviewed field set is frozen by one compact digest, not a duplicated Python row."""
    host_tiers = set(contract.get("host_tiers", []))
    reason_ids = set(contract.get("reasons", {}))
    profiles = profiles_doc.get("profiles", [])
    if not isinstance(profiles, list):
        errors.append(f"{where}: profiles must be an array")
        return
    if not profiles:
        errors.append(f"{where}: profiles must be nonempty")
    seen_ids: set[str] = set()
    for i, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            errors.append(f"{where}.profiles[{i}]: must be an object")
            continue
        pid = profile.get("profile_id", "?")
        if pid in seen_ids:
            errors.append(f"{where}.profiles[{i}]: duplicate profile_id {pid!r}")
        seen_ids.add(pid)
        pwhere = f"{where}.profiles[{i}] ({pid})"
        version = profile.get("version", "")
        if not isinstance(version, str) or RANGE_OPS.search(version):
            errors.append(f"{pwhere}: version {version!r} must be exact (no range)")
        tier = profile.get("current_tier")
        if tier not in host_tiers:
            errors.append(f"{pwhere}: unknown current_tier {tier!r}")
        cand = profile.get("candidate_tier")
        if cand is not None and cand not in host_tiers:
            errors.append(f"{pwhere}: unknown candidate_tier {cand!r}")
        for fid in profile.get("required_fixture_ids", []) or []:
            if fid not in known_fixture_ids:
                errors.append(f"{pwhere}: required_fixture_id {fid!r} is not a committed v2 fixture")
        for rid in profile.get("unsupported_reason_ids", []) or []:
            if rid not in reason_ids:
                errors.append(f"{pwhere}: unsupported_reason_id {rid!r} is not a known reason")
        if not (profile.get("required_live_probe_ids") or []):
            errors.append(f"{pwhere}: required_live_probe_ids must be nonempty")
        if "candidate_tier" in profile and not (profile.get("candidate_probe_ids") or []):
            errors.append(f"{pwhere}: candidate_tier requires candidate_probe_ids")
    # Compact reviewed digest freezes the exact profile inventory and every field value.
    actual = registry_digest(profiles_doc)
    if actual != REVIEWED_HOST_PROFILES_DIGEST:
        errors.append(f"{where}: host-profiles digest {actual} != reviewed "
                      f"{REVIEWED_HOST_PROFILES_DIGEST}")


def _scan_banned(obj: Any, errors: list[str], where: str, path: str) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in BANNED_RESPONSE_KEYS:
                errors.append(f"{where}: banned field {key!r} at {path}")
            _scan_banned(value, errors, where, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _scan_banned(value, errors, where, f"{path}[{i}]")


def check_banned_fields(response: dict, errors: list[str], where: str) -> None:
    _scan_banned(response.get("result", {}), errors, where, "result")


def check_protocol_identity(response: dict, errors: list[str], where: str) -> None:
    if response.get("protocol") != "empirica/v2":
        errors.append(f"{where}: protocol must be exactly empirica/v2, got {response.get('protocol')!r}")


def check_allow_cross_field(result: dict, errors: list[str], where: str) -> None:
    """Allow.converged == (run.status == 'converged'); RunView has no converged field."""
    if result.get("type") != "Allow" or "run" not in result:
        return
    run = result.get("run")
    if not isinstance(run, dict):
        return
    if "converged" in run:
        errors.append(f"{where}: RunView must not duplicate converged (lives only on Allow)")
    want = run.get("status") == "converged"
    if result.get("converged") is not want:
        errors.append(f"{where}: Allow.converged={result.get('converged')} must equal "
                      f"run.status=='converged' (status={run.get('status')!r})")


def _validate_params(params: dict, schema: dict, where: str, errors: list[str]) -> None:
    if Draft202012Validator is not None:
        for error in Draft202012Validator(schema).iter_errors(params):
            errors.append(f"{where}: parameters invalid: {error.message}")


def check_response_reasons(result: dict, contract: dict, errors: list[str], where: str) -> None:
    """Known code, valid params (registry schema), EXACT ordered next_actions/sections, claim_id scope."""
    reasons_registry = contract.get("reasons", {})
    if result.get("type") != "Block":
        return
    for i, reason in enumerate(result.get("reasons", []) or []):
        rwhere = f"{where}.reasons[{i}]"
        if not isinstance(reason, dict):
            errors.append(f"{rwhere}: must be an object")
            continue
        code = reason.get("code")
        if code not in reasons_registry:
            errors.append(f"{rwhere}: unknown reason code {code!r}")
            continue
        reg = reasons_registry[code]
        params = reason.get("parameters", {})
        if not isinstance(params, dict):
            errors.append(f"{rwhere}: parameters must be an object")
            params = {}
        _validate_params(params, reg.get("params", {}), rwhere, errors)
        # Exact ordered equality for next_actions and sections.
        if reason.get("next_actions") != reg.get("next_actions"):
            errors.append(f"{rwhere}: next_actions {reason.get('next_actions')!r} != registry "
                          f"{reg.get('next_actions')!r} for {code!r}")
        if reason.get("sections") != reg.get("sections"):
            errors.append(f"{rwhere}: sections {reason.get('sections')!r} != registry "
                          f"{reg.get('sections')!r} for {code!r}")
        if code == "audit.stale" and params.get("scope") != "claim" and "claim_id" in params:
            errors.append(f"{rwhere}: audit.stale claim_id permitted only for scope=claim")


def check_response_run_view(result: dict, contract: dict, host_tiers_by_profile: dict,
                            digest: str, errors: list[str], where: str) -> None:
    run = result.get("run")
    if not isinstance(run, dict):
        return
    section_ids = set(contract.get("sections", {}))
    child_states = set(contract.get("child_lifecycle", {}).get("states", []))
    reason_ids = set(contract.get("reasons", {}))
    next_action_ids = set(contract.get("next_actions", {}))
    contract_ref = run.get("contract", {})
    if isinstance(contract_ref, dict):
        dg = contract_ref.get("digest")
        if dg != digest:
            errors.append(f"{where}: run.contract.digest {dg!r} != registry digest {digest!r}")
        for sid in contract_ref.get("relevant_sections", []) or []:
            if sid not in section_ids:
                errors.append(f"{where}: run.contract.relevant_sections references unknown section {sid!r}")
    else:
        errors.append(f"{where}: run.contract is required")
    for child in run.get("children", []) or []:
        if isinstance(child, dict):
            if child.get("state") not in child_states:
                errors.append(f"{where}: run.children state {child.get('state')!r} is unknown")
            ra = child.get("recovery_action")
            if ra is not None and ra not in next_action_ids:
                errors.append(f"{where}: run.children recovery_action {ra!r} is not a known next action")
    host = run.get("host", {})
    if isinstance(host, dict) and "tier" in host:
        pid = host.get("profile_id")
        if pid in host_tiers_by_profile and host.get("tier") != host_tiers_by_profile[pid]:
            errors.append(f"{where}: run.host.tier {host.get('tier')!r} != profile {pid!r} tier "
                          f"{host_tiers_by_profile[pid]!r}")
        elif pid not in host_tiers_by_profile:
            errors.append(f"{where}: run.host.profile_id {pid!r} not in host profile registry")
        for cap in host.get("missing_capabilities", []) or []:
            if cap not in reason_ids:
                errors.append(f"{where}: run.host.missing_capabilities references unknown reason {cap!r}")
    for residual in run.get("residuals", []) or []:
        if isinstance(residual, dict):
            code = residual.get("code")
            if code not in reason_ids:
                errors.append(f"{where}: run.residuals code {code!r} is unknown")
            else:
                _validate_params(residual.get("parameters", {}), contract["reasons"][code].get("params", {}),
                                 f"{where}: run.residuals[{code}]", errors)


def _check_freshness_changes(changes: list, freshness_states: set, where: str,
                            errors: list[str]) -> None:
    """Closed items, canonical states, lexical path ordering, unique paths, no hashes."""
    if not isinstance(changes, list):
        errors.append(f"{where}: changes must be an array")
        return
    seen_paths: list[str] = []
    for i, item in enumerate(changes):
        if not isinstance(item, dict):
            errors.append(f"{where}.changes[{i}]: must be an object")
            continue
        if set(item.keys()) != {"path", "state"}:
            errors.append(f"{where}.changes[{i}]: must have only path and state, got {sorted(item.keys())}")
        path = item.get("path")
        state = item.get("state")
        if not isinstance(path, str) or not path:
            errors.append(f"{where}.changes[{i}]: path must be a nonempty string")
        elif not RELATIVE_POSIX_RE.match(path):
            errors.append(f"{where}.changes[{i}]: path {path!r} must be normalized relative posix")
        if state not in freshness_states:
            errors.append(f"{where}.changes[{i}]: state {state!r} is not a canonical freshness state")
        if isinstance(path, str) and path in seen_paths:
            errors.append(f"{where}.changes[{i}]: duplicate path {path!r}")
        seen_paths.append(path) if isinstance(path, str) else None
    if seen_paths != sorted(seen_paths):
        errors.append(f"{where}: changes must be canonically path-ordered {seen_paths!r}")


def check_run_view_fields(result: dict, contract: dict, errors: list[str], where: str) -> None:
    """D2A §3: complete required RunView fields + freshness/obligations/child-recovery semantics."""
    run = result.get("run")
    if not isinstance(run, dict):
        return
    required = ["id", "goal", "status", "modes", "contract", "obligations",
                "residuals", "freshness", "children", "host"]
    for field in required:
        if field not in run:
            errors.append(f"{where}: run.{field} is required (complete RunView)")
    freshness_states = set(contract.get("freshness_states", []))
    freshness = run.get("freshness")
    if isinstance(freshness, dict):
        if set(freshness.keys()) != {"changes"}:
            errors.append(f"{where}: run.freshness must have only changes, got {sorted(freshness.keys())}")
        _check_freshness_changes(freshness.get("changes", []), freshness_states,
                                 f"{where}: run.freshness", errors)
    elif "freshness" in run:
        errors.append(f"{where}: run.freshness must be an object")
    modes = run.get("modes")
    mode_fields = set(contract.get("mode_fields", []))
    if isinstance(modes, dict):
        if set(modes.keys()) != mode_fields:
            errors.append(f"{where}: run.modes keys {sorted(modes.keys())} != canonical "
                          f"{sorted(mode_fields)}")
        for mf in mode_fields:
            if not isinstance(modes.get(mf), bool):
                errors.append(f"{where}: run.modes.{mf} must be a boolean")
    elif "modes" in run:
        errors.append(f"{where}: run.modes must be an object")
    obligations = run.get("obligations")
    if isinstance(obligations, dict):
        if set(obligations.keys()) != {"active", "deferred"}:
            errors.append(f"{where}: run.obligations must have only active and deferred")
        seen_ids: set[str] = set()
        for key in ("active", "deferred"):
            for item in obligations.get(key, []) or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    if item["id"] in seen_ids:
                        errors.append(f"{where}: run.obligations duplicate id {item['id']!r}")
                    seen_ids.add(item["id"])
    # Child adverse/completed recovery conditions (schema also enforces via if/then).
    adverse = set(contract.get("child_lifecycle", {}).get("terminal_states", [])) - {"completed"}
    for i, child in enumerate(run.get("children", []) or []):
        if not isinstance(child, dict):
            continue
        state = child.get("state")
        ra = child.get("recovery_action")
        if state in adverse and not (isinstance(ra, str) and ra):
            errors.append(f"{where}: run.children[{i}] adverse state {state!r} requires recovery_action")
        if state == "completed" and ra is not None:
            errors.append(f"{where}: run.children[{i}] completed forbids recovery_action")


def check_stale_freshness_match(result: dict, errors: list[str], where: str) -> None:
    """D2A §5: Block run freshness changes and claim.spike_stale reason changes match exactly."""
    if result.get("type") != "Block":
        return
    run = result.get("run")
    run_changes = run.get("freshness", {}).get("changes") if isinstance(run, dict) else None
    for i, reason in enumerate(result.get("reasons", []) or []):
        if reason.get("code") != "claim.spike_stale":
            continue
        reason_changes = reason.get("parameters", {}).get("changes")
        if run_changes != reason_changes:
            errors.append(f"{where}: run.freshness.changes {run_changes!r} != "
                          f"reasons[{i}].parameters.changes {reason_changes!r} for claim.spike_stale")


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(DIGEST_RE.match(value))


def _check_prereqs(art: dict, p: str, cdigest: Any,
                   supporting_research_by_digest: dict, errors: list[str]) -> None:
    """Invariant 6: prerequisite_research_ids is a nonempty, unique digest array and every
    ID names an earlier supporting research artifact for the same claim digest. The sealed
    request proves the application selected the active snapshot at request time; current
    ``active`` may now be false after later supersession, so activeness is NOT required here."""
    pr = art.get("prerequisite_research_ids", []) or []
    if not isinstance(pr, list) or not pr:
        errors.append(f"{p}: prerequisite_research_ids must be a nonempty array")
        return
    pr_seen: list[str] = []
    for pid in pr:
        if not _is_digest(pid):
            errors.append(f"{p}: prerequisite_research_id {pid!r} must be sha256:<64hex>")
        elif pid in pr_seen:
            errors.append(f"{p}: prerequisite_research_id {pid!r} is not unique")
        else:
            pr_seen.append(pid)
        if cdigest is not None and pid not in supporting_research_by_digest.get(cdigest, set()):
            errors.append(f"{p}: prerequisite_research_id {pid!r} is not an earlier supporting "
                          f"research artifact for claim digest {cdigest!r}")


def check_argument_view(result: dict, contract: dict, errors: list[str], where: str) -> None:
    """D2A §4: ArgumentView closed, referential integrity, audit cross-field, delimiters."""
    argument = result.get("argument")
    if not isinstance(argument, dict):
        return
    ud = contract.get("untrusted_delimiters", {})
    # Canonical delimiters exactly equal registry values.
    aud = argument.get("untrusted_delimiters")
    if not isinstance(aud, dict) or aud != ud:
        errors.append(f"{where}: argument.untrusted_delimiters {aud!r} != registry {ud!r}")
    # Digest formatting for non-nullable fields.
    for field in ("argument_digest", "goal_digest", "deferred_scope_digest"):
        if not _is_digest(argument.get(field)):
            errors.append(f"{where}: argument.{field} must be sha256:<64hex>, got {argument.get(field)!r}")
    if argument.get("frozen_scope_digest") is not None and not _is_digest(argument.get("frozen_scope_digest")):
        errors.append(f"{where}: argument.frozen_scope_digest must be null or sha256:<64hex>")
    claims = argument.get("claims", []) or []
    claim_ids: set[str] = set()
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"{where}: argument.claims[{i}] must be an object")
            continue
        cid = claim.get("claim_id")
        if not isinstance(cid, str) or not cid:
            errors.append(f"{where}: argument.claims[{i}].claim_id required")
        elif cid in claim_ids:
            errors.append(f"{where}: argument.claims[{i}] duplicate claim_id {cid!r}")
        else:
            claim_ids.add(cid)
        if claim.get("state") not in set(contract.get("claim_states", [])):
            errors.append(f"{where}: argument.claims[{i}].state {claim.get('state')!r} not canonical")
        claim_kinds = set(contract.get("claim_kinds", []))
        if claim.get("kind") not in claim_kinds:
            errors.append(f"{where}: argument.claims[{i}].kind {claim.get('kind')!r} not canonical")
        if not _is_digest(claim.get("wording_digest")) or not _is_digest(claim.get("evidence_digest")):
            errors.append(f"{where}: argument.claims[{i}] wording/evidence digest malformed")
        ae = claim.get("active_evidence_ids", [])
        if not isinstance(ae, list) or not all(isinstance(x, str) and x for x in ae):
            errors.append(f"{where}: argument.claims[{i}].active_evidence_ids must be nonempty strings")
    # Root claim exists.
    root = argument.get("root_claim_id")
    if root not in claim_ids:
        errors.append(f"{where}: argument.root_claim_id {root!r} not in claims")
    # D2C: one canonical typed artifacts union replaces the reduced evidence summary.
    # Per-kind vocabularies are derived from the registry (artifact_kinds/artifact_outcomes/
    # spike_gates); research outcomes = artifact_outcomes \ spike_gates, spike outcomes =
    # spike_gates. The raw schema enforces closure/discriminator/required/forbidden/enum/
    # digest/path format; this loop enforces the ordering and correlation invariants 1-8.
    # Invariants 9-13 (complete active-set, evidence-digest, active prerequisites,
    # supersession, approved composition) are enforced after the loop. Invariant 14
    # (passed/failed audit current gating coverage) is enforced in the audit block below,
    # unchanged from D2A. Array order equals strictly-increasing sequence order.
    artifact_kinds = set(contract.get("artifact_kinds", []))
    artifact_outcomes = set(contract.get("artifact_outcomes", []))
    spike_gates = set(contract.get("spike_gates", []))
    research_outcomes = artifact_outcomes - spike_gates
    spike_outcomes = spike_gates

    def _bad_path(p: Any) -> bool:
        return not isinstance(p, str) or not p or not RELATIVE_POSIX_RE.match(p)

    artifacts = argument.get("artifacts", []) or []
    artifact_ids: set[str] = set()
    artifact_by_id: dict[str, dict] = {}
    artifact_kind_by_id: dict[str, str] = {}
    prev_seq: int | None = None
    seq_seen: set[int] = set()
    supporting_research_by_digest: dict[str, set[str]] = {}
    active_supporting_by_cd: dict[tuple[str, str], set[str]] = {}
    requests_by_key: dict[tuple[str, str], dict] = {}
    results_per_request: dict[tuple[str, str], int] = {}
    spike_results_by_claim: dict[str, set[str]] = {}
    superseded_ids: set[str] = set()
    active_spikes_by_cd: dict[tuple[str, str], list[str]] = {}
    active_spike_bindings_by_cd: dict[tuple[str, str], set[str]] = { }
    for i, art in enumerate(artifacts):
        p = f"{where}: argument.artifacts[{i}]"
        if not isinstance(art, dict):
            errors.append(f"{p}: must be an object")
            continue
        # invariant 1: sequence integer >= 0, unique, strictly increasing in array order.
        seq = art.get("sequence")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            errors.append(f"{p}: sequence must be an integer >= 0, got {seq!r}")
            seq = None
        else:
            if seq in seq_seen:
                errors.append(f"{p}: sequence {seq} is not unique")
            seq_seen.add(seq)
            if prev_seq is not None and seq <= prev_seq:
                errors.append(f"{p}: sequence {seq} not strictly increasing after {prev_seq}")
            prev_seq = seq
        # invariant 2: globally unique artifact_id.
        aid = art.get("artifact_id")
        if not _is_digest(aid):
            errors.append(f"{p}: artifact_id must be sha256:<64hex>, got {aid!r}")
            aid = None
        elif aid in artifact_ids:
            errors.append(f"{p}: artifact_id {aid!r} is not unique")
            aid = None
        else:
            artifact_ids.add(aid)
        cid = art.get("claim_id")
        if cid not in claim_ids:
            errors.append(f"{p}: claim_id {cid!r} does not resolve")
        if not _is_digest(art.get("claim_digest")):
            errors.append(f"{p}: claim_digest must be sha256:<64hex>, got {art.get('claim_digest')!r}")
        cdigest = art.get("claim_digest") if _is_digest(art.get("claim_digest")) else None
        kind = art.get("kind")
        if kind not in artifact_kinds:
            errors.append(f"{p}: kind {kind!r} not a canonical artifact kind")
        if not _is_digest(art.get("statement_digest")):
            errors.append(f"{p}: statement_digest must be sha256:<64hex>")
        if kind == "research":
            for fb in ("harness_request_id", "command", "command_digest", "dependent_files",
                      "prerequisite_research_ids", "file_bindings", "exit_code",
                      "spike_gate", "supersedes"):
                if fb in art:
                    errors.append(f"{p}: research artifact must not carry spike field {fb!r}")
            if art.get("outcome") not in research_outcomes:
                errors.append(f"{p}: research outcome {art.get('outcome')!r} must be {sorted(research_outcomes)}")
            if not isinstance(art.get("active"), bool):
                errors.append(f"{p}: research active must be boolean")
            sk = art.get("source_kind")
            if sk not in EVIDENCE_SOURCE_KINDS:
                errors.append(f"{p}: research source_kind {sk!r} not canonical")
            if not isinstance(art.get("source_ref"), str) or not art.get("source_ref"):
                errors.append(f"{p}: research source_ref must be a nonempty string")
            if not isinstance(art.get("citation"), str) or not art.get("citation"):
                errors.append(f"{p}: research citation must be a nonempty string")
            if ("observed_content_digest" in art
                    and not _is_digest(art.get("observed_content_digest"))):
                errors.append(f"{p}: research observed_content_digest must be sha256:<64hex>")
            if aid is not None and art.get("outcome") == "supporting" and cdigest is not None:
                supporting_research_by_digest.setdefault(cdigest, set()).add(aid)
                if art.get("active") is True and isinstance(cid, str):
                    active_supporting_by_cd.setdefault((cid, cdigest), set()).add(aid)
        elif kind == "spike_request":
            for fb in ("active", "outcome", "file_bindings", "exit_code", "spike_gate",
                      "supersedes", "source_kind", "source_ref", "citation",
                      "observed_content_digest"):
                if fb in art:
                    errors.append(f"{p}: spike_request artifact must not carry field {fb!r}")
            if not isinstance(art.get("harness_request_id"), str) or not art.get("harness_request_id"):
                errors.append(f"{p}: spike_request harness_request_id must be nonempty")
            if not isinstance(art.get("command"), str) or not art.get("command"):
                errors.append(f"{p}: spike_request command must be nonempty")
            if not _is_digest(art.get("command_digest")):
                errors.append(f"{p}: spike_request command_digest must be sha256:<64hex>")
            df = art.get("dependent_files", []) or []
            if not isinstance(df, list) or not df:
                errors.append(f"{p}: dependent_files must be a nonempty array")
            else:
                df_seen: list[str] = []
                for fp in df:
                    if _bad_path(fp):
                        errors.append(f"{p}: dependent_files path {fp!r} must be normalized relative posix")
                    elif fp in df_seen:
                        errors.append(f"{p}: dependent_files path {fp!r} is not unique")
                    else:
                        df_seen.append(fp)
            _check_prereqs(art, p, cdigest, supporting_research_by_digest, errors)
            hrid = art.get("harness_request_id") if isinstance(art.get("harness_request_id"), str) else None
            if aid is not None and hrid is not None:
                key = (cid if isinstance(cid, str) else "", hrid)
                if key in requests_by_key:
                    errors.append(f"{p}: spike_request {key!r} duplicates an earlier request")
                else:
                    requests_by_key[key] = art
        elif kind == "spike":
            for fb in ("source_kind", "source_ref", "citation", "observed_content_digest",
                       "dependent_files"):
                if fb in art:
                    errors.append(f"{p}: spike result artifact must not carry field {fb!r}")
            if not isinstance(art.get("active"), bool):
                errors.append(f"{p}: spike active must be boolean")
            if art.get("outcome") not in spike_outcomes:
                errors.append(f"{p}: spike outcome {art.get('outcome')!r} must be {sorted(spike_outcomes)}")
            if not isinstance(art.get("harness_request_id"), str) or not art.get("harness_request_id"):
                errors.append(f"{p}: spike harness_request_id must be nonempty")
            if not isinstance(art.get("command"), str) or not art.get("command"):
                errors.append(f"{p}: spike command must be nonempty")
            if not _is_digest(art.get("command_digest")):
                errors.append(f"{p}: spike command_digest must be sha256:<64hex>")
            _check_prereqs(art, p, cdigest, supporting_research_by_digest, errors)
            fb = art.get("file_bindings", []) or []
            fb_paths: list[str] = []
            if not isinstance(fb, list) or not fb:
                errors.append(f"{p}: file_bindings must be a nonempty array")
            else:
                fb_path_seen: list[str] = []
                for b in fb:
                    if not isinstance(b, dict) or set(b.keys()) != {"path", "sha256"}:
                        errors.append(f"{p}: file_binding must be closed {{path, sha256}}")
                        continue
                    bp = b.get("path")
                    if _bad_path(bp):
                        errors.append(f"{p}: file_binding path {bp!r} must be normalized relative posix")
                    elif bp in fb_path_seen:
                        errors.append(f"{p}: file_binding path {bp!r} is not unique")
                    else:
                        fb_path_seen.append(bp)
                    if not _is_digest(b.get("sha256")):
                        errors.append(f"{p}: file_binding sha256 must be sha256:<64hex>")
                    if isinstance(bp, str) and bp:
                        fb_paths.append(bp)
            ec = art.get("exit_code")
            if not isinstance(ec, int) or isinstance(ec, bool):
                errors.append(f"{p}: exit_code must be an integer, got {ec!r}")
            gate = art.get("spike_gate")
            if gate not in spike_gates:
                errors.append(f"{p}: spike_gate {gate!r} not canonical")
            sup = art.get("supersedes")
            if sup is not None and not _is_digest(sup):
                errors.append(f"{p}: supersedes must be null or sha256:<64hex>")
            # invariant 5: spike_gate == pass iff exit_code == 0; outcome == pass iff gate pass.
            if gate == "pass" and isinstance(ec, int) and not isinstance(ec, bool) and ec != 0:
                errors.append(f"{p}: spike_gate pass requires exit_code 0, got {ec!r}")
            if gate == "fail" and isinstance(ec, int) and not isinstance(ec, bool) and ec == 0:
                errors.append(f"{p}: spike_gate fail requires a non-zero exit_code, got {ec!r}")
            if art.get("outcome") == "pass" and gate != "pass":
                errors.append(f"{p}: outcome pass requires spike_gate pass, got {gate!r}")
            if art.get("outcome") == "fail" and gate != "fail":
                errors.append(f"{p}: outcome fail requires spike_gate fail, got {gate!r}")
            # invariant 3: exactly one earlier matching spike_request (claim/harness/command/
            # command_digest/ordered prerequisite_research_ids).
            hrid = art.get("harness_request_id") if isinstance(art.get("harness_request_id"), str) else None
            key = (cid if isinstance(cid, str) else "", hrid) if hrid else None
            req = requests_by_key.get(key) if key else None
            if req is None:
                errors.append(f"{p}: spike result references no earlier spike_request for "
                              f"claim/harness {key!r}")
            else:
                if cdigest is not None and cdigest != req.get("claim_digest"):
                    errors.append(f"{p}: spike result claim_digest {cdigest!r} != request {req.get('claim_digest')!r}")
                if art.get("command") != req.get("command"):
                    errors.append(f"{p}: spike result command {art.get('command')!r} != request {req.get('command')!r}")
                if art.get("command_digest") != req.get("command_digest"):
                    errors.append(f"{p}: spike result command_digest {art.get('command_digest')!r} != request")
                if art.get("prerequisite_research_ids") != req.get("prerequisite_research_ids"):
                    errors.append(f"{p}: spike result prerequisite_research_ids "
                                  f"{art.get('prerequisite_research_ids')!r} != request "
                                  f"{req.get('prerequisite_research_ids')!r}")
                # invariant 7: file_binding paths exactly equal request dependent_files, same order.
                if fb_paths and fb_paths != req.get("dependent_files"):
                    errors.append(f"{p}: file_binding paths {fb_paths!r} != request "
                                f"dependent_files {req.get('dependent_files')!r}")
            # invariant 4: at most one direct result per request.
            if key is not None:
                results_per_request[key] = results_per_request.get(key, 0) + 1
            # invariant 8: non-null supersedes names an earlier spike result for the same claim.
            if sup is not None:
                claim_key = cid if isinstance(cid, str) else ""
                if sup not in spike_results_by_claim.get(claim_key, set()):
                    errors.append(f"{p}: supersedes {sup!r} is not an earlier spike result "
                                  f"for claim {cid!r}")
                if _is_digest(sup):
                    superseded_ids.add(sup)
            if aid is not None and isinstance(cid, str):
                spike_results_by_claim.setdefault(cid, set()).add(aid)
            # D2C invariant 12 tracking: active spike per claim (id, digest) + binding paths.
            if art.get("active") is True and cdigest is not None and aid is not None and isinstance(cid, str):
                active_spikes_by_cd.setdefault((cid, cdigest), []).append(aid)
                for b in (art.get("file_bindings") or []):
                    if isinstance(b, dict) and isinstance(b.get("path"), str):
                        active_spike_bindings_by_cd.setdefault((cid, cdigest), set()).add(b["path"])
        if aid is not None and kind in artifact_kinds:
            artifact_by_id[aid] = art
            artifact_kind_by_id[aid] = kind
    # invariant 4: no request has more than one direct result.
    for key, count in results_per_request.items():
        if count > 1:
            errors.append(f"{where}: argument spike_request {key!r} has more than one direct result ({count})")
    # D2C invariants 9-13: complete active-set projection, evidence-digest correlation,
    # active-spike prerequisites, supersession activeness, and approved-claim composition.
    # Invariant 14 (passed/failed audit gating coverage) is enforced in the audit block
    # below, unchanged from D2A, and binds the complete active-set digest from invariant 10.
    # Build the complete active research/spike set per (claim_id, claim_digest) in sequence
    # order (the artifacts array is already sequence-ordered by invariant 1). Keying by
    # both claim_id and wording digest prevents distinct claims with identical wording
    # from sharing active evidence.
    active_set_by_cd: dict[tuple[str, str], list[str]] = {}
    for art in artifacts:
        if not isinstance(art, dict):
            continue
        if art.get("kind") not in ("research", "spike"):
            continue
        if art.get("active") is not True:
            continue
        a_cid = art.get("claim_id") if isinstance(art.get("claim_id"), str) else None
        a_cd = art.get("claim_digest") if _is_digest(art.get("claim_digest")) else None
        aid = art.get("artifact_id") if _is_digest(art.get("artifact_id")) else None
        if a_cid is not None and a_cd is not None and aid is not None:
            active_set_by_cd.setdefault((a_cid, a_cd), []).append(aid)

    # RunView freshness changes (for invariant 13: approved needs-experiment claims have no
    # stale spike bindings).
    freshness_paths: set[str] = set()
    run = result.get("run")
    if isinstance(run, dict):
        fresh = run.get("freshness", {})
        if isinstance(fresh, dict):
            for ch in fresh.get("changes", []) or []:
                if isinstance(ch, dict) and isinstance(ch.get("path"), str):
                    freshness_paths.add(ch["path"])

    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        wd = claim.get("wording_digest")
        if not _is_digest(wd):
            continue
        cid = claim.get("claim_id") if isinstance(claim.get("claim_id"), str) else None
        if cid is None:
            continue
        ck = claim.get("kind")
        ae = claim.get("active_evidence_ids", []) or []
        # invariant 9: active_evidence_ids resolve to active research/spike artifacts, never
        # spike_request artifacts, AND match both the claim's ID and wording digest.
        for aid in ae:
            if not isinstance(aid, str) or not aid:
                errors.append(f"{where}: argument.claims[{i}].active_evidence_id must be nonempty")
            elif aid not in artifact_by_id:
                errors.append(f"{where}: argument.claims[{i}].active_evidence_id {aid!r} does not resolve")
            else:
                a = artifact_by_id[aid]
                if a.get("kind") == "spike_request":
                    errors.append(f"{where}: argument.claims[{i}].active_evidence_id {aid!r} "
                                  f"must not reference a spike_request artifact")
                if a.get("active") is not True:
                    errors.append(f"{where}: argument.claims[{i}].active_evidence_id {aid!r} "
                                  f"must reference an active artifact")
                # invariant 9: the artifact must belong to this exact claim (id + digest).
                a_cid = a.get("claim_id")
                a_cd = a.get("claim_digest")
                if (isinstance(a_cid, str) and a_cid != cid
                        or _is_digest(a_cd) and a_cd != wd):
                    errors.append(f"{where}: argument.claims[{i}].active_evidence_id {aid!r} "
                                  f"belongs to claim_id={a_cid!r} digest={a_cd!r}, "
                                  f"not claim_id={cid!r} digest={wd!r}")
        # invariant 9 (complete set): active_evidence_ids equals the complete active
        # research/spike set in sequence order for this claim's (id, digest).
        expected_active = active_set_by_cd.get((cid, wd), [])
        if ae != expected_active:
            errors.append(f"{where}: argument.claims[{i}].active_evidence_ids {ae!r} != "
                          f"complete active set {expected_active!r} for claim_id={cid!r} "
                          f"wording_digest={wd!r}")
        # invariant 10: evidence_digest = canonical registry_digest(active_evidence_ids).
        if isinstance(ae, list) and all(isinstance(x, str) for x in ae):
            computed_ed = registry_digest(ae)
            if claim.get("evidence_digest") != computed_ed:
                errors.append(f"{where}: argument.claims[{i}].evidence_digest "
                              f"{claim.get('evidence_digest')!r} != registry_digest("
                              f"active_evidence_ids) {computed_ed!r}")
        # invariant 11: an active spike's prerequisites are currently active/supporting for
        # the same claim (id, digest); historical inactive spike/request rows may reference
        # now-inactive prerequisites.
        for aid in ae:
            a = artifact_by_id.get(aid)
            if a is None or a.get("kind") != "spike" or a.get("active") is not True:
                continue
            a_cid = a.get("claim_id") if isinstance(a.get("claim_id"), str) else None
            a_cd = a.get("claim_digest") if _is_digest(a.get("claim_digest")) else None
            for pid in (a.get("prerequisite_research_ids") or []):
                if a_cid is not None and a_cd is not None:
                    if pid not in active_supporting_by_cd.get((a_cid, a_cd), set()):
                        errors.append(f"{where}: argument.claims[{i}] active spike {aid!r} "
                                      f"prerequisite {pid!r} is not a currently active "
                                      f"supporting research artifact for claim "
                                      f"id={a_cid!r} digest={a_cd!r}")
                elif a_cd is not None:
                    if pid not in active_supporting_by_cd.get((a_cid or "", a_cd), set()):
                        errors.append(f"{where}: argument.claims[{i}] active spike {aid!r} "
                                      f"prerequisite {pid!r} is not a currently active "
                                      f"supporting research artifact for claim digest {a_cd!r}")
        # invariant 13: approval follows canonical claim kind (D1 §5).
        #   ordinary: active supporting research, no active refuting/failing evidence.
        #   needs-experiment: additionally requires an active passing spike with satisfied
        #     prerequisites and no RunView freshness change for its bindings.
        #   needs-decision: can never project approved.
        if claim.get("state") == "approved":
            active_arts = [artifact_by_id[a] for a in ae if a in artifact_by_id]
            has_supporting = any(a.get("kind") == "research"
                                 and a.get("outcome") == "supporting"
                                 for a in active_arts)
            has_passing_spike = any(a.get("kind") == "spike"
                                   and a.get("outcome") == "pass"
                                   for a in active_arts)
            has_refuting = any(a.get("kind") == "research"
                              and a.get("outcome") == "refuting"
                              for a in active_arts)
            has_failing_spike = any(a.get("kind") == "spike"
                                  and a.get("outcome") == "fail"
                                  for a in active_arts)
            if ck == "needs-decision":
                errors.append(f"{where}: argument.claims[{i}] needs-decision claim "
                              f"cannot project approved")
            if not has_supporting:
                errors.append(f"{where}: argument.claims[{i}] approved but no active "
                              f"supporting research in active_evidence_ids")
            if has_refuting:
                errors.append(f"{where}: argument.claims[{i}] approved but active "
                              f"refuting research present in active_evidence_ids")
            if has_failing_spike:
                errors.append(f"{where}: argument.claims[{i}] approved but active "
                              f"failing spike present in active_evidence_ids")
            if ck == "needs-experiment":
                if not has_passing_spike:
                    errors.append(f"{where}: argument.claims[{i}] needs-experiment "
                                  f"approved but no active passing spike "
                                  f"in active_evidence_ids")
                stale = freshness_paths & active_spike_bindings_by_cd.get((cid, wd), set())
                if stale:
                    errors.append(f"{where}: argument.claims[{i}] needs-experiment "
                                  f"approved but RunView freshness changes cover "
                                  f"active spike bindings {sorted(stale)!r}")
            # ordinary approved does not require a passing spike.

    # invariant 12: a spike named by a later result's supersedes is inactive, and at most
    # one spike result is active per current claim (id, digest).
    for sid in superseded_ids:
        a = artifact_by_id.get(sid)
        if a is not None and a.get("active") is True:
            errors.append(f"{where}: argument artifact {sid!r} is superseded but still active")
    for key, spikes in active_spikes_by_cd.items():
        if len(spikes) > 1:
            errors.append(f"{where}: argument has more than one active spike result for "
                          f"claim {key!r}: {spikes!r}")
    # Edges: endpoints resolve; type canonical; deterministic ordering.
    edge_keys: set[tuple] = set()
    for i, edge in enumerate(argument.get("edges", []) or []):
        if not isinstance(edge, dict):
            errors.append(f"{where}: argument.edges[{i}] must be an object")
            continue
        if set(edge.keys()) != {"from", "to", "type"}:
            errors.append(f"{where}: argument.edges[{i}] must have only from/to/type")
        if edge.get("type") not in EDGE_TYPES:
            errors.append(f"{where}: argument.edges[{i}].type {edge.get('type')!r} not canonical")
        frm, to = edge.get("from"), edge.get("to")
        for ep in (frm, to):
            if ep not in claim_ids:
                errors.append(f"{where}: argument.edges[{i}] endpoint {ep!r} does not resolve")
        key = (frm, to, edge.get("type"))
        if key in edge_keys:
            errors.append(f"{where}: argument.edges[{i}] duplicate edge {key!r}")
        edge_keys.add(key)
    if len(argument.get("edges", [])) > 1:
        keys = [(e.get("from"), e.get("to"), e.get("type")) for e in argument.get("edges", [])]
        if keys != sorted(keys):
            errors.append(f"{where}: argument.edges must be deterministically ordered {keys!r}")
    # Audit cross-field conditions (D2A §4).
    audit = argument.get("audit")
    if isinstance(audit, dict):
        if audit.get("state") not in set(contract.get("audit_states", [])):
            errors.append(f"{where}: argument.audit.state {audit.get('state')!r} not canonical")
        if audit.get("independence") not in set(contract.get("independence_states", [])):
            errors.append(f"{where}: argument.audit.independence {audit.get('independence')!r} not canonical")
        covered = audit.get("state") in {"passed", "failed", "stale"}
        for field in ("reviewed_argument_digest", "reviewed_goal_digest", "reviewed_deferred_scope_digest"):
            if covered and audit.get(field) is None:
                errors.append(f"{where}: argument.audit.{field} must be non-null when state={audit.get('state')!r}")
            if audit.get(field) is not None and not _is_digest(audit.get(field)):
                errors.append(f"{where}: argument.audit.{field} must be null or sha256:<64hex>")
        # frozen digest equals null only when current frozen scope digest is null.
        if audit.get("reviewed_frozen_scope_digest") is not None:
            if argument.get("frozen_scope_digest") is None:
                errors.append(f"{where}: argument.audit.reviewed_frozen_scope_digest must be null "
                              f"when current frozen_scope_digest is null")
            if not _is_digest(audit.get("reviewed_frozen_scope_digest")):
                errors.append(f"{where}: argument.audit.reviewed_frozen_scope_digest must be null or sha256:<64hex>")
        elif argument.get("frozen_scope_digest") is not None and covered:
            errors.append(f"{where}: argument.audit.reviewed_frozen_scope_digest must be non-null "
                          f"when frozen_scope_digest is set and state={audit.get('state')!r}")
        # Reviewed claims resolve and are unique.
        reviewed_claims = audit.get("reviewed_claims", []) or []
        rc_keys: set[str] = set()
        for i, rc in enumerate(reviewed_claims):
            if not isinstance(rc, dict) or set(rc.keys()) != {"claim_id", "evidence_digest"}:
                errors.append(f"{where}: argument.audit.reviewed_claims[{i}] must have claim_id and evidence_digest")
                continue
            if rc.get("claim_id") not in claim_ids:
                errors.append(f"{where}: argument.audit.reviewed_claims[{i}].claim_id {rc.get('claim_id')!r} does not resolve")
            if not _is_digest(rc.get("evidence_digest")):
                errors.append(f"{where}: argument.audit.reviewed_claims[{i}].evidence_digest malformed")
            if rc.get("claim_id") in rc_keys:
                errors.append(f"{where}: argument.audit.reviewed_claims[{i}] duplicate claim_id {rc.get('claim_id')!r}")
            rc_keys.add(rc.get("claim_id"))
        # MAJOR-1: for passed/failed, reviewed_claims must equal exactly all current claims
        # where gating=true and state=approved, with each reviewed evidence_digest equal to
        # that claim's current evidence_digest. for not_required/required/pending they must be
        # empty (the raw schema enforces this); stale retains prior coverage.
        if audit.get("state") in {"passed", "failed"}:
            expected_coverage = {c.get("claim_id"): c.get("evidence_digest")
                                 for c in claims if isinstance(c, dict)
                                 and c.get("gating") is True and c.get("state") == "approved"}
            reviewed_map = {rc.get("claim_id"): rc.get("evidence_digest") for rc in reviewed_claims
                            if isinstance(rc, dict)}
            if set(reviewed_map) != set(expected_coverage):
                missing_cov = sorted(set(expected_coverage) - set(reviewed_map))
                extra_cov = sorted(set(reviewed_map) - set(expected_coverage))
                if missing_cov:
                    errors.append(f"{where}: argument.audit.reviewed_claims missing gating-claim "
                                  f"coverage {missing_cov} for state={audit.get('state')!r}")
                if extra_cov:
                    errors.append(f"{where}: argument.audit.reviewed_claims extra non-gating "
                                  f"coverage {extra_cov} for state={audit.get('state')!r}")
            for cid, exp_digest in expected_coverage.items():
                if cid in reviewed_map and reviewed_map[cid] != exp_digest:
                    errors.append(f"{where}: argument.audit.reviewed_claims[{cid}] "
                                  f"evidence_digest {reviewed_map[cid]!r} != current "
                                  f"{exp_digest!r} for state={audit.get('state')!r}")


def check_getargument_exclusivity(request: dict, result: dict, errors: list[str], where: str) -> None:
    """D2A §4: GetArgument carries argument; GetRun/RestoreRun do not."""
    cmd = request.get("command", {})
    if not isinstance(cmd, dict):
        return
    ctype = cmd.get("type")
    has_argument = "argument" in result
    if ctype == "GetArgument" and not has_argument:
        errors.append(f"{where}: GetArgument response must include argument")
    if ctype in ("GetRun", "RestoreRun") and has_argument:
        errors.append(f"{where}: {ctype} response must not include argument")


# --------------------------------------------------------------------------- #
# D6-A state-schema checks (D6 section 4/5).  The state schema is internal
# host-neutral persisted state, not PublicContract behavior.  Status/child-state
# enums are mechanically mirrored from the loaded PublicContract registry;
# procedural invariants (counter/stamp bounds, child_id uniqueness) are owned
# here because JSON Schema cannot express them.
# --------------------------------------------------------------------------- #


def check_state_schema_mirror(state_schema: dict, contract: dict, request_schema: dict,
                               response_schema: dict, errors: list[str],
                               where: str) -> None:
    """Mechanically compare state-schema enums/shapes to the loaded PublicContract registry.

    The internal state schema cannot own a drifting enum: status and child-state
    vocabularies must exactly equal the canonical PublicContract registry values,
    and the protocol const must equal the registry protocol. The child ``purpose``
    shape must match the D2 request/response schemas (nonempty opaque string).
    Child-branch if/then selectors must cover every PublicContract child state, and
    terminal-state branches must exactly cover the registry terminal_states.
    """
    props = state_schema.get("properties", {})
    status_enum = set(props.get("status", {}).get("enum", []))
    reg_statuses = set(contract.get("statuses", []))
    if status_enum != reg_statuses:
        errors.append(f"{where}: state.status enum {sorted(status_enum)} != registry "
                      f"statuses {sorted(reg_statuses)}")
    child_rec = state_schema.get("$defs", {}).get("childRecord", {})
    child_props = child_rec.get("properties", {})
    child_state_enum = set(child_props.get("state", {}).get("enum", []))
    reg_child_states = set(contract.get("child_lifecycle", {}).get("states", []))
    if child_state_enum != reg_child_states:
        errors.append(f"{where}: state childRecord.state enum {sorted(child_state_enum)} != "
                      f"registry child_lifecycle.states {sorted(reg_child_states)}")
    proto_const = props.get("protocol", {}).get("const")
    if proto_const != contract.get("protocol"):
        errors.append(f"{where}: state.protocol const {proto_const!r} != registry "
                      f"protocol {contract.get('protocol')!r}")
    # purpose shape parity: state childRecord.purpose must exact-match the D2
    # request actionChildReserve.purpose AND the response childSummary.purpose
    # (both are nonempty opaque strings). Extract the actual request/response purpose
    # definitions and exact-compare with the state purpose; no hardcoded shape.
    state_purpose = child_props.get("purpose", {})
    req_purpose = (request_schema.get("$defs", {})
                   .get("actionChildReserve", {}).get("properties", {})
                   .get("purpose", {}))
    res_purpose = (response_schema.get("$defs", {})
                   .get("childSummary", {}).get("properties", {})
                   .get("purpose", {}))
    if state_purpose != req_purpose:
        errors.append(f"{where}: state childRecord.purpose {state_purpose} != request "
                      f"actionChildReserve.purpose {req_purpose}")
    if state_purpose != res_purpose:
        errors.append(f"{where}: state childRecord.purpose {state_purpose} != response "
                      f"childSummary.purpose {res_purpose}")
    # child-branch coverage: every PublicContract child state must have a branch
    # selector in the schema's allOf if/then list (one if-block per state or group).
    all_of = child_rec.get("allOf", [])
    covered_states: set[str] = set()
    for branch in all_of:
        if_state = branch.get("if", {}).get("properties", {}).get("state", {})
        if "const" in if_state:
            covered_states.add(if_state["const"])
        elif "enum" in if_state:
            covered_states.update(if_state["enum"])
    if covered_states != reg_child_states:
        errors.append(f"{where}: state child branch selectors {sorted(covered_states)} != "
                      f"registry child_lifecycle.states {sorted(reg_child_states)}")
    # terminal-state branch coverage: the terminal fingerprint requirement must
    # cover exactly the registry terminal_states.
    reg_terminal = set(contract.get("child_lifecycle", {}).get("terminal_states", []))
    fingerprint_required: set[str] = set()
    for branch in all_of:
        then_props = branch.get("then", {}).get("properties", {})
        fp = then_props.get("first_terminal_fingerprint", {})
        if_state = branch.get("if", {}).get("properties", {}).get("state", {})
        if "$ref" in fp or (fp.get("type") == "string" and "pattern" in fp):
            if "const" in if_state:
                fingerprint_required.add(if_state["const"])
            elif "enum" in if_state:
                fingerprint_required.update(if_state["enum"])
    if fingerprint_required != reg_terminal:
        errors.append(f"{where}: state terminal-fingerprint branches {sorted(fingerprint_required)} "
                      f"!= registry terminal_states {sorted(reg_terminal)}")


def check_state_child_branch_matrix(state_schema: dict, contract: dict,
                                        errors: list[str], where: str) -> None:
    """Data-driven child behavior matrix (D6 section 4).

    For every canonical PublicContract child state, build one valid child record, assert
    it validates against the schema, then mutate spent/refunded/native_id/fingerprint
    in isolation and assert each mutation is rejected. Also assert each state is matched
    by exactly one state-selector branch, every such ``if`` requires ``state``, and each
    ``then`` requires all four relation fields (spent, refunded, native_id,
    first_terminal_fingerprint).

    The matrix is driven by the registry terminal_states set and the schema branch
    selectors -- no policy is added here; the schema already encodes the exact matrix.
    """
    import copy
    if Draft202012Validator is None:
        return
    child_rec = state_schema.get("$defs", {}).get("childRecord", {})
    all_of = child_rec.get("allOf", [])
    reg_states = set(contract.get("child_lifecycle", {}).get("states", []))
    reg_terminal = set(contract.get("child_lifecycle", {}).get("terminal_states", []))
    validator = Draft202012Validator(state_schema)
    # Map each state to the state-selector branch(es) that select it.
    state_branches: dict[str, list[int]] = {}
    for idx, branch in enumerate(all_of):
        if_state = branch.get("if", {}).get("properties", {}).get("state", {})
        if "const" in if_state:
            state_branches.setdefault(if_state["const"], []).append(idx)
        elif "enum" in if_state:
            for s in if_state["enum"]:
                state_branches.setdefault(s, []).append(idx)
    for state in sorted(reg_states):
        if state not in state_branches:
            errors.append(f"{where}: state {state!r} has no state-selector branch")
            continue
        if len(state_branches[state]) != 1:
            errors.append(f"{where}: state {state!r} matched by {len(state_branches[state])} "
                          f"state-selector branches {state_branches[state]}; expected exactly one")
    # Assert every state-selector if requires state, and each then requires all four.
    relation_fields = {"spent", "refunded", "native_id", "first_terminal_fingerprint"}
    for idx, branch in enumerate(all_of):
        if_state = branch.get("if", {}).get("properties", {})
        if "state" not in if_state:
            continue  # non-state-selector branch (e.g. refunded branch)
        if_req = branch.get("if", {}).get("required", [])
        if "state" not in if_req:
            errors.append(f"{where}: branch {idx} if must require state")
            continue
        then_req = set(branch.get("then", {}).get("required", []))
        missing = relation_fields - then_req
        if missing:
            errors.append(f"{where}: branch {idx} then must require all four relation "
                          f"fields; missing {sorted(missing)}")
    # Build one valid child per state and verify it validates; then mutate each relation.
    d64 = "sha256:" + "f" * 64
    for state in sorted(reg_states):
        child = _valid_child_for_state(state, d64, reg_terminal)
        wrapper = _minimal_valid_state()
        wrapper["children"] = [child]
        schema_errors = list(validator.iter_errors(wrapper))
        if schema_errors:
            msgs = [e.message for e in schema_errors]
            errors.append(f"{where}: valid child for state {state!r} was rejected: {msgs}")
            continue
        for field, bad_value in _child_relation_mutations(state, d64, reg_terminal):
            bad_child = copy.deepcopy(child)
            bad_child[field] = bad_value
            bad_wrapper = _minimal_valid_state()
            bad_wrapper["children"] = [bad_child]
            schema_errors = list(validator.iter_errors(bad_wrapper))
            if not schema_errors:
                errors.append(f"{where}: state {state!r} mutation of {field!r} to "
                              f"{bad_value!r} was NOT rejected")


def _minimal_valid_state() -> dict:
    """A minimal valid state wrapper for child-matrix validation."""
    return {
        "protocol": "empirica/v2",
        "state_schema": "empirica.run/2",
        "goal": "g",
        "status": "active",
        "modes": {"multi_provider": False, "cli_exec": False},
        "budgets": {"max_passes": 1, "passes_used": 0,
                    "max_spawns": 1, "spawns_used": 0,
                    "max_audit_spawns": 1, "audit_spawns_used": 0},
        "selected_graph_artifact_id": None,
        "committed_artifact_head_id": None,
        "frozen_claim_ids": None,
        "frozen_semantic_digest": None,
        "route_stamp": None,
        "investigation_stamp": None,
        "stamp_seq": 0,
        "last_derivation_digest": None,
        "children": [],
    }


def _valid_child_for_state(state: str, d64: str, reg_terminal: set) -> dict:
    """Build one valid child record for the given canonical child state."""
    base = {"child_id": f"c-{state}", "purpose": "audit", "resource_class": "audit",
            "state": state, "deadline": None, "capability_ref": "cap-1",
            "audit_operation_id": d64, "audit_argument": {"argument_digest": d64},
            "audit_role_profile": "empirica:empirica-auditor"}
    if state == "reserved":
        base.update(spent=False, refunded=False, native_id=None,
                     first_terminal_fingerprint=None)
    elif state == "launch_rejected":
        base.update(spent=False, refunded=True, native_id=None,
                     first_terminal_fingerprint=d64)
    elif state in ("launching", "pending"):
        base.update(spent=True, refunded=False, native_id="n1",
                     first_terminal_fingerprint=None)
    else:  # terminal states: completed/failed/cancelled/timed_out/orphaned
        base.update(spent=True, refunded=False, native_id="n1",
                     first_terminal_fingerprint=d64)
    return base


def _child_relation_mutations(state: str, d64: str, reg_terminal: set):
    """Yield (field, bad_value) mutations that must be rejected for each state."""
    if state == "reserved":
        yield ("spent", True)
        yield ("refunded", True)
        yield ("native_id", "n1")
        yield ("first_terminal_fingerprint", d64)
    elif state == "launch_rejected":
        yield ("spent", True)
        yield ("refunded", False)
        yield ("native_id", "n1")
        yield ("first_terminal_fingerprint", None)
    elif state in ("launching", "pending"):
        yield ("spent", False)
        yield ("refunded", True)
        yield ("native_id", None)
        yield ("first_terminal_fingerprint", d64)
    else:  # terminal states
        yield ("spent", False)
        yield ("refunded", True)
        yield ("native_id", None)
        yield ("first_terminal_fingerprint", None)


def check_state_invariants(state: dict, errors: list[str], where: str) -> None:
    """Procedural invariants not expressible in JSON Schema (D6 section 4).

    - counters: each used counter is bounded and equals non-refunded children of its class;
    - stamps: route_stamp <= stamp_seq and investigation_stamp <= stamp_seq when non-null;
    - child_id uniqueness across the ordered children array;
    - child deadline: finite number or null (reject NaN/+Inf/-Inf).
    """
    import math
    if not isinstance(state, dict):
        return
    budgets = state.get("budgets", {})
    if isinstance(budgets, dict):
        max_passes = budgets.get("max_passes", 0)
        passes_used = budgets.get("passes_used", 0)
        max_spawns = budgets.get("max_spawns", 0)
        spawns_used = budgets.get("spawns_used", 0)
        max_audit_spawns = budgets.get("max_audit_spawns", 0)
        audit_spawns_used = budgets.get("audit_spawns_used", 0)
        if isinstance(passes_used, int) and isinstance(max_passes, int) \
                and passes_used > max_passes:
            errors.append(f"{where}: passes_used {passes_used} > max_passes {max_passes}")
        if isinstance(spawns_used, int) and isinstance(max_spawns, int) \
                and spawns_used > max_spawns:
            errors.append(f"{where}: spawns_used {spawns_used} > max_spawns {max_spawns}")
        if isinstance(audit_spawns_used, int) and isinstance(max_audit_spawns, int) \
                and audit_spawns_used > max_audit_spawns:
            errors.append(f"{where}: audit_spawns_used {audit_spawns_used} > "
                          f"max_audit_spawns {max_audit_spawns}")
        charged = {"investigation": 0, "audit": 0}
        for child in state.get("children", []) or []:
            if isinstance(child, dict) and child.get("refunded") is False:
                charged[child.get("resource_class")] = charged.get(child.get("resource_class"), 0) + 1
        if spawns_used != charged["investigation"] or audit_spawns_used != charged["audit"]:
            errors.append(f"{where}: spawn counters do not reconcile with non-refunded children")
    stamp_seq = state.get("stamp_seq", 0)
    if isinstance(stamp_seq, int):
        route_stamp = state.get("route_stamp")
        if isinstance(route_stamp, int) and route_stamp > stamp_seq:
            errors.append(f"{where}: route_stamp {route_stamp} > stamp_seq {stamp_seq}")
        investigation_stamp = state.get("investigation_stamp")
        if isinstance(investigation_stamp, int) and investigation_stamp > stamp_seq:
            errors.append(f"{where}: investigation_stamp {investigation_stamp} > "
                          f"stamp_seq {stamp_seq}")
    seen: set[str] = set()
    for i, child in enumerate(state.get("children", []) or []):
        if not isinstance(child, dict):
            continue
        cid = child.get("child_id")
        if isinstance(cid, str):
            if cid in seen:
                errors.append(f"{where}: children[{i}] duplicate child_id {cid!r}")
            seen.add(cid)
        deadline = child.get("deadline")
        if deadline is not None:
            if not (isinstance(deadline, (int, float)) and not isinstance(deadline, bool)
                    and math.isfinite(deadline)):
                errors.append(f"{where}: children[{i}] deadline {deadline!r} must be a "
                              f"finite number or null")


def check_trusted_action(action: dict, author_actions: set, trusted_actions: set,
                         errors: list[str], where: str, redacted: bool) -> None:
    if not isinstance(action, dict):
        return
    kind = action.get("kind")
    if kind in trusted_actions:
        trusted = action.get("trusted")
        if not isinstance(trusted, dict) or not trusted.get("capability_ref") or not trusted.get("boundary"):
            errors.append(f"{where}: trusted action {kind!r} must carry a trusted envelope")
        elif redacted and not REDACTED_RE.match(str(trusted.get("capability_ref", ""))):
            errors.append(f"{where}: trusted capability_ref must be a redacted placeholder in fixtures")
    elif kind in author_actions:
        if "trusted" in action:
            errors.append(f"{where}: author action {kind!r} must not carry a trusted envelope")


def check_trusted_payload(request: dict, result: dict, registry: dict,
                         errors: list[str], where: str) -> None:
    """D2D: correlate a trusted action request payload with the expected response view.

    Uses the correlated request/response fixture bundle for procedural facts only; it
    implements no runtime state or second policy model. Raw schema closure/branch/
    enum/digest/path invariants are enforced by the request schema; this enforces the
    cross-bundle correlation facts listed in the D2D contract.
    """
    command = request.get("command", {})
    if not isinstance(command, dict) or command.get("type") != "ObserveAction":
        return
    action = command.get("action", {})
    if not isinstance(action, dict):
        return
    kind = action.get("kind")
    payload = action.get("payload", {})
    if not isinstance(payload, dict):
        return
    run = result.get("run") if isinstance(result, dict) else None
    argument = result.get("argument") if isinstance(result, dict) else None
    pw = f"{where}:request.action.payload"

    if kind == "child_event":
        child_id = action.get("child_id")
        children = run.get("children", []) if isinstance(run, dict) else []
        matched = next((c for c in children if isinstance(c, dict)
                       and c.get("child_id") == child_id), None)
        if matched is None:
            errors.append(f"{pw}: child_event child_id {child_id!r} does not match a run child")
        elif matched.get("state") != payload.get("state"):
            errors.append(f"{pw}: child_event state {payload.get('state')!r} != "
                          f"run child state {matched.get('state')!r}")
    elif kind == "attribution":
        if payload.get("subject_kind") == "covered_actor":
            if not isinstance(argument, dict):
                return
            covered = payload.get("covered_artifact_ids", []) or []
            artifacts = argument.get("artifacts", []) or []
            claims = argument.get("claims", []) or []
            by_id = {a.get("artifact_id"): a for a in artifacts if isinstance(a, dict)}
            gating_ids = {c.get("claim_id") for c in claims if isinstance(c, dict)
                         and c.get("gating") is True and c.get("state") == "approved"}
            for aid in covered:
                art = by_id.get(aid)
                if art is None:
                    errors.append(f"{pw}: covered_artifact_id {aid!r} does not resolve "
                                  f"to an argument artifact")
                elif art.get("active") is not True:
                    errors.append(f"{pw}: covered_artifact_id {aid!r} is not an active artifact")
                elif art.get("claim_id") not in gating_ids:
                    errors.append(f"{pw}: covered_artifact_id {aid!r} does not belong to "
                                  f"an approved gating claim")
        elif payload.get("subject_kind") == "auditor":
            # D2D-R correction 4: an auditor attribution child resolves to exactly one
            # audit-purpose `pending` child (the verdict later binds its completion).
            child_id = payload.get("child_id")
            children = run.get("children", []) if isinstance(run, dict) else []
            audit_children = [c for c in children if isinstance(c, dict)
                             and c.get("child_id") == child_id
                             and c.get("purpose") == "audit"]
            if len(audit_children) != 1:
                errors.append(f"{pw}: auditor child_id {child_id!r} does not match an "
                              f"admitted audit child")
            elif audit_children[0].get("state") != "pending":
                errors.append(f"{pw}: auditor child_id {child_id!r} matches an audit child "
                              f"but state {audit_children[0].get('state')!r} is not pending")
    elif kind == "audit_verdict":
        # D2D-R correction 3: the verdict's child_id is the trusted host-observed
        # completion bound from a previously pending audit child; it atomically admits
        # verdict + terminal child result. It must resolve in the expected RunView to
        # exactly one audit-purpose child in `completed` state. D4 establishes the
        # pending precondition and must not send a second child completion afterward.
        child_id = action.get("child_id")
        children = run.get("children", []) if isinstance(run, dict) else []
        same_id = [c for c in children if isinstance(c, dict)
                  and c.get("child_id") == child_id]
        audit_children = [c for c in same_id if c.get("purpose") == "audit"]
        if not same_id:
            errors.append(f"{pw}: audit_verdict child_id {child_id!r} does not match a run child")
        elif not audit_children:
            errors.append(f"{pw}: audit_verdict child_id {child_id!r} matches a run child "
                          f"but it is not audit-purpose")
        elif len(audit_children) != 1:
            errors.append(f"{pw}: audit_verdict child_id {child_id!r} matches multiple "
                          f"audit-purpose children")
        elif audit_children[0].get("state") != "completed":
            errors.append(f"{pw}: audit_verdict child_id {child_id!r} matches an audit child "
                          f"but state {audit_children[0].get('state')!r} is not completed")
        if not isinstance(argument, dict) or not argument:
            return
        for field in ("argument_digest", "goal_digest", "frozen_scope_digest",
                      "deferred_scope_digest"):
            if payload.get(field) != argument.get(field):
                errors.append(f"{pw}: audit_verdict {field} {payload.get(field)!r} != "
                              f"current {argument.get(field)!r}")
        # D2D-R correction 1: reviewed_claims stays an ordered array in canonical
        # ArgumentView claim order. Reject duplicate claim_id BEFORE any map
        # construction (no lossy dict), then compare the ordered (claim_id,
        # evidence_digest) list exactly to the current approved gating claims;
        # deferred claims are never included.
        reviewed_items = payload.get("reviewed_claims", []) or []
        rc_seen: list[str] = []
        for i, rc in enumerate(reviewed_items):
            if not isinstance(rc, dict):
                errors.append(f"{pw}: audit_verdict reviewed_claims[{i}] must be an object")
                continue
            cid = rc.get("claim_id")
            if cid in rc_seen:
                errors.append(f"{pw}: audit_verdict reviewed_claims[{i}] duplicate "
                              f"claim_id {cid!r}")
            else:
                rc_seen.append(cid)
        claims = argument.get("claims", []) or []
        expected_items = [(c.get("claim_id"), c.get("evidence_digest")) for c in claims
                         if isinstance(c, dict) and c.get("gating") is True
                         and c.get("state") == "approved"]
        reviewed_pairs = [(rc.get("claim_id"), rc.get("evidence_digest"))
                         for rc in reviewed_items if isinstance(rc, dict)]
        if reviewed_pairs != expected_items:
            errors.append(f"{pw}: audit_verdict reviewed_claims {reviewed_pairs!r} != "
                          f"gating coverage {expected_items!r} (ordered, canonical claim order)")
        # Per-claim evidence-digest correlation; the expected map is built from the
        # trusted argument claims (unique by construction), never a lossy map over
        # the reviewed list, so a stale digest is named precisely.
        expected_by_id = {c.get("claim_id"): c.get("evidence_digest") for c in claims
                         if isinstance(c, dict) and c.get("gating") is True
                         and c.get("state") == "approved"}
        for rc in reviewed_items:
            if not isinstance(rc, dict):
                continue
            cid = rc.get("claim_id")
            if cid in expected_by_id and rc.get("evidence_digest") != expected_by_id[cid]:
                errors.append(f"{pw}: audit_verdict reviewed_claims[{cid}] evidence_digest "
                              f"{rc.get('evidence_digest')!r} != current {expected_by_id[cid]!r}")
    elif kind == "evidence_leaf":
        if not isinstance(argument, dict) or not argument:
            return
        artifacts = argument.get("artifacts", []) or []
        hrid = payload.get("harness_request_id")
        req = next((a for a in artifacts if isinstance(a, dict)
                   and a.get("kind") == "spike_request"
                   and a.get("harness_request_id") == hrid), None)
        if req is None:
            errors.append(f"{pw}: evidence_leaf harness_request_id {hrid!r} does not "
                          f"match a sealed spike_request")
        else:
            if payload.get("command_digest") != req.get("command_digest"):
                errors.append(f"{pw}: evidence_leaf command_digest {payload.get('command_digest')!r} "
                              f"!= sealed request {req.get('command_digest')!r}")
            if payload.get("prerequisite_research_ids") != req.get("prerequisite_research_ids"):
                errors.append(f"{pw}: evidence_leaf prerequisite_research_ids "
                              f"{payload.get('prerequisite_research_ids')!r} != sealed request "
                              f"{req.get('prerequisite_research_ids')!r}")
        spike = next((a for a in artifacts if isinstance(a, dict)
                    and a.get("kind") == "spike"
                    and a.get("harness_request_id") == hrid), None)
        # D2D-R correction 2: correlate file_bindings EXACTLY and in order to the
        # admitted spike result bindings (path AND sha256), not only request
        # dependent paths; reject duplicate normalized path even when hashes differ
        # (raw schema uniqueItems catches only identical-object duplicates).
        payload_bindings = payload.get("file_bindings", []) or []
        fb_path_seen: list[str] = []
        for b in payload_bindings:
            if not isinstance(b, dict):
                continue
            bp = b.get("path")
            if isinstance(bp, str):
                if bp in fb_path_seen:
                    errors.append(f"{pw}: evidence_leaf file_binding duplicate path {bp!r}")
                else:
                    fb_path_seen.append(bp)
        if spike is None:
            errors.append(f"{pw}: evidence_leaf has no spike result for "
                          f"harness_request_id {hrid!r}")
        else:
            if payload.get("result_digest") != spike.get("artifact_id"):
                errors.append(f"{pw}: evidence_leaf result_digest "
                              f"{payload.get('result_digest')!r} != spike result "
                              f"artifact_id {spike.get('artifact_id')!r}")
            if payload.get("exit_code") != spike.get("exit_code"):
                errors.append(f"{pw}: evidence_leaf exit_code {payload.get('exit_code')!r} "
                              f"!= spike result exit_code {spike.get('exit_code')!r}")
            payload_pairs = [(b.get("path"), b.get("sha256")) for b in payload_bindings
                            if isinstance(b, dict)]
            result_pairs = [(b.get("path"), b.get("sha256"))
                           for b in (spike.get("file_bindings", []) or [])
                           if isinstance(b, dict)]
            if payload_pairs != result_pairs:
                errors.append(f"{pw}: evidence_leaf file_bindings {payload_pairs!r} != "
                              f"spike result bindings {result_pairs!r}")


# Nonsemantic JSON Schema annotation keywords that do not affect validation
# behavior; stripped before semantic comparison so two schemas with identical
# validation semantics compare equal.
_ANNOTATION_KEYS = frozenset({
    "title", "description", "default", "deprecated", "readOnly", "writeOnly",
    "examples", "$comment", "$id", "$schema",
})


def _strip_annotations(node: Any) -> Any:
    """Recursively remove nonsemantic JSON Schema annotation keywords."""
    if isinstance(node, dict):
        return {k: _strip_annotations(v) for k, v in node.items()
                if k not in _ANNOTATION_KEYS}
    if isinstance(node, list):
        return [_strip_annotations(v) for v in node]
    return node


def _resolve_refs(node: Any, defs: dict, seen: frozenset | None = None) -> Any:
    """Recursively resolve local ``#/$defs/<name>`` ``$ref`` pointers, returning a
    self-contained schema node with every ``$ref`` inlined.  Cycles are broken by
    keeping the already-resolved node without following the ref a second time."""
    if seen is None:
        seen = frozenset()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.split("/")[-1]
            if name not in seen:
                return _resolve_refs(defs.get(name, {}), defs, seen | {name})
            node = {k: v for k, v in node.items() if k != "$ref"}
        return {k: _resolve_refs(v, defs, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(v, defs, seen) for v in node]
    return node


def _extract_action_kinds(request_schema: dict) -> set:
    """Extract the set of action kind consts from the request schema $defs."""
    kinds = set()
    defs = request_schema.get("$defs", {})
    action = defs.get("action", {})
    for sub in action.get("oneOf", []):
        ref = sub.get("$ref", "")
        name = ref.split("/")[-1]
        d = defs.get(name, {})
        props = d.get("properties", {})
        k = props.get("kind", {})
        if "const" in k:
            kinds.add(k["const"])
    return kinds


def _consts_from_def(defs: dict, name: str) -> set:
    """Collect type consts from a $def that may be a plain object or a oneOf of variants."""
    d = defs.get(name, {})
    out = set()
    if "oneOf" in d:
        for sub in d.get("oneOf", []):
            ref = sub.get("$ref", "")
            if ref.startswith("#/$defs/"):
                out |= _consts_from_def(defs, ref.split("/")[-1])
            else:
                t = sub.get("properties", {}).get("type", {})
                if "const" in t:
                    out.add(t["const"])
    t = d.get("properties", {}).get("type", {})
    if "const" in t:
        out.add(t["const"])
    return out


def _extract_command_types(request_schema: dict) -> set:
    defs = request_schema.get("$defs", {})
    types = set()
    for sub in request_schema.get("properties", {}).get("command", {}).get("oneOf", []):
        ref = sub.get("$ref", "")
        if ref.startswith("#/$defs/"):
            types |= _consts_from_def(defs, ref.split("/")[-1])
    return types


def _extract_reason_codes(response_schema: dict) -> set:
    """Extract canonical reason code consts from the response schema reasonPayload oneOf."""
    defs = response_schema.get("$defs", {})
    payload = defs.get("reasonPayload", {})
    codes = set()
    for branch in payload.get("oneOf", []):
        resolved = _resolve_refs(branch, defs)
        c = resolved.get("properties", {}).get("code", {}).get("const")
        if isinstance(c, str):
            codes.add(c)
    return codes


def _artifact_branches(response_schema: dict) -> dict:
    """Map each artifact kind const to its resolved branch def from artifactItem.oneOf.

    Each branch is a closed object discriminated by ``kind``; the per-kind outcome and
    spike_gate enums and the research source_kind enum are read from these resolved
    branches so the mirror check derives them from the schema without a Python table.
    """
    defs = response_schema.get("$defs", {})
    item = defs.get("artifactItem", {})
    out: dict[str, dict] = {}
    for sub in item.get("oneOf", []):
        ref = sub.get("$ref", "")
        name = ref.split("/")[-1] if ref.startswith("#/$defs/") else None
        branch = defs.get(name, {}) if name else sub
        kind = branch.get("properties", {}).get("kind", {}).get("const")
        if isinstance(kind, str):
            out[kind] = branch
    return out


def check_reason_params_mirror(response_schema: dict, contract: dict,
                               errors: list[str], where: str) -> None:
    """Compare every reasonPayload parameter schema (with local ``$ref`` pointers
    recursively resolved and nonsemantic annotations stripped) against the
    corresponding registry reason ``params``.  A one-sided enum / required /
    nested / closure mutation in the schema fails with a diagnostic naming the
    reason code, so a schema cannot drift from the registry without detection."""
    defs = response_schema.get("$defs", {})
    payload = defs.get("reasonPayload", {})
    reasons_registry = contract.get("reasons", {})
    for branch in payload.get("oneOf", []):
        resolved = _resolve_refs(branch, defs)
        if not isinstance(resolved, dict):
            continue
        code = resolved.get("properties", {}).get("code", {}).get("const")
        if not isinstance(code, str) or code not in reasons_registry:
            continue
        schema_params = resolved.get("properties", {}).get("parameters", {})
        schema_clean = _strip_annotations(schema_params)
        reg_clean = _strip_annotations(reasons_registry[code].get("params", {}))
        if schema_clean != reg_clean:
            errors.append(
                f"{where}: reasonPayload {code!r} parameters schema "
                f"!= registry params (one-sided enum/required/nested/closure drift)")


def check_schema_mirror(request_schema: dict, response_schema: dict, contract: dict,
                        errors: list[str], where: str) -> None:
    """Mechanically compare request/response schema discriminators/enums to the loaded registry.

    All mirrored values are derived from ``contract`` (the canonical JSON); a one-sided schema
    mutation fails with a precise diagnostic naming the offending enum/const and registry value.
    """
    actions = contract.get("actions", {})
    reg_author = set(actions.get("author", []))
    reg_host = set(actions.get("host", []))
    reg_trusted = set(actions.get("trusted", []))
    reg_kinds = reg_author | reg_host | reg_trusted
    schema_kinds = _extract_action_kinds(request_schema)
    if schema_kinds != reg_kinds:
        errors.append(f"{where}: request action kinds {sorted(schema_kinds)} != registry {sorted(reg_kinds)}")
    reg_cmds = set(contract.get("commands", []))
    schema_cmds = _extract_command_types(request_schema)
    if schema_cmds != reg_cmds:
        errors.append(f"{where}: request command types {sorted(schema_cmds)} != registry {sorted(reg_cmds)}")
    reg_decisions = set(contract.get("decisions", []))
    decisions = set()
    for sub in response_schema.get("properties", {}).get("result", {}).get("oneOf", []):
        ref = sub.get("$ref", "")
        if ref.startswith("#/$defs/"):
            decisions |= _consts_from_def(response_schema.get("$defs", {}), ref.split("/")[-1])
    if decisions != reg_decisions:
        errors.append(f"{where}: response decision types {sorted(decisions)} != registry {sorted(reg_decisions)}")
    defs = response_schema.get("$defs", {})
    rv = defs.get("runView", {}).get("properties", {}).get("status", {})
    if set(rv.get("enum", [])) != set(contract.get("statuses", [])):
        errors.append(f"{where}: response runView.status enum {rv.get('enum')} != registry {sorted(contract.get('statuses', []))}")
    cs = defs.get("childSummary", {}).get("properties", {}).get("state", {})
    reg_child_states = set(contract.get("child_lifecycle", {}).get("states", []))
    if set(cs.get("enum", [])) != reg_child_states:
        errors.append(f"{where}: response childSummary.state enum {cs.get('enum')} != registry {sorted(reg_child_states)}")
    ht = defs.get("hostView", {}).get("properties", {}).get("tier", {})
    reg_tiers = set(contract.get("host_tiers", []))
    if set(ht.get("enum", [])) != reg_tiers:
        errors.append(f"{where}: response hostView.tier enum {ht.get('enum')} != registry {sorted(reg_tiers)}")
    reg_modes = set(contract.get("mode_fields", []))
    req_modes = request_schema.get("$defs", {}).get("modes", {}).get("properties", {})
    if set(req_modes.keys()) != reg_modes:
        errors.append(f"{where}: request modes keys {sorted(req_modes.keys())} != registry {sorted(reg_modes)}")
    rm = defs.get("runModes", {}).get("properties", {})
    if set(rm.keys()) != reg_modes:
        errors.append(f"{where}: response runModes keys {sorted(rm.keys())} != registry {sorted(reg_modes)}")
    fci = set(defs.get("freshnessChangeItem", {}).get("properties", {}).get("state", {}).get("enum", []))
    if fci != set(contract.get("freshness_states", [])):
        errors.append(f"{where}: response freshnessChangeItem.state enum {sorted(fci)} != registry {sorted(contract.get('freshness_states', []))}")
    ci = set(defs.get("claimItem", {}).get("properties", {}).get("state", {}).get("enum", []))
    if ci != set(contract.get("claim_states", [])):
        errors.append(f"{where}: response claimItem.state enum {sorted(ci)} != registry {sorted(contract.get('claim_states', []))}")
    cki = set(defs.get("claimItem", {}).get("properties", {}).get("kind", {}).get("enum", []))
    if cki != set(contract.get("claim_kinds", [])):
        errors.append(f"{where}: response claimItem.kind enum {sorted(cki)} != registry "
                      f"claim_kinds {sorted(contract.get('claim_kinds', []))}")
    avs = set(defs.get("auditView", {}).get("properties", {}).get("state", {}).get("enum", []))
    if avs != set(contract.get("audit_states", [])):
        errors.append(f"{where}: response auditView.state enum {sorted(avs)} != registry {sorted(contract.get('audit_states', []))}")
    avi = set(defs.get("auditView", {}).get("properties", {}).get("independence", {}).get("enum", []))
    if avi != set(contract.get("independence_states", [])):
        errors.append(f"{where}: response auditView.independence enum {sorted(avi)} != registry {sorted(contract.get('independence_states', []))}")
    ud = defs.get("untrustedDelimiters", {}).get("properties", {})
    reg_ud = contract.get("untrusted_delimiters", {})
    if (ud.get("open", {}).get("const") != reg_ud.get("open")
            or ud.get("close", {}).get("const") != reg_ud.get("close")):
        errors.append(f"{where}: response untrustedDelimiters consts {ud} != registry {reg_ud}")
    # Reason code discriminator: schema reasonPayload branch consts == registry reason keys.
    reg_reasons = set(contract.get("reasons", {}))
    schema_reasons = _extract_reason_codes(response_schema)
    if schema_reasons != reg_reasons:
        errors.append(f"{where}: response reasonPayload codes {sorted(schema_reasons)} != registry reason keys {sorted(reg_reasons)}")
    # Reason parameter schemas: resolve $refs, strip annotations, compare to registry.
    check_reason_params_mirror(response_schema, contract, errors, where)
    # D2C artifact provenance vocabularies are canonical-JSON (artifact_kinds,
    # artifact_outcomes, spike_gates); the response schema derives every per-kind enum
    # from them. research outcomes = artifact_outcomes \ spike_gates; spike outcomes =
    # spike_gates (mechanically equivalent to the gate); spike_gate enum = spike_gates.
    reg_kinds = set(contract.get("artifact_kinds", []))
    reg_outcomes = set(contract.get("artifact_outcomes", []))
    reg_gates = set(contract.get("spike_gates", []))
    branches = _artifact_branches(response_schema)
    if set(branches) != reg_kinds:
        errors.append(f"{where}: response artifactItem kinds {sorted(branches)} != registry "
                      f"artifact_kinds {sorted(reg_kinds)}")
    research_b = branches.get("research", {})
    spike_b = branches.get("spike", {})
    research_outcomes = set(research_b.get("properties", {}).get("outcome", {}).get("enum", []))
    spike_outcomes = set(spike_b.get("properties", {}).get("outcome", {}).get("enum", []))
    spike_gate_enum = set(spike_b.get("properties", {}).get("spike_gate", {}).get("enum", []))
    if research_outcomes != (reg_outcomes - reg_gates):
        errors.append(f"{where}: response researchArtifact.outcome enum {sorted(research_outcomes)} != "
                      f"registry artifact_outcomes minus spike_gates {sorted(reg_outcomes - reg_gates)}")
    if spike_outcomes != reg_gates:
        errors.append(f"{where}: response spikeArtifact.outcome enum {sorted(spike_outcomes)} != "
                      f"registry spike_gates {sorted(reg_gates)}")
    if spike_gate_enum != reg_gates:
        errors.append(f"{where}: response spikeArtifact.spike_gate enum {sorted(spike_gate_enum)} != "
                      f"registry spike_gates {sorted(reg_gates)}")
    esk = set(research_b.get("properties", {}).get("source_kind", {}).get("enum", []))
    if esk != EVIDENCE_SOURCE_KINDS:
        errors.append(f"{where}: response researchArtifact.source_kind enum {sorted(esk)} != "
                      f"{sorted(EVIDENCE_SOURCE_KINDS)}")
    et = set(defs.get("edgeItem", {}).get("properties", {}).get("type", {}).get("enum", []))
    if et != EDGE_TYPES:
        errors.append(f"{where}: response edgeItem.type enum {sorted(et)} != {sorted(EDGE_TYPES)}")
    # D2D: trusted-ingress payload vocabularies are canonical-JSON and mirrored from the
    # request schema into the loaded registry. Child event states reuse child_lifecycle.states
    # minus reserved; no Python EXPECTED_* table duplicates the registry values.
    req_defs = request_schema.get("$defs", {})
    # child_event state enum == child_lifecycle.states - {reserved}.
    ce_state = set(req_defs.get("childEventPayload", {}).get("properties", {})
                  .get("state", {}).get("enum", []))
    reg_child_states = set(contract.get("child_lifecycle", {}).get("states", []))
    expected_ce_states = reg_child_states - {"reserved"}
    if ce_state != expected_ce_states:
        errors.append(f"{where}: request childEventPayload.state enum {sorted(ce_state)} != "
                      f"registry child_lifecycle.states minus reserved {sorted(expected_ce_states)}")
    # attribution subject_kind / observed_by enums.
    ask = set(req_defs.get("attributionPayload", {}).get("properties", {})
             .get("subject_kind", {}).get("enum", []))
    if ask != set(contract.get("attribution_subject_kinds", [])):
        errors.append(f"{where}: request attributionPayload.subject_kind enum {sorted(ask)} != "
                      f"registry attribution_subject_kinds {sorted(contract.get('attribution_subject_kinds', []))}")
    aob = set(req_defs.get("attributionPayload", {}).get("properties", {})
             .get("observed_by", {}).get("enum", []))
    if aob != set(contract.get("attribution_observers", [])):
        errors.append(f"{where}: request attributionPayload.observed_by enum {sorted(aob)} != "
                      f"registry attribution_observers {sorted(contract.get('attribution_observers', []))}")
    # audit_verdict verdict enum.
    avv = set(req_defs.get("auditVerdictPayload", {}).get("properties", {})
             .get("verdict", {}).get("enum", []))
    if avv != set(contract.get("audit_verdicts", [])):
        errors.append(f"{where}: request auditVerdictPayload.verdict enum {sorted(avv)} != "
                      f"registry audit_verdicts {sorted(contract.get('audit_verdicts', []))}")
    # audit_verdict scope_review enum (non-null values) == scope_reviews.
    sr_props = req_defs.get("auditVerdictPayload", {}).get("properties", {})
    sr_enum = sr_props.get("scope_review", {}).get("enum", [])
    sr_nonnull = {v for v in sr_enum if v is not None}
    if sr_nonnull != set(contract.get("scope_reviews", [])):
        errors.append(f"{where}: request auditVerdictPayload.scope_review enum {sorted(sr_nonnull)} != "
                      f"registry scope_reviews {sorted(contract.get('scope_reviews', []))}")


def check_getcontract_fixture(request: dict, directive: dict, registry: dict, validate_fn: Callable,
                              errors: list[str], where: str) -> None:
    """Correlate GetContract request target/section with directive; materialize + validate response."""
    cmd = request.get("command", {})
    req_target = cmd.get("target")
    dir_target = directive.get("target")
    if req_target != dir_target:
        errors.append(f"{where}: request target {req_target!r} != directive target {dir_target!r}")
    if req_target == "section":
        req_sid = cmd.get("section_id")
        dir_sid = directive.get("section_id")
        if req_sid != dir_sid:
            errors.append(f"{where}: request section_id {req_sid!r} != directive section_id {dir_sid!r}")
        if req_sid not in registry.get("sections", {}):
            errors.append(f"{where}: section_id {req_sid!r} not in registry")
    materialized = {"protocol": "empirica/v2", "request_id": request.get("request_id", ""),
                    "result": {"type": "Allow",
                               "contract_result": materialize_contract_result(
                                   registry, dir_target, directive.get("section_id"))}}
    validate_fn(materialized, "empirica/v2", "response", f"{where}:materialized")
    # D2B: the materialized contract_result.digest must equal the canonical registry digest
    # (exact equality; derived, never a duplicated constant).
    check_getcontract_digest(materialized["result"]["contract_result"], registry,
                             errors, f"{where}:materialized")
    # For section, the materialized section id must match the directive.
    if dir_target == "section":
        sid = materialized["result"]["contract_result"].get("section", {}).get("id")
        if sid != directive.get("section_id"):
            errors.append(f"{where}: materialized section id {sid!r} != directive {directive.get('section_id')!r}")


# --------------------------------------------------------------------------- #
# main() — all I/O and orchestration; passes explicit data to pure checks.
# --------------------------------------------------------------------------- #
def main() -> int:
    errors: list[str] = []

    def load(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
            return {}
        if not isinstance(value, dict):
            errors.append(f"{path.relative_to(ROOT)}: root must be an object")
            return {}
        return value

    # --- schema discovery (auto-discovers v1 + v2) ---
    schemas: dict[tuple[str, str], dict] = {}
    for path in sorted(CONTRACTS.glob("*/*/*.schema.json")):
        data = load(path)
        protocol = "/".join(path.relative_to(CONTRACTS).parts[:2])
        kind = path.name.removesuffix(".schema.json")
        schemas[(protocol, kind)] = data
        if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"{path.relative_to(ROOT)}: must use JSON Schema 2020-12")
        if not isinstance(data.get("$id"), str):
            errors.append(f"{path.relative_to(ROOT)}: missing $id")

    # --- referencing.Registry (current API; no RefResolver) ---
    registry_obj = None
    if Registry is not None:
        resources = []
        for (protocol, kind), schema in schemas.items():
            uri = schema.get("$id")
            if isinstance(uri, str):
                resources.append((uri, Resource.from_contents(schema, default_specification=DRAFT202012)))
        registry_obj = Registry().with_resources(resources)

    def validate_schema_instance(instance: Any, protocol: str, kind: str, where: str) -> None:
        if Draft202012Validator is None:
            return
        schema = schemas.get((protocol, kind))
        if schema is None:
            errors.append(f"{where}: no {kind} schema for {protocol!r}")
            return
        validator = Draft202012Validator(schema, registry=registry_obj)
        for error in validator.iter_errors(instance):
            errors.append(f"{where}: {error.message}")

    # --- v1 substrate-neutral fixtures (D6-C: v1 schemas removed; structural checks only) ---
    # D6-C deletes contracts/empirica/v1, so v1 fixture envelopes can no longer be schema-validated.
    # The negative v1 wire examples remain allowed (spec §7) for host-suite strict refusal; only
    # structural identity (request_id presence, request/expected protocol + id correlation) is
    # enforced here. Schema validation below is best-effort: it runs only when a schema exists.
    REQUIRED_EMPIRICA_FIXTURES = frozenset({"empirica-get-argument.json", "empirica-void-spawn.json"})
    fixture_paths = sorted((CONTRACTS / "fixtures").glob("*.json"))
    missing = REQUIRED_EMPIRICA_FIXTURES - {p.name for p in fixture_paths}
    for name in sorted(missing):
        errors.append(f"contracts/fixtures/{name}: required Empirica audit fixture is missing")
    for path in fixture_paths:
        fixture = load(path)
        for field, kind in (("request", "request"), ("expected", "response")):
            envelope = fixture.get(field)
            if not isinstance(envelope, dict):
                errors.append(f"{path.relative_to(ROOT)}: {field} must be an object")
                continue
            if not isinstance(envelope.get("request_id"), str) or not envelope["request_id"]:
                errors.append(f"{path.relative_to(ROOT)}: {field}.request_id is required")
        request, expected = fixture.get("request", {}), fixture.get("expected", {})
        if request.get("protocol") != expected.get("protocol"):
            errors.append(f"{path.relative_to(ROOT)}: request/expected protocols differ")
        if request.get("request_id") != expected.get("request_id"):
            errors.append(f"{path.relative_to(ROOT)}: request/expected ids differ")
        for field, kind in (("request", "request"), ("expected", "response")):
            envelope = fixture.get(field, {})
            protocol = envelope.get("protocol") if isinstance(envelope, dict) else None
            if (protocol, kind) in schemas:
                validate_schema_instance(envelope, protocol, kind, f"{path.relative_to(ROOT)}:{field}")

    # --- v2 registry ---
    registry = load(V2 / "public-contract.json")
    if registry:
        validate_schema_instance(registry, "empirica/v2", "public-contract",
                                 "contracts/empirica/v2/public-contract.json")
        check_identity(registry, errors, "public-contract")
        check_registry_digest(registry, errors, "public-contract")
        check_key_sets(registry, errors, "public-contract")
        check_referential(registry, errors, "public-contract")
        check_clauses(registry, errors, "public-contract")
        check_child_lifecycle(registry, errors, "public-contract")
        check_presentation_selector(registry, errors, "public-contract")
        try:
            sys.path.insert(0, str(ROOT / "plugins" / "empirica"))
            from core.context_selector import select_sections
            selector = registry["presentation_selector"]
            for context, expected in selector["context_sections"].items():
                if select_sections(registry, context, [], None) != expected:
                    errors.append(f"context-selector: context {context!r} drift")
            for code, reason in registry["reasons"].items():
                expected = list(dict.fromkeys(
                    [*selector["context_sections"]["block"], *reason["sections"]]))
                if select_sections(registry, "block", [code], None) != expected:
                    errors.append(f"context-selector: reason {code!r} drift")
            if select_sections(registry, "block", ["unknown.reason"], None) != selector["unknown_reason_sections"]:
                errors.append("context-selector: unknown-reason fallback drift")
        except (ImportError, KeyError, TypeError) as exc:
            errors.append(f"context-selector: unavailable: {exc}")
    else:
        errors.append("contracts/empirica/v2/public-contract.json: required v2 registry is missing")

    # --- host profiles ---
    host_profiles_doc = load(V2 / "host-profiles.json")
    if host_profiles_doc:
        validate_schema_instance(host_profiles_doc, "empirica/v2", "host-profiles",
                                 "contracts/empirica/v2/host-profiles.json")
    else:
        errors.append("contracts/empirica/v2/host-profiles.json: required v2 host profiles are missing")

    # --- schema↔registry mirror ---
    req_schema = schemas.get(("empirica/v2", "request"), {})
    res_schema = schemas.get(("empirica/v2", "response"), {})
    if req_schema and res_schema and registry:
        check_schema_mirror(req_schema, res_schema, registry, errors, "schema-mirror")

    # --- D6-A state schema ↔ registry mirror + state fixtures ---
    state_schema = schemas.get(("empirica/v2", "state"), {})
    if state_schema and registry:
        check_state_schema_mirror(state_schema, registry, req_schema, res_schema,
                                   errors, "state-schema-mirror")
        check_state_child_branch_matrix(state_schema, registry, errors, "state-child-matrix")
    state_fixture_dir = V2 / "state-fixtures"
    state_fixture_paths = sorted(state_fixture_dir.glob("*.json")) if state_fixture_dir.is_dir() else []
    REQUIRED_STATE_FIXTURES = {
        "valid-active", "valid-converged",
        "invalid-missing-goal", "invalid-extra-field", "invalid-status",
        "invalid-child-duplicate-id", "invalid-counter", "invalid-stamp",
        "invalid-child-branch", "invalid-deadline-nan", "invalid-refund-mismatch",
        "invalid-budget-reconciliation",
    }
    state_names = {p.name.removesuffix(".json") for p in state_fixture_paths}
    for name in sorted(REQUIRED_STATE_FIXTURES - state_names):
        errors.append(f"contracts/empirica/v2/state-fixtures/{name}.json: required state fixture is missing")
    # Map fixture name → (expect_valid, expected_substring).
    state_expect: dict[str, tuple[bool, str | None]] = {
        "valid-active": (True, None),
        "valid-converged": (True, None),
        "invalid-missing-goal": (False, "goal"),
        "invalid-extra-field": (False, "Additional properties"),
        "invalid-status": (False, "not_a_status"),
        "invalid-child-duplicate-id": (False, "duplicate child_id"),
        "invalid-counter": (False, "passes_used"),
        "invalid-stamp": (False, "route_stamp"),
        "invalid-child-branch": (False, "False was expected"),
        "invalid-deadline-nan": (False, "finite number"),
        "invalid-refund-mismatch": (False, "launch_rejected"),
        "invalid-budget-reconciliation": (False, "reconcile"),
    }
    for path in state_fixture_paths:
        name = path.name.removesuffix(".json")
        where = str(path.relative_to(ROOT))
        state = load(path)
        expect_valid, expected_sub = state_expect.get(name, (True, None))
        if expect_valid:
            validate_schema_instance(state, "empirica/v2", "state", where)
            check_state_invariants(state, errors, where)
        else:
            before = len(errors)
            validate_schema_instance(state, "empirica/v2", "state", where)
            check_state_invariants(state, errors, where)
            produced = errors[before:]
            del errors[before:]
            if not produced:
                errors.append(f"{where}: invalid state fixture was NOT rejected")
            elif expected_sub and not any(expected_sub in e for e in produced):
                errors.append(f"{where}: rejected but no diagnostic contains "
                              f"{expected_sub!r}; got {produced}")

    # --- v2 fixtures ---
    REQUIRED_V2_FIXTURES = {
        "start-bootstrap-allow", "block-open-claim", "block-stale-spike",
        "block-pending-audit", "block-child-terminal", "allow-stopped-frozen",
        "allow-stopped-budget", "allow-converged", "block-corrupt-state",
        "getcontract-index", "getcontract-section", "getcontract-full",
        # D2E added presentation_selector GetContract section fixture.
        "getcontract-presentation-selector",
        "block-host-async-unsupported",
        "observe-child-event-redacted",
        # D2A §7 added fixtures.
        "getargument-active", "getargument-audited", "restore-active",
        "block-stale-spike-missing", "block-deferred-scope",
        "block-child-cancelled", "block-child-timeout", "block-child-orphaned",
        # D2C added GetArgument provenance fixtures.
        "getargument-spike-approved", "getargument-superseded",
        # D2D added closed trusted-ingress payload fixtures.
        "observe-attribution-covered-actor",
        "observe-attribution-covered-actor-same-model",
        "observe-attribution-covered-actor-unverified",
        "observe-attribution-auditor",
        "observe-audit-verdict", "observe-audit-verdict-frozen",
        "observe-evidence-leaf",
    }
    v2_fixture_paths = sorted((V2 / "fixtures").glob("*.json"))
    v2_names = {p.name.removesuffix(".json") for p in v2_fixture_paths}
    for name in sorted(REQUIRED_V2_FIXTURES - v2_names):
        errors.append(f"contracts/empirica/v2/fixtures/{name}.json: required v2 fixture is missing")

    host_tiers_by_profile = {}
    if host_profiles_doc:
        for p in host_profiles_doc.get("profiles", []):
            if isinstance(p, dict):
                host_tiers_by_profile[p.get("profile_id")] = p.get("current_tier")
        check_host_profiles(host_profiles_doc, registry, v2_names, errors, "host-profiles")

    digest = registry_digest(registry) if registry else ""

    for path in v2_fixture_paths:
        fx = load(path)
        where = str(path.relative_to(ROOT))
        request = fx.get("request", {})
        # GetContract fixtures use expected_from_registry (no hand-copied registry).
        if "expected_from_registry" in fx:
            validate_schema_instance(request, "empirica/v2", "request", f"{where}:request")
            check_getcontract_fixture(request, fx["expected_from_registry"], registry,
                                       validate_schema_instance, errors, where)
            continue
        expected = fx.get("expected", {})
        if request.get("protocol") != "empirica/v2" or expected.get("protocol") != "empirica/v2":
            errors.append(f"{where}: v2 fixtures must use protocol empirica/v2")
        if request.get("request_id") != expected.get("request_id"):
            errors.append(f"{where}: request/expected ids differ")
        validate_schema_instance(request, "empirica/v2", "request", f"{where}:request")
        validate_schema_instance(expected, "empirica/v2", "response", f"{where}:expected")
        check_protocol_identity(expected, errors, f"{where}:expected")
        check_banned_fields(expected, errors, f"{where}:expected")
        result = expected.get("result", {})
        check_allow_cross_field(result, errors, f"{where}:expected")
        check_response_reasons(result, registry, errors, f"{where}:expected")
        check_run_view_fields(result, registry, errors, f"{where}:expected")
        check_stale_freshness_match(result, errors, f"{where}:expected")
        check_response_run_view(result, registry, host_tiers_by_profile, digest, errors, f"{where}:expected")
        check_argument_view(result, registry, errors, f"{where}:expected")
        check_getargument_exclusivity(request, result, errors, f"{where}:expected")
        command = request.get("command", {})
        if isinstance(command, dict) and command.get("type") == "ObserveAction":
            reg_actions = registry.get("actions", {})
            check_trusted_action(command.get("action", {}),
                                 set(reg_actions.get("author", [])),
                                 set(reg_actions.get("trusted", [])),
                                 errors, f"{where}:request.action", redacted=True)
            check_trusted_payload(request, result, registry,
                                  errors, f"{where}")

    # --- in-memory negative cases: one mutation each, expected substring ---
    run_negatives(registry, host_profiles_doc, v2_names, res_schema, req_schema,
                  state_schema, errors, validate_schema_instance)

    if errors:
        print("\n".join(f"ERROR: {e}" for e in errors), file=sys.stderr)
        return 1
    print(f"ok: {len(schemas)} schemas, {len(fixture_paths)} fixtures, "
          f"{len(v2_fixture_paths)} v2 fixtures")
    return 0


def run_negatives(registry: dict, host_profiles_doc: dict, required_fixtures: set,
                  res_schema: dict, req_schema: dict, state_schema: dict,
                  errors: list[str], validate_schema: Callable) -> None:
    """Each case: one mutation + expected diagnostic substring (cannot pass for wrong reason).

    ``expect`` takes a thunk that receives a fresh error list and runs one check,
    so checks with heterogeneous signatures all route through the same harness.
    """
    import copy

    def expect(run, expected: str, label: str) -> None:
        local: list[str] = []
        run(local)
        if not local:
            errors.append(f"NEG {label}: expected rejection but none produced")
        elif not any(expected in e for e in local):
            errors.append(f"NEG {label}: rejected but no diagnostic contains {expected!r}; got {local}")

    if not registry:
        return
    d64 = "sha256:" + "f" * 64

    # Registry-side mutations. Exact vocabulary freeze is now via the reviewed digest
    # and schema mirror; a one-sided mutation must still fail precisely.
    bad = copy.deepcopy(registry)
    bad["reasons"]["run.no_active"]["next_actions"].append("bogus.action")
    expect(lambda e: check_referential(bad, e, "neg"), "unknown action", "unknown action link")
    bad = copy.deepcopy(registry)
    bad["reasons"]["run.no_active"]["sections"].append("bogus/section")
    expect(lambda e: check_referential(bad, e, "neg"), "unknown section", "unknown section link")
    bad = copy.deepcopy(registry)
    bad["child_lifecycle"]["transitions"].append({"from": "reserved", "to": "bogus_state"})
    expect(lambda e: check_child_lifecycle(bad, e, "neg"), "references unknown state", "invalid child transition")
    bad = copy.deepcopy(registry)
    bad["child_lifecycle"]["transitions"].append({"from": "reserved", "to": "launching"})
    expect(lambda e: check_child_lifecycle(bad, e, "neg"), "duplicate child transition", "duplicate child transition")
    bad = copy.deepcopy(registry)
    bad["child_lifecycle"]["terminal_states"].append("bogus_state")
    expect(lambda e: check_child_lifecycle(bad, e, "neg"), "subset of states", "nonterminal listed terminal")
    bad = copy.deepcopy(registry)
    bad["commands"].append("Bogus")
    expect(lambda e: check_registry_digest(bad, e, "neg"), "registry digest", "unknown command")
    bad = copy.deepcopy(registry)
    bad["sections"].pop("core", None)
    expect(lambda e: check_registry_digest(bad, e, "neg"), "registry digest", "section keys drift")
    bad = copy.deepcopy(registry)
    bad["reasons"].pop("run.no_active", None)
    expect(lambda e: check_registry_digest(bad, e, "neg"), "registry digest", "remove reason id")
    bad = copy.deepcopy(registry)
    bad["next_actions"].pop("run.start_fresh", None)
    expect(lambda e: check_registry_digest(bad, e, "neg"), "registry digest", "remove next_action id")
    bad = copy.deepcopy(registry)
    bad["claim_states"] = ["open", "approved"]
    expect(lambda e: check_registry_digest(bad, e, "neg"), "registry digest", "canonical claim_states drift")
    bad = copy.deepcopy(registry)
    bad["untrusted_delimiters"]["open"] = "<<<WRONG>>>"
    expect(lambda e: check_registry_digest(bad, e, "neg"), "registry digest", "canonical delimiters drift")

    # Host profile: independent negatives with matching diagnostics.
    # (a) wrong version -> digest detects; range -> structural version check detects.
    bad_ver = copy.deepcopy(host_profiles_doc)
    bad_ver["profiles"][0]["version"] = "9.9.9"
    expect(lambda e: check_host_profiles(bad_ver, registry, required_fixtures, e, "neg"),
           "host-profiles digest", "host profile wrong version")
    bad_range = copy.deepcopy(host_profiles_doc)
    bad_range["profiles"][0]["version"] = ">=2.0"
    expect(lambda e: check_host_profiles(bad_range, registry, required_fixtures, e, "neg"),
           "must be exact (no range)", "host profile semver range")
    # (b) duplicate profile ID.
    bad_dup = copy.deepcopy(host_profiles_doc)
    bad_dup["profiles"].append(copy.deepcopy(bad_dup["profiles"][0]))
    expect(lambda e: check_host_profiles(bad_dup, registry, required_fixtures, e, "neg"),
           "duplicate profile_id", "host profile duplicate ID")
    # (c) extra unknown profile ID -> digest + structural detect.
    bad_extra = copy.deepcopy(host_profiles_doc)
    bad_extra["profiles"].append({
        "host_id": "x", "profile_id": "x@1.0", "version": "1.0", "current_tier": "foreground_only",
        "required_fixture_ids": [], "required_live_probe_ids": ["p"],
        "promotion_status": "pending_live",
        "input_private": "unverified", "output_private": False, "unsupported_reason_ids": [],
        "sources": ["s"]})
    expect(lambda e: check_host_profiles(bad_extra, registry, required_fixtures, e, "neg"),
           "host-profiles digest", "host profile extra unknown ID")
    # (d) fact drift: current_tier changed -> digest detects.
    bad_facts = copy.deepcopy(host_profiles_doc)
    bad_facts["profiles"][0]["current_tier"] = "observational"
    expect(lambda e: check_host_profiles(bad_facts, registry, required_fixtures, e, "neg"),
           "host-profiles digest", "host profile current_tier drift")
    # (e) fact drift: required_live_probe_ids changed -> digest detects.
    bad_probe = copy.deepcopy(host_profiles_doc)
    bad_probe["profiles"][0]["required_live_probe_ids"] = ["WRONG_PROBE"]
    expect(lambda e: check_host_profiles(bad_probe, registry, required_fixtures, e, "neg"),
           "host-profiles digest", "host profile live_probe drift")

    # Response reason mutations.
    expect(lambda e: check_response_reasons(
        {"type": "Block", "run": {}, "reasons": [{"code": "budget.exhausted", "parameters": {},
         "next_actions": ["budget.raise", "residual.accept"], "sections": ["budget"]}]},
        registry, e, "neg"), "parameters invalid", "budget.exhausted missing resource")
    expect(lambda e: check_response_reasons(
        {"type": "Block", "run": {}, "reasons": [{"code": "child.terminal", "parameters": {"state": "started"},
         "next_actions": ["child.retry", "residual.accept"], "sections": ["children"]}]},
        registry, e, "neg"), "parameters invalid", "child.terminal invalid state")
    expect(lambda e: check_response_reasons(
        {"type": "Block", "run": {}, "reasons": [{"code": "audit.stale",
         "parameters": {"scope": "argument", "claim_id": "G0"},
         "next_actions": ["child.spawn_auditor"], "sections": ["audit"]}]},
        registry, e, "neg"), "claim_id permitted only", "audit.stale claim_id wrong scope")
    expect(lambda e: check_response_reasons(
        {"type": "Block", "run": {}, "reasons": [{"code": "run.no_active", "parameters": {},
         "next_actions": ["run.start_fresh", "bogus"], "sections": ["run/lifecycle"]}]},
        registry, e, "neg"), "next_actions", "reason next_actions not exact registry order")

    # Allow.converged / RunView.converged duplication.
    expect(lambda e: check_allow_cross_field(
        {"type": "Allow", "converged": True, "run": {"id": "r", "goal": "g", "status": "active",
         "contract": {"id": "empirica/public", "version": "2.0.0", "digest": d64, "relevant_sections": []}}},
        e, "neg"), "Allow.converged=True must equal", "converged/status contradiction")
    expect(lambda e: check_allow_cross_field(
        {"type": "Allow", "converged": False, "run": {"id": "r", "goal": "g", "status": "active",
         "converged": False, "contract": {"id": "empirica/public", "version": "2.0.0",
         "digest": d64, "relevant_sections": []}}},
        e, "neg"), "RunView must not duplicate converged", "RunView converged duplication")

    # Banned v1 fields.
    expect(lambda e: check_banned_fields(
        {"protocol": "empirica/v2", "request_id": "x", "result": {"type": "Allow", "converged": False,
         "run": {"id": "r", "goal": "g", "status": "active", "contract": {"id": "empirica/public",
         "version": "2.0.0", "digest": d64, "relevant_sections": []},
         "nonce": "abc", "phase": "investigate"}}},
        e, "neg"), "banned field", "banned v1 fields in response")

    # Protocol identity.
    expect(lambda e: check_protocol_identity(
        {"protocol": "empirica/v1", "request_id": "x", "result": {}}, e, "neg"),
        "protocol must be exactly", "non-v2 protocol identity")

    # Trusted/author envelope misuse.
    reg_actions = registry.get("actions", {})
    author_set = set(reg_actions.get("author", []))
    trusted_set = set(reg_actions.get("trusted", []))
    expect(lambda e: check_trusted_action(
        {"kind": "child_event", "child_id": "c"}, author_set, trusted_set, e, "neg", redacted=True),
        "must carry a trusted envelope", "trusted action without envelope")
    expect(lambda e: check_trusted_action(
        {"kind": "research", "trusted": {"capability_ref": "<redacted>", "boundary": "host"}},
        author_set, trusted_set, e, "neg", redacted=True),
        "must not carry a trusted envelope", "author action carrying trusted envelope")

    # Run-view digest mismatch + residual params + child recovery action.
    expect(lambda e: check_response_run_view(
        {"type": "Allow", "converged": False, "run": {"id": "r", "goal": "g", "status": "active",
         "contract": {"id": "empirica/public", "version": "2.0.0", "digest": "sha256:" + "0"*64,
         "relevant_sections": []}}},
        registry, {}, d64, e, "neg"), "run.contract.digest", "run view digest mismatch")
    expect(lambda e: check_response_run_view(
        {"type": "Block", "run": {"id": "r", "goal": "g", "status": "active",
         "contract": {"id": "empirica/public", "version": "2.0.0", "digest": d64, "relevant_sections": []},
         "children": [{"child_id": "c", "purpose": "audit", "state": "completed",
         "recovery_action": "bogus.action"}]}},
        registry, {}, d64, e, "neg"), "recovery_action", "child recovery action unknown")
    expect(lambda e: check_response_run_view(
        {"type": "Block", "run": {"id": "r", "goal": "g", "status": "active",
         "contract": {"id": "empirica/public", "version": "2.0.0", "digest": d64, "relevant_sections": []},
         "residuals": [{"code": "budget.exhausted", "parameters": {}}]}},
        registry, {}, d64, e, "neg"), "parameters invalid", "residual params not validated")

    # Direct schema negatives: convergence contradictions both directions.
    def schema_rejects(envelope: dict, kind: str) -> bool:
        before = len(errors)
        validate_schema(envelope, "empirica/v2", kind, "neg-schema")
        # Move any schema errors into a local check; return True if rejected.
        produced = errors[before:]
        del errors[before:]
        return bool(produced)
    ci = {"id": "empirica/public", "version": "2.0.0", "digest": d64, "relevant_sections": []}
    conv_true_active = {"protocol": "empirica/v2", "request_id": "x",
                        "result": {"type": "Allow", "converged": True,
                                   "run": {"id": "r", "goal": "g", "status": "active", "contract": ci}}}
    if not schema_rejects(conv_true_active, "response"):
        errors.append("NEG schema converged=true+active: expected schema rejection but none")
    conv_false_converged = {"protocol": "empirica/v2", "request_id": "x",
                            "result": {"type": "Allow", "converged": False,
                                       "run": {"id": "r", "goal": "g", "status": "converged", "contract": ci}}}
    if not schema_rejects(conv_false_converged, "response"):
        errors.append("NEG schema converged=false+converged: expected schema rejection but none")

    # Direct schema negatives: GetContract request closed branches reject siblings.
    gc_index_sibling = {"protocol": "empirica/v2", "request_id": "x",
                        "command": {"type": "GetContract", "target": "index", "section_id": "core"}}
    if not schema_rejects(gc_index_sibling, "request"):
        errors.append("NEG schema GetContract index+section_id: expected schema rejection but none")
    gc_section_no_id = {"protocol": "empirica/v2", "request_id": "x",
                        "command": {"type": "GetContract", "target": "section"}}
    if not schema_rejects(gc_section_no_id, "request"):
        errors.append("NEG schema GetContract section without section_id: expected schema rejection but none")

    # Direct schema negatives: GetContract response closed branches reject sibling target payloads.
    # Each sibling case carries a well-formed digest so the only mutation is the sibling payload;
    # this preserves the sibling-target rejection semantics after the D2B required-digest change.
    idx = {"id": "empirica/public", "version": "2.0.0", "sections": [], "reasons": [], "next_actions": []}
    sec = {"id": "core", "title": "t", "summary": "", "clauses": []}
    res_index_full = {"protocol": "empirica/v2", "request_id": "x",
                      "result": {"type": "Allow", "contract_result": {"target": "index", "digest": d64,
                                   "index": idx, "full": {}}}}
    if not schema_rejects(res_index_full, "response"):
        errors.append("NEG schema contractResult index+full sibling: expected schema rejection but none")
    res_section_index = {"protocol": "empirica/v2", "request_id": "x",
                         "result": {"type": "Allow",
                                    "contract_result": {"target": "section", "digest": d64,
                                                         "section_id": "core",
                                                         "section": sec, "index": idx}}}
    if not schema_rejects(res_section_index, "response"):
        errors.append("NEG schema contractResult section+index sibling: expected schema rejection but none")
    res_full_section_id = {"protocol": "empirica/v2", "request_id": "x",
                            "result": {"type": "Allow", "contract_result": {"target": "full", "digest": d64,
                                       "full": registry, "section_id": "core"}}}
    if not schema_rejects(res_full_section_id, "response"):
        errors.append("NEG schema contractResult full+section_id sibling: expected schema rejection but none")

    # D2B: raw-schema missing-digest probes for each GetContract target (one mutation each). The
    # target payload is valid for its branch; the only mutation is the omitted digest.
    gc_index_no_digest = {"protocol": "empirica/v2", "request_id": "x",
        "result": {"type": "Allow", "contract_result": {"target": "index", "index": idx}}}
    if not schema_rejects(gc_index_no_digest, "response"):
        errors.append("NEG schema GetContract index missing digest: expected schema rejection")
    gc_section_no_digest = {"protocol": "empirica/v2", "request_id": "x",
        "result": {"type": "Allow", "contract_result": {"target": "section", "section_id": "core",
                                                             "section": sec}}}
    if not schema_rejects(gc_section_no_digest, "response"):
        errors.append("NEG schema GetContract section missing digest: expected schema rejection")
    gc_full_no_digest = {"protocol": "empirica/v2", "request_id": "x",
        "result": {"type": "Allow", "contract_result": {"target": "full", "full": registry}}}
    if not schema_rejects(gc_full_no_digest, "response"):
        errors.append("NEG schema GetContract full missing digest: expected schema rejection")
    # D2B: raw-schema malformed-digest probe (well-formed sha256 shape but not the issue here; the
    # schema only enforces format, so a non-hex garbage value is rejected by the digest256 pattern).
    gc_index_bad_digest = {"protocol": "empirica/v2", "request_id": "x",
        "result": {"type": "Allow", "contract_result": {"target": "index", "digest": "not-a-digest",
                                                             "index": idx}}}
    if not schema_rejects(gc_index_bad_digest, "response"):
        errors.append("NEG schema GetContract index malformed digest: expected schema rejection")
    # D2B: procedural wrong-but-well-formed digest probe. The raw schema cannot compare against
    # the registry, so the validator's check_getcontract_digest must reject a well-formed digest
    # that is not the canonical registry digest (one mutation; expects its own diagnostic).
    expect(lambda e: check_getcontract_digest(
        {"target": "index", "digest": d64, "index": idx}, registry, e, "neg"),
        "contract_result.digest", "GetContract wrong-but-well-formed digest")

    # D2A §8/§9 negatives: one mutation each with its own expected diagnostic substring.
    full_rv = {"id": "r", "goal": "g", "status": "active", "modes": {"multi_provider": False, "cli_exec": False},
               "contract": {"id": "empirica/public", "version": "2.0.0", "digest": d64, "relevant_sections": []},
               "obligations": {"active": [], "deferred": []}, "residuals": [],
               "freshness": {"changes": []}, "children": [], "next_actions": [],
               "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                                        "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
               "host": {"profile_id": "claude-code@2.1.270", "tier": "foreground_only",
                        "missing_capabilities": ["host.async_unsupported"]}}

    # (a) old partial RunView omitting required fields is schema-rejected.
    partial = {"protocol": "empirica/v2", "request_id": "x",
               "result": {"type": "Allow", "converged": False,
                          "run": {"id": "r", "goal": "g", "status": "active", "contract": ci}}}
    if not schema_rejects(partial, "response"):
        errors.append("NEG partial RunView missing modes/freshness/host: expected schema rejection")

    # (b) canonical additions drift is covered by the registry-digest negatives above.

    # (c) complete RunView required fields missing -> check_run_view_fields rejects.
    no_modes = copy.deepcopy(full_rv)
    no_modes.pop("modes")
    expect(lambda e: check_run_view_fields({"type": "Allow", "run": no_modes}, registry, e, "neg"),
           "run.modes is required", "RunView missing modes")
    no_host = copy.deepcopy(full_rv)
    no_host.pop("host")
    expect(lambda e: check_run_view_fields({"type": "Allow", "run": no_host}, registry, e, "neg"),
           "run.host is required", "RunView missing host")

    # (d) freshness unknown state + path ordering.
    bad_fresh = copy.deepcopy(full_rv)
    bad_fresh["freshness"] = {"changes": [{"path": "b.py", "state": "bad_state"}]}
    expect(lambda e: check_run_view_fields({"type": "Allow", "run": bad_fresh}, registry, e, "neg"),
           "not a canonical freshness state", "freshness unknown state")
    bad_order = copy.deepcopy(full_rv)
    bad_order["freshness"] = {"changes": [
        {"path": "z.py", "state": "present"}, {"path": "a.py", "state": "present"}]}
    expect(lambda e: check_run_view_fields({"type": "Allow", "run": bad_order}, registry, e, "neg"),
           "canonically path-ordered", "freshness path ordering")
    bad_hash = copy.deepcopy(full_rv)
    bad_hash["freshness"] = {"changes": [
        {"path": "a.py", "state": "present", "sha256": "deadbeef"}]}
    expect(lambda e: check_run_view_fields({"type": "Allow", "run": bad_hash}, registry, e, "neg"),
           "only path and state", "freshness with hash")

    # (e) stale reason changes != RunView freshness changes.
    expect(lambda e: check_stale_freshness_match(
        {"type": "Block", "run": {"freshness": {"changes": [{"path": "a.py", "state": "present"}]}},
         "reasons": [{"code": "claim.spike_stale", "parameters": {"changes": [{"path": "b.py", "state": "missing"}]}}]},
        e, "neg"), "!=", "stale reason freshness mismatch")
    # stale reason with no changes params -> schema-rejected via registry params.
    expect(lambda e: check_response_reasons(
        {"type": "Block", "run": full_rv, "reasons": [{"code": "claim.spike_stale", "parameters": {},
         "next_actions": ["spike.regate"], "sections": ["evidence/freshness"]}]},
        registry, e, "neg"), "parameters invalid", "stale reason no changes")
    # stale reason with hashes in changes -> rejected.
    expect(lambda e: check_response_reasons(
        {"type": "Block", "run": full_rv, "reasons": [{"code": "claim.spike_stale",
         "parameters": {"changes": [{"path": "a.py", "state": "present", "sha256": "x"}]},
         "next_actions": ["spike.regate"], "sections": ["evidence/freshness"]}]},
        registry, e, "neg"), "parameters invalid", "stale reason with hash")

    # (f) deferred reason missing digest / ordering.
    expect(lambda e: check_response_reasons(
        {"type": "Block", "run": full_rv, "reasons": [{"code": "freeze.deferred",
         "parameters": {"claim_ids": ["G-later"]},
         "next_actions": ["residual.accept"], "sections": ["freeze"]}]},
        registry, e, "neg"), "parameters invalid", "deferred missing digest")

    # (g) child completed WITH recovery_action -> check_run_view_fields rejects.
    bad_child = copy.deepcopy(full_rv)
    bad_child["children"] = [
        {"child_id": "c", "purpose": "audit", "state": "completed", "recovery_action": "child.retry"}]
    expect(lambda e: check_run_view_fields({"type": "Allow", "run": bad_child}, registry, e, "neg"),
           "completed forbids recovery_action", "completed child with recovery")
    # child adverse WITHOUT recovery_action -> rejected.
    bad_child2 = copy.deepcopy(full_rv)
    bad_child2["children"] = [
        {"child_id": "c", "purpose": "audit", "state": "failed"}]
    expect(lambda e: check_run_view_fields({"type": "Block", "run": bad_child2}, registry, e, "neg"),
           "requires recovery_action", "adverse child without recovery")

    # (h) GetArgument without argument -> check_getargument_exclusivity rejects.
    expect(lambda e: check_getargument_exclusivity(
        {"command": {"type": "GetArgument", "run_id": "r"}}, {"type": "Allow"}, e, "neg"),
        "must include argument", "GetArgument without argument")
    # GetRun with argument -> rejected.
    expect(lambda e: check_getargument_exclusivity(
        {"command": {"type": "GetRun", "run_id": "r"}}, {"type": "Allow", "argument": {}}, e, "neg"),
        "must not include argument", "GetRun with argument")
    # argument on plain Allow (non-converged) is schema-rejected.
    arg_on_allow = {"protocol": "empirica/v2", "request_id": "x",
                    "result": {"type": "Allow", "converged": False, "run": full_rv, "argument": {}}}
    if not schema_rejects(arg_on_allow, "response"):
        errors.append("NEG argument on plain Allow: expected schema rejection")

    # (i) argument root/edge/artifact referential integrity.
    arg_bad_root = {"type": "Allow", "converged": False, "run": full_rv,
                    "argument": {"root_claim_id": "G0", "argument_digest": d64, "goal_digest": d64,
                        "frozen_scope_digest": None, "deferred_scope_digest": d64,
                        "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                            "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
                        "claims": [], "edges": [], "artifacts": [],
                        "audit": {"state": "not_required", "independence": "unverified",
                            "reviewed_argument_digest": None, "reviewed_goal_digest": None,
                            "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": None,
                            "reviewed_claims": []}}}
    expect(lambda e: check_argument_view(arg_bad_root, registry, e, "neg"),
           "root_claim_id", "argument root not in claims")
    arg_unresolved_ev = copy.deepcopy(arg_bad_root)
    arg_unresolved_ev["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "open", "evidence_digest": d64, "active_evidence_ids": ["sha256:" + "z" * 64]}]
    arg_unresolved_ev["argument"]["root_claim_id"] = "G0"
    expect(lambda e: check_argument_view(arg_unresolved_ev, registry, e, "neg"),
           "does not resolve", "active_evidence_id unresolved")
    arg_edge_unresolved = copy.deepcopy(arg_bad_root)
    arg_edge_unresolved["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "open", "evidence_digest": d64, "active_evidence_ids": []}]
    arg_edge_unresolved["argument"]["edges"] = [{"from": "G0", "to": "MISSING", "type": "SupportedBy"}]
    expect(lambda e: check_argument_view(arg_edge_unresolved, registry, e, "neg"),
           "does not resolve", "edge endpoint unresolved")
    arg_dup_claim = copy.deepcopy(arg_bad_root)
    arg_dup_claim["argument"]["claims"] = [
        {"claim_id": "G0", "text": "t", "wording_digest": d64, "kind": "ordinary", "state": "open", "evidence_digest": d64, "active_evidence_ids": []},
        {"claim_id": "G0", "text": "t", "wording_digest": d64, "kind": "ordinary", "state": "open", "evidence_digest": d64, "active_evidence_ids": []}]
    expect(lambda e: check_argument_view(arg_dup_claim, registry, e, "neg"),
           "duplicate claim_id", "argument duplicate claim")

    # (j) canonical digest formatting.
    arg_bad_digest = copy.deepcopy(arg_bad_root)
    arg_bad_digest["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": "not-a-digest",
        "kind": "ordinary", "state": "open", "evidence_digest": d64, "active_evidence_ids": []}]
    expect(lambda e: check_argument_view(arg_bad_digest, registry, e, "neg"),
           "wording/evidence digest", "argument malformed digest")

    # (k) audit cross-field: passed state with missing reviewed digest.
    arg_audit_bad = copy.deepcopy(arg_bad_root)
    arg_audit_bad["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "approved", "gating": True, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_audit_bad["argument"]["audit"] = {"state": "passed", "independence": "decorrelated",
        "reviewed_argument_digest": None, "reviewed_goal_digest": d64,
        "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": d64,
        "reviewed_claims": []}
    expect(lambda e: check_argument_view(arg_audit_bad, registry, e, "neg"),
           "reviewed_argument_digest must be non-null", "audit passed missing reviewed digest")
    # frozen reviewed digest non-null when current frozen is null.
    arg_audit_frozen = copy.deepcopy(arg_bad_root)
    arg_audit_frozen["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "approved", "gating": True, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_audit_frozen["argument"]["audit"] = {"state": "passed", "independence": "decorrelated",
        "reviewed_argument_digest": d64, "reviewed_goal_digest": d64,
        "reviewed_frozen_scope_digest": d64, "reviewed_deferred_scope_digest": d64,
        "reviewed_claims": [{"claim_id": "G0", "evidence_digest": d64}]}
    expect(lambda e: check_argument_view(arg_audit_frozen, registry, e, "neg"),
           "reviewed_frozen_scope_digest must be null", "audit frozen digest when scope null")

    # (l) canonical delimiters mismatch.
    arg_bad_ud = copy.deepcopy(arg_bad_root)
    arg_bad_ud["argument"]["untrusted_delimiters"] = {"open": "<<<WRONG>>>", "close": "<<<END>>>"}
    expect(lambda e: check_argument_view(arg_bad_ud, registry, e, "neg"),
           "untrusted_delimiters", "argument delimiter mismatch")

    # (m) banned internal field leak.
    leaky = {"type": "Allow", "converged": False, "run": copy.deepcopy(full_rv)}
    leaky["run"]["native_child_id"] = "agent-7"
    expect(lambda e: check_banned_fields(
        {"protocol": "empirica/v2", "request_id": "x", "result": leaky}, e, "neg"),
        "banned field", "native_child_id leak")
    leaky2 = {"type": "Allow", "converged": False, "run": copy.deepcopy(full_rv)}
    leaky2["run"]["pass_count"] = 3
    expect(lambda e: check_banned_fields(
        {"protocol": "empirica/v2", "request_id": "x", "result": leaky2}, e, "neg"),
        "banned field", "pass_count leak")

    # (n) artifact kind<->outcome consistency: research artifact with a spike outcome.
    arg_bad_kind = copy.deepcopy(arg_bad_root)
    arg_bad_kind["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "open", "gating": True, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_bad_kind["argument"]["artifacts"] = [{"sequence": 0,
        "artifact_id": "sha256:" + "1" * 64, "claim_id": "G0", "claim_digest": d64,
        "kind": "research", "statement_digest": d64, "active": True, "outcome": "pass",
        "source_kind": "web", "source_ref": "https://example.test/s",
        "citation": "Observed test source."}]
    expect(lambda e: check_argument_view(arg_bad_kind, registry, e, "neg"),
           "research outcome", "research artifact spike outcome")

    # (o) required fixture inventory: a missing required fixture is detected by main().
    # (covered by REQUIRED_V2_FIXTURES set diff in main; not a negative here.)

    # (p) MAJOR-1: passed audit with empty reviewed_claims -> missing gating-claim coverage.
    arg_audit_empty = copy.deepcopy(arg_bad_root)
    arg_audit_empty["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "approved", "gating": True, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_audit_empty["argument"]["audit"] = {"state": "passed", "independence": "decorrelated",
        "reviewed_argument_digest": d64, "reviewed_goal_digest": d64,
        "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": d64,
        "reviewed_claims": []}
    expect(lambda e: check_argument_view(arg_audit_empty, registry, e, "neg"),
           "missing gating-claim coverage", "audit passed empty reviewed_claims")
    # passed audit with stale reviewed evidence digest.
    arg_audit_stale = copy.deepcopy(arg_bad_root)
    arg_audit_stale["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "approved", "gating": True, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_audit_stale["argument"]["audit"] = {"state": "passed", "independence": "decorrelated",
        "reviewed_argument_digest": d64, "reviewed_goal_digest": d64,
        "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": d64,
        "reviewed_claims": [{"claim_id": "G0", "evidence_digest": "sha256:" + "e" * 64}]}
    expect(lambda e: check_argument_view(arg_audit_stale, registry, e, "neg"),
           "!= current", "audit passed stale reviewed evidence")
    # passed audit with extra non-gating reviewed claim.
    arg_audit_extra = copy.deepcopy(arg_bad_root)
    arg_audit_extra["argument"]["claims"] = [
        {"claim_id": "G0", "text": "t", "wording_digest": d64,
         "kind": "ordinary", "state": "approved", "gating": True, "evidence_digest": d64, "active_evidence_ids": []},
        {"claim_id": "G1", "text": "t", "wording_digest": d64,
         "kind": "ordinary", "state": "open", "gating": False, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_audit_extra["argument"]["audit"] = {"state": "passed", "independence": "decorrelated",
        "reviewed_argument_digest": d64, "reviewed_goal_digest": d64,
        "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": d64,
        "reviewed_claims": [{"claim_id": "G0", "evidence_digest": d64},
                            {"claim_id": "G1", "evidence_digest": d64}]}
    expect(lambda e: check_argument_view(arg_audit_extra, registry, e, "neg"),
           "extra non-gating coverage", "audit passed extra non-gating reviewed claim")
    # passed audit with non-null reviewed digest where schema requires null (not_required state).
    arg_audit_nonnull = copy.deepcopy(arg_bad_root)
    arg_audit_nonnull["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "open", "gating": True, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_audit_nonnull["argument"]["audit"] = {"state": "not_required", "independence": "unverified",
        "reviewed_argument_digest": d64, "reviewed_goal_digest": None,
        "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": None,
        "reviewed_claims": []}
    if not schema_rejects({"protocol": "empirica/v2", "request_id": "x",
            "result": {"type": "Allow", "converged": False, "run": full_rv,
                        "argument": arg_audit_nonnull["argument"]}}, "response"):
        errors.append("NEG schema not_required+non-null reviewed digest: expected schema rejection")

    # (q) unique artifact IDs: duplicate artifact_id in distinct artifact objects.
    arg_dup_art = copy.deepcopy(arg_bad_root)
    arg_dup_art["argument"]["claims"] = [{"claim_id": "G0", "text": "t", "wording_digest": d64,
        "kind": "ordinary", "state": "open", "gating": True, "evidence_digest": d64, "active_evidence_ids": []}]
    arg_dup_art["argument"]["artifacts"] = [
        {"sequence": 0, "artifact_id": "sha256:" + "1" * 64, "claim_id": "G0", "claim_digest": d64,
         "kind": "research", "statement_digest": d64, "active": True, "outcome": "supporting",
         "source_kind": "web", "source_ref": "https://example.test/s",
         "citation": "Observed test source."},
        {"sequence": 1, "artifact_id": "sha256:" + "1" * 64, "claim_id": "G0", "claim_digest": d64,
         "kind": "research", "statement_digest": d64, "active": True, "outcome": "supporting",
         "source_kind": "web", "source_ref": "https://example.test/s",
         "citation": "Observed test source."}]
    expect(lambda e: check_argument_view(arg_dup_art, registry, e, "neg"),
           "not unique", "duplicate artifact_id")

    # D2C artifact provenance negatives: one mutation each against a valid base argument
    # (supporting research -> sealed spike_request -> passing spike result, approved and
    # audited). Raw-schema mutations route through schema_rejects (closure/discriminator/
    # format); procedural mutations route through check_argument_view (ordering/correlation).
    # Each expects its own diagnostic substring so a case cannot pass for the wrong reason.
    R1 = "sha256:" + "1" * 64
    R2 = "sha256:" + "5" * 64
    Q1 = "sha256:" + "2" * 64
    P1 = "sha256:" + "3" * 64
    REQ_FILES = ["src/a.py", "tests/a_test.py"]

    def _d2c_arg() -> dict:
        _ed = registry_digest([R1, R2, P1])
        return {"root_claim_id": "G0", "argument_digest": d64, "goal_digest": d64,
            "frozen_scope_digest": None, "deferred_scope_digest": d64,
            "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
            "claims": [{"claim_id": "G0", "text": "t", "wording_digest": d64,
                "kind": "needs-experiment", "state": "approved", "gating": True, "evidence_digest": _ed,
                "active_evidence_ids": [R1, R2, P1]}],
            "edges": [],
            "artifacts": [
                {"sequence": 0, "artifact_id": R1, "claim_id": "G0", "claim_digest": d64,
                 "kind": "research", "statement_digest": d64, "active": True,
                 "outcome": "supporting", "source_kind": "web",
                 "source_ref": "https://example.test/s", "citation": "Observed test source."},
                {"sequence": 1, "artifact_id": R2, "claim_id": "G0", "claim_digest": d64,
                 "kind": "research", "statement_digest": d64, "active": True,
                 "outcome": "supporting", "source_kind": "code",
                 "source_ref": "src/a.py", "citation": "Observed test source."},
                {"sequence": 2, "artifact_id": Q1, "claim_id": "G0", "claim_digest": d64,
                 "kind": "spike_request", "statement_digest": d64,
                 "harness_request_id": "h1", "command": "make test", "command_digest": d64,
                 "dependent_files": list(REQ_FILES), "prerequisite_research_ids": [R1, R2]},
                {"sequence": 3, "artifact_id": P1, "claim_id": "G0", "claim_digest": d64,
                 "kind": "spike", "statement_digest": d64, "active": True, "outcome": "pass",
                 "harness_request_id": "h1", "command": "make test", "command_digest": d64,
                 "prerequisite_research_ids": [R1, R2],
                 "file_bindings": [{"path": "src/a.py", "sha256": d64},
                                   {"path": "tests/a_test.py", "sha256": d64}],
                 "exit_code": 0, "spike_gate": "pass", "supersedes": None}],
            "audit": {"state": "passed", "independence": "decorrelated",
                "reviewed_argument_digest": d64, "reviewed_goal_digest": d64,
                "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": d64,
                "reviewed_claims": [{"claim_id": "G0", "evidence_digest": _ed}]}}

    def _arg_env(argument: dict) -> dict:
        return {"type": "Allow", "converged": False, "run": full_rv, "argument": argument}

    def _full_env(argument: dict) -> dict:
        return {"protocol": "empirica/v2", "request_id": "x",
                "result": {"type": "Allow", "converged": False, "run": full_rv, "argument": argument}}

    # The valid base argument must pass both the raw schema and the procedural check.
    if schema_rejects(_full_env(_d2c_arg()), "response"):
        errors.append("NEG D2C base argument: expected schema-valid but rejected")
    _base_local: list[str] = []
    check_argument_view(_arg_env(_d2c_arg()), registry, _base_local, "neg")
    if _base_local:
        errors.append(f"NEG D2C base argument must pass but produced: {_base_local}")

    # Raw-schema discriminator/closure/format negatives (one mutation each).
    env = _full_env(_d2c_arg())
    del env["result"]["argument"]["artifacts"]
    env["result"]["argument"]["evidence"] = [{"artifact_id": R1, "claim_id": "G0",
        "kind": "research", "statement_digest": d64, "active": True, "outcome": "supporting"}]
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C old evidence field: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][0]["exit_code"] = 0
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C research carries spike field: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][2]["outcome"] = "pass"
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C spike_request carries outcome: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][3]["source_kind"] = "web"
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C spike carries source_kind: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][3]["kind"] = "bogus"
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C unknown artifact kind: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][3]["native_child_id"] = "host-1"
    env["result"]["argument"]["artifacts"][3]["private_capability"] = "SECRET"
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C artifact private/native IDs: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][2]["prerequisite_research_ids"] = []
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C empty prerequisite_research_ids: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][3]["file_bindings"] = []
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C empty file_bindings: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][3]["supersedes"] = "not-a-digest"
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C malformed supersedes: expected schema rejection")
    env = _full_env(_d2c_arg())
    env["result"]["argument"]["artifacts"][2]["dependent_files"] = ["C:\\Users"]
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C dependent_files bad path: expected schema rejection")
    env = _full_env(_d2c_arg())
    del env["result"]["argument"]["artifacts"][3]["sequence"]
    if not schema_rejects(env, "response"):
        errors.append("NEG D2C missing sequence: expected schema rejection")

    # Procedural ordering/correlation negatives (one mutation each).
    arg = _d2c_arg()
    arg["artifacts"][3]["sequence"] = 2
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "not unique", "duplicate sequence")
    arg = _d2c_arg()
    arg["artifacts"][2]["sequence"] = 5
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "not strictly increasing", "nonmonotonic sequence")
    arg = _d2c_arg()
    arg["artifacts"][3]["artifact_id"] = R2
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "not unique", "duplicate artifact_id")
    arg = _d2c_arg()
    arg["artifacts"][3]["harness_request_id"] = "hX"
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "no earlier spike_request", "spike result no earlier request")
    arg = _d2c_arg()
    arg["artifacts"][3]["command"] = "make check"
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "spike result command", "spike result command mismatch")
    arg = _d2c_arg()
    arg["artifacts"][3]["command_digest"] = "sha256:" + "9" * 64
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "spike result command_digest", "spike result command_digest mismatch")
    arg = _d2c_arg()
    arg["artifacts"][3]["prerequisite_research_ids"] = [R2, R1]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "spike result prerequisite_research_ids", "spike result prerequisite order mismatch")
    arg = _d2c_arg()
    arg["artifacts"][3]["file_bindings"][0]["path"] = "other.py"
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "file_binding paths", "file bindings != dependent_files")
    arg = _d2c_arg()
    arg["artifacts"][3]["exit_code"] = 1
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "spike_gate pass requires exit_code 0", "gate pass but non-zero exit")
    arg = _d2c_arg()
    arg["artifacts"][3]["spike_gate"] = "fail"
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "spike_gate fail requires", "gate fail but zero exit")
    arg = _d2c_arg()
    arg["artifacts"][3]["outcome"] = "fail"
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "outcome fail requires spike_gate fail", "outcome fail but gate pass")
    # invariant 4: a second direct result for the same request.
    arg = _d2c_arg()
    arg["artifacts"].append({"sequence": 4, "artifact_id": "sha256:" + "6" * 64,
        "claim_id": "G0", "claim_digest": d64, "kind": "spike", "statement_digest": d64,
        "active": False, "outcome": "fail", "harness_request_id": "h1", "command": "make test",
        "command_digest": d64, "prerequisite_research_ids": [R1, R2],
        "file_bindings": [{"path": "src/a.py", "sha256": d64}, {"path": "tests/a_test.py", "sha256": d64}],
        "exit_code": 1, "spike_gate": "fail", "supersedes": None})
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "more than one direct result", "two results for one request")
    # invariant 6: prerequisite points to a spike_request (not supporting research).
    arg = _d2c_arg()
    arg["artifacts"][3]["prerequisite_research_ids"] = [Q1]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "not an earlier supporting research", "prerequisite points to request")
    # invariant 6: prerequisite points to refuting research.
    arg = _d2c_arg()
    arg["artifacts"][1]["outcome"] = "refuting"
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "not an earlier supporting research", "prerequisite refuting research")
    # invariant 6: prerequisite research for a different claim digest.
    arg = _d2c_arg()
    arg["artifacts"][1]["claim_digest"] = "sha256:" + "7" * 64
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "not an earlier supporting research", "prerequisite wrong claim digest")
    # invariant 8: supersedes names a research artifact (not a spike result).
    arg = _d2c_arg()
    arg["artifacts"][3]["supersedes"] = R1
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "is not an earlier spike result", "supersedes a research artifact")
    # invariant 8: supersedes names a nonexistent spike result.
    arg = _d2c_arg()
    arg["artifacts"][3]["supersedes"] = "sha256:" + "8" * 64
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "is not an earlier spike result", "supersedes nonexistent result")
    # invariant 9: active_evidence_id references a spike_request artifact.
    arg = _d2c_arg()
    arg["claims"][0]["active_evidence_ids"] = [Q1]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "must not reference a spike_request artifact", "active evidence is a spike_request")
    # invariant 9: active_evidence_id references an inactive artifact.
    arg = _d2c_arg()
    arg["artifacts"][3]["active"] = False
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "must reference an active artifact", "active evidence is inactive")

    # D2C invariants 9-13 negatives: one mutation each against a valid base argument.
    # The superseded base carries an old failing spike (inactive, superseded) and a new
    # passing spike (active, supersedes the old) so supersession and active-set semantics
    # are exercised. Each mutation expects its own diagnostic substring.
    P0 = "sha256:" + "4" * 64
    Q2 = "sha256:" + "6" * 64

    def _d2c_super_arg() -> dict:
        _ed = registry_digest([R1, P1])
        return {"root_claim_id": "G0", "argument_digest": d64, "goal_digest": d64,
            "frozen_scope_digest": None, "deferred_scope_digest": d64,
            "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
            "claims": [{"claim_id": "G0", "text": "t", "wording_digest": d64,
                "kind": "needs-experiment", "state": "approved", "gating": True, "evidence_digest": _ed,
                "active_evidence_ids": [R1, P1]}],
            "edges": [],
            "artifacts": [
                {"sequence": 0, "artifact_id": R1, "claim_id": "G0", "claim_digest": d64,
                 "kind": "research", "statement_digest": d64, "active": True,
                 "outcome": "supporting", "source_kind": "web",
                 "source_ref": "https://example.test/s", "citation": "Observed test source."},
                {"sequence": 1, "artifact_id": Q1, "claim_id": "G0", "claim_digest": d64,
                 "kind": "spike_request", "statement_digest": d64,
                 "harness_request_id": "h1", "command": "make test", "command_digest": d64,
                 "dependent_files": list(REQ_FILES), "prerequisite_research_ids": [R1]},
                {"sequence": 2, "artifact_id": P0, "claim_id": "G0", "claim_digest": d64,
                 "kind": "spike", "statement_digest": d64, "active": False, "outcome": "fail",
                 "harness_request_id": "h1", "command": "make test", "command_digest": d64,
                 "prerequisite_research_ids": [R1],
                 "file_bindings": [{"path": "src/a.py", "sha256": d64},
                                   {"path": "tests/a_test.py", "sha256": d64}],
                 "exit_code": 1, "spike_gate": "fail", "supersedes": None},
                {"sequence": 3, "artifact_id": Q2, "claim_id": "G0", "claim_digest": d64,
                 "kind": "spike_request", "statement_digest": d64,
                 "harness_request_id": "h2", "command": "make test", "command_digest": d64,
                 "dependent_files": list(REQ_FILES), "prerequisite_research_ids": [R1]},
                {"sequence": 4, "artifact_id": P1, "claim_id": "G0", "claim_digest": d64,
                 "kind": "spike", "statement_digest": d64, "active": True, "outcome": "pass",
                 "harness_request_id": "h2", "command": "make test", "command_digest": d64,
                 "prerequisite_research_ids": [R1],
                 "file_bindings": [{"path": "src/a.py", "sha256": d64},
                                   {"path": "tests/a_test.py", "sha256": d64}],
                 "exit_code": 0, "spike_gate": "pass", "supersedes": P0}],
            "audit": {"state": "passed", "independence": "decorrelated",
                "reviewed_argument_digest": d64, "reviewed_goal_digest": d64,
                "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": d64,
                "reviewed_claims": [{"claim_id": "G0", "evidence_digest": _ed}]}}

    # The superseded base must pass both the raw schema and the procedural check.
    if schema_rejects(_full_env(_d2c_super_arg()), "response"):
        errors.append("NEG D2C super base argument: expected schema-valid but rejected")
    _super_local: list[str] = []
    check_argument_view(_arg_env(_d2c_super_arg()), registry, _super_local, "neg")
    if _super_local:
        errors.append(f"NEG D2C super base argument must pass but produced: {_super_local}")

    # invariant 12: superseded spike still active.
    arg = _d2c_super_arg()
    arg["artifacts"][2]["active"] = True
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "superseded but still active", "superseded spike still active")
    # invariant 12: more than one active spike per claim digest.
    arg = _d2c_arg()
    arg["artifacts"].append({"sequence": 4, "artifact_id": "sha256:" + "7" * 64,
        "claim_id": "G0", "claim_digest": d64, "kind": "spike", "statement_digest": d64,
        "active": True, "outcome": "pass", "harness_request_id": "h2", "command": "make test",
        "command_digest": d64, "prerequisite_research_ids": [R1, R2],
        "file_bindings": [{"path": "src/a.py", "sha256": d64}, {"path": "tests/a_test.py", "sha256": d64}],
        "exit_code": 0, "spike_gate": "pass", "supersedes": None})
    arg["artifacts"].insert(2, {"sequence": 2, "artifact_id": "sha256:" + "8" * 64,
        "claim_id": "G0", "claim_digest": d64, "kind": "spike_request", "statement_digest": d64,
        "harness_request_id": "h2", "command": "make test", "command_digest": d64,
        "dependent_files": list(REQ_FILES), "prerequisite_research_ids": [R1, R2]})
    # re-sequence to keep strictly increasing order
    for idx, a in enumerate(arg["artifacts"]):
        a["sequence"] = idx
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "more than one active spike", "multiple active spike heads")
    # invariant 11: active spike with inactive prerequisite.
    arg = _d2c_arg()
    arg["artifacts"][0]["active"] = False
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "not a currently active supporting research", "active spike inactive prerequisite")
    # invariant 9: omitted active evidence ID (subset).
    arg = _d2c_arg()
    arg["claims"][0]["active_evidence_ids"] = [R1, P1]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "complete active set", "omitted active evidence ID")
    # invariant 9: extra active evidence ID.
    arg = _d2c_arg()
    arg["claims"][0]["active_evidence_ids"] = [R1, R2, P1, Q1]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "complete active set", "extra active evidence ID")
    # invariant 10: wrong canonical evidence digest.
    arg = _d2c_arg()
    arg["claims"][0]["evidence_digest"] = d64
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "registry_digest", "wrong evidence digest")
    # invariant 13: approved with active refuting research.
    arg = _d2c_arg()
    arg["artifacts"].append({"sequence": 4, "artifact_id": "sha256:" + "9" * 64,
        "claim_id": "G0", "claim_digest": d64, "kind": "research", "statement_digest": d64,
        "active": True, "outcome": "refuting", "source_kind": "web",
        "source_ref": "https://example.test/refutes", "citation": "Observed refutation."})
    arg["claims"][0]["active_evidence_ids"] = [R1, R2, "sha256:" + "9" * 64, P1]
    arg["claims"][0]["evidence_digest"] = registry_digest(arg["claims"][0]["active_evidence_ids"])
    arg["audit"]["reviewed_claims"][0]["evidence_digest"] = arg["claims"][0]["evidence_digest"]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "approved but active refuting research", "approved with refuting evidence")
    # invariant 13: approved with active failing spike.
    arg = _d2c_arg()
    arg["artifacts"].append({"sequence": 4, "artifact_id": "sha256:" + "a" * 64,
        "claim_id": "G0", "claim_digest": d64, "kind": "spike", "statement_digest": d64,
        "active": True, "outcome": "fail", "harness_request_id": "h2", "command": "make test",
        "command_digest": d64, "prerequisite_research_ids": [R1, R2],
        "file_bindings": [{"path": "src/a.py", "sha256": d64}, {"path": "tests/a_test.py", "sha256": d64}],
        "exit_code": 1, "spike_gate": "fail", "supersedes": None})
    arg["artifacts"].insert(2, {"sequence": 2, "artifact_id": "sha256:" + "b" * 64,
        "claim_id": "G0", "claim_digest": d64, "kind": "spike_request", "statement_digest": d64,
        "harness_request_id": "h2", "command": "make test", "command_digest": d64,
        "dependent_files": list(REQ_FILES), "prerequisite_research_ids": [R1, R2]})
    for idx, a in enumerate(arg["artifacts"]):
        a["sequence"] = idx
    arg["claims"][0]["active_evidence_ids"] = [R1, R2, "sha256:" + "a" * 64, P1]
    arg["claims"][0]["evidence_digest"] = registry_digest(arg["claims"][0]["active_evidence_ids"])
    arg["audit"]["reviewed_claims"][0]["evidence_digest"] = arg["claims"][0]["evidence_digest"]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "approved but active failing spike", "approved with failing evidence")
    # invariant 13: approved with stale spike binding (RunView freshness change).
    arg = _d2c_arg()
    _stale_env = {"type": "Allow", "converged": False,
        "run": {**full_rv, "freshness": {"changes": [
            {"path": "src/a.py", "state": "present"}]}},
        "argument": arg}
    expect(lambda e: check_argument_view(_stale_env, registry, e, "neg"),
           "approved but RunView freshness", "approved with stale binding")

    # D2C invariant 13 conditional approval negatives and positives: claim kind drives the
    # requirement. ordinary approved requires supporting research only; needs-experiment
    # additionally requires a passing spike; needs-decision can never be approved.

    # ordinary approved research-only POSITIVE (must pass).
    _ord_ed = registry_digest([R1])
    arg_ord = {"root_claim_id": "G0", "argument_digest": d64, "goal_digest": d64,
        "frozen_scope_digest": None, "deferred_scope_digest": d64,
        "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
            "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
        "claims": [{"claim_id": "G0", "text": "t", "wording_digest": d64,
            "kind": "ordinary", "state": "approved", "gating": True,
            "evidence_digest": _ord_ed, "active_evidence_ids": [R1]}],
        "edges": [],
        "artifacts": [
            {"sequence": 0, "artifact_id": R1, "claim_id": "G0", "claim_digest": d64,
             "kind": "research", "statement_digest": d64, "active": True,
             "outcome": "supporting", "source_kind": "web",
             "source_ref": "https://example.test/s", "citation": "Observed test source."}],
        "audit": {"state": "not_required", "independence": "unverified",
            "reviewed_argument_digest": None, "reviewed_goal_digest": None,
            "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": None,
            "reviewed_claims": []}}
    _ord_local: list[str] = []
    check_argument_view(_arg_env(arg_ord), registry, _ord_local, "neg")
    if _ord_local:
        errors.append(f"NEG ordinary approved research-only must pass but produced: {_ord_local}")

    # needs-experiment approved without spike NEGATIVE.
    arg = _d2c_arg()
    arg["claims"][0]["kind"] = "needs-experiment"
    arg["claims"][0]["active_evidence_ids"] = [R1, R2]
    arg["claims"][0]["evidence_digest"] = registry_digest([R1, R2])
    arg["audit"]["reviewed_claims"][0]["evidence_digest"] = arg["claims"][0]["evidence_digest"]
    arg["artifacts"] = [a for a in arg["artifacts"] if a["kind"] != "spike"]
    # Remove the spike_request too since there's no spike result
    arg["artifacts"] = [a for a in arg["artifacts"] if a["kind"] != "spike_request"]
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "needs-experiment approved but no active passing spike",
           "needs-experiment approved without spike")

    # needs-decision approved NEGATIVE.
    arg = _d2c_arg()
    arg["claims"][0]["kind"] = "needs-decision"
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "needs-decision claim cannot project approved",
           "needs-decision approved")

    # identical-digest distinct-claim leakage NEGATIVE: two claims share one wording
    # digest; each must have its own active set and must not share artifacts.
    R_X = "sha256:" + "c" * 64
    _ed_g0 = registry_digest([R1, P1])
    _ed_g1 = registry_digest([R_X])
    arg_leak = {"root_claim_id": "G0", "argument_digest": d64, "goal_digest": d64,
        "frozen_scope_digest": None, "deferred_scope_digest": d64,
        "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
            "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
        "claims": [
            {"claim_id": "G0", "text": "same", "wording_digest": d64,
             "kind": "needs-experiment", "state": "approved", "gating": True,
             "evidence_digest": _ed_g0, "active_evidence_ids": [R1, P1]},
            {"claim_id": "G1", "text": "same", "wording_digest": d64,
             "kind": "ordinary", "state": "open", "gating": False,
             "evidence_digest": _ed_g1, "active_evidence_ids": [R_X]}],
        "edges": [],
        "artifacts": [
            {"sequence": 0, "artifact_id": R1, "claim_id": "G0", "claim_digest": d64,
             "kind": "research", "statement_digest": d64, "active": True,
             "outcome": "supporting", "source_kind": "web",
             "source_ref": "https://example.test/s", "citation": "Observed test source."},
            {"sequence": 1, "artifact_id": Q1, "claim_id": "G0", "claim_digest": d64,
             "kind": "spike_request", "statement_digest": d64,
             "harness_request_id": "h1", "command": "make test", "command_digest": d64,
             "dependent_files": list(REQ_FILES), "prerequisite_research_ids": [R1]},
            {"sequence": 2, "artifact_id": P1, "claim_id": "G0", "claim_digest": d64,
             "kind": "spike", "statement_digest": d64, "active": True, "outcome": "pass",
             "harness_request_id": "h1", "command": "make test", "command_digest": d64,
             "prerequisite_research_ids": [R1],
             "file_bindings": [{"path": "src/a.py", "sha256": d64},
                               {"path": "tests/a_test.py", "sha256": d64}],
             "exit_code": 0, "spike_gate": "pass", "supersedes": None},
            {"sequence": 3, "artifact_id": R_X, "claim_id": "G1", "claim_digest": d64,
             "kind": "research", "statement_digest": d64, "active": True,
             "outcome": "supporting", "source_kind": "code",
             "source_ref": "src/b.py", "citation": "Observed test source."}],
        "audit": {"state": "not_required", "independence": "unverified",
            "reviewed_argument_digest": None, "reviewed_goal_digest": None,
            "reviewed_frozen_scope_digest": None, "reviewed_deferred_scope_digest": None,
            "reviewed_claims": []}}
    # The valid two-claim/same-digest base must pass.
    _leak_local: list[str] = []
    check_argument_view(_arg_env(arg_leak), registry, _leak_local, "neg")
    if _leak_local:
        errors.append(f"NEG two-claim same-digest base must pass but produced: {_leak_local}")
    # One mutation: G1's active_evidence_ids references G0's research (cross-claim leak).
    arg = {**arg_leak, "claims": [dict(c) for c in arg_leak["claims"]]}
    arg["claims"][1]["active_evidence_ids"] = [R1]
    arg["claims"][1]["evidence_digest"] = registry_digest([R1])
    expect(lambda e: check_argument_view(_arg_env(arg), registry, e, "neg"),
           "belongs to claim_id", "cross-claim active evidence leak")

    # (r) strict POSIX path negatives (raw schema rejects Windows/UNC/backslash/dot/dotdot).
    for bad_path in ["C:\\\\Users\\secret", "\\\\server\\share", "a/../b", "a//b", "a\\b", "a/./b"]:
        bad_fresh_path = {"protocol": "empirica/v2", "request_id": "x",
            "result": {"type": "Block", "run": full_rv,
                "reasons": [{"code": "claim.spike_stale", "parameters": {"changes": [
                    {"path": bad_path, "state": "present"}]},
                    "next_actions": ["spike.regate"], "sections": ["evidence/freshness"]}]}}
        if not schema_rejects(bad_fresh_path, "response"):
            errors.append(f"NEG schema spike_stale bad path {bad_path!r}: expected schema rejection")
    # procedural freshness rejects the same bad paths.
    for bad_path in ["C:\\\\Users", "a/../b", "a//b", "a\\b"]:
        bad_rv = copy.deepcopy(full_rv)
        bad_rv["freshness"] = {"changes": [{"path": bad_path, "state": "present"}]}
        expect(lambda e: check_run_view_fields({"type": "Allow", "run": bad_rv}, registry, e, "neg"),
               "must be normalized relative posix", f"freshness bad path {bad_path!r}")

    # (s) raw-schema unknown reason code rejects without the validator.
    unknown_reason = {"protocol": "empirica/v2", "request_id": "x",
        "result": {"type": "Block", "run": full_rv,
            "reasons": [{"code": "totally.unknown", "parameters": {},
                "next_actions": ["run.start_fresh"], "sections": ["run/lifecycle"]}]}}
    if not schema_rejects(unknown_reason, "response"):
        errors.append("NEG schema unknown reason code: expected schema rejection")
    # raw-schema malformed residual parameters rejects without the validator.
    bad_residual_rv = copy.deepcopy(full_rv)
    bad_residual_rv["residuals"] = [{"code": "budget.exhausted", "parameters": {}}]
    bad_residual = {"protocol": "empirica/v2", "request_id": "x",
        "result": {"type": "Block", "run": bad_residual_rv,
            "reasons": [{"code": "run.no_active", "parameters": {},
                "next_actions": ["run.start_fresh"], "sections": ["run/lifecycle"]}]}}
    if not schema_rejects(bad_residual, "response"):
        errors.append("NEG schema malformed residual params: expected schema rejection")
    # raw-schema GetContract full private field rejects without the validator.
    # Carries a well-formed digest so the only mutation is the private/unknown field in `full`.
    full_private = {"protocol": "empirica/v2", "request_id": "x",
        "result": {"type": "Allow", "contract_result": {"target": "full", "digest": d64,
            "full": {"private_capability": "SECRET", "native_child_id": "host-1"}}}}
    if not schema_rejects(full_private, "response"):
        errors.append("NEG schema GetContract full private field: expected schema rejection")

    # (t) reasonPayload parameter schema one-sided drift negatives: each mutates
    # exactly one facet of a single response-schema $def and asserts the parameter
    # mirror detects the drift against the unchanged registry.
    bad_enum = copy.deepcopy(res_schema)
    bad_enum["$defs"]["budgetExhaustedParams"]["properties"]["resource"]["enum"].append("extra")
    expect(lambda e: check_reason_params_mirror(bad_enum, registry, e, "neg"),
           "reasonPayload", "params enum drift")
    bad_required = copy.deepcopy(res_schema)
    bad_required["$defs"]["budgetExhaustedParams"]["required"] = []
    expect(lambda e: check_reason_params_mirror(bad_required, registry, e, "neg"),
           "reasonPayload", "params required drift")
    bad_nested = copy.deepcopy(res_schema)
    bad_nested["$defs"]["relativePosixPath"]["pattern"] = "WRONG"
    expect(lambda e: check_reason_params_mirror(bad_nested, registry, e, "neg"),
           "reasonPayload", "params nested drift")
    bad_closure = copy.deepcopy(res_schema)
    bad_closure["$defs"]["budgetExhaustedParams"]["additionalProperties"] = True
    expect(lambda e: check_reason_params_mirror(bad_closure, registry, e, "neg"),
           "reasonPayload", "params closure drift")

    # D2D: trusted-ingress payload raw-schema + procedural negatives (one mutation each).
    # Each raw-schema mutation routes through schema_rejects (closure/discriminator/
    # format/enum/null-relation); each procedural mutation routes through
    # check_trusted_payload against a correlated request/response bundle. Each expects
    # its own diagnostic substring so a case cannot pass for the wrong reason.
    R1 = "sha256:" + "1" * 64
    P1 = "sha256:" + "3" * 64
    CMD = "sha256:" + "d" * 64
    FSHA = "sha256:" + "9" * 64
    EV = "sha256:" + "a" * 64
    ARG = "sha256:" + "0" * 64
    GOAL = "sha256:" + "b" * 64
    WORD = "sha256:" + "c" * 64
    STMT = "sha256:" + "e" * 64
    FP = "sha256:" + "4" * 64
    RD = "sha256:" + "5" * 64
    Q1 = "sha256:" + "2" * 64
    REQ_FILES = ["src/a.py", "tests/a_test.py"]

    def _req_cmd(action: dict) -> dict:
        return {"protocol": "empirica/v2", "request_id": "x",
                "command": {"type": "ObserveAction", "run_id": "run-fx",
                             "action": action}}

    def _trusted(kind: str, child_id, payload) -> dict:
        a = {"kind": kind, "trusted": {"capability_ref": "<redacted>",
                                         "boundary": "host"}}
        if child_id is not None:
            a["child_id"] = child_id
        a["payload"] = payload
        return a

    def _child_event_payload(state="completed", native_id="agent-7",
                             fingerprint=FP, result_digest=RD) -> dict:
        return {"state": state, "native_id": native_id, "fingerprint": fingerprint,
                "result_digest": result_digest}

    def _attribution_payload(subject_kind="covered_actor", subject_id="author-1",
                             child_id=None, provider_id="p-a", model_id="m-1",
                             observed_by="host", covered=None) -> dict:
        if covered is None:
            covered = [R1, P1]
        return {"subject_kind": subject_kind, "subject_id": subject_id,
                "child_id": child_id, "provider_id": provider_id,
                "model_id": model_id, "observed_by": observed_by,
                "covered_artifact_ids": covered}

    def _audit_verdict_payload(frozen=None, scope_review=None,
                               reviewed_claims=None) -> dict:
        if reviewed_claims is None:
            reviewed_claims = [{"claim_id": "G0", "evidence_digest": EV}]
        return {"verdict": "pass", "findings": [], "argument_digest": ARG,
                "goal_digest": GOAL, "frozen_scope_digest": frozen,
                "deferred_scope_digest": ARG,
                "reviewed_claims": reviewed_claims, "scope_review": scope_review}

    def _evidence_leaf_payload(exit_code=0, result_digest=P1,
                                command_digest=CMD) -> dict:
        return {"harness_request_id": "h1", "command_digest": command_digest,
                "prerequisite_research_ids": [R1],
                "file_bindings": [{"path": "src/a.py", "sha256": FSHA},
                                   {"path": "tests/a_test.py", "sha256": FSHA}],
                "exit_code": exit_code, "result_digest": result_digest}

    # --- child_event raw-schema negatives (one mutation each) ---
    # unknown state (reserved excluded).
    if not schema_rejects(_req_cmd(_trusted("child_event", "c1",
            {"state": "reserved", "native_id": "n", "fingerprint": FP,
             "result_digest": None})), "request"):
        errors.append("NEG D2D child_event reserved state: expected schema rejection")
    # missing fingerprint.
    p = _child_event_payload()
    del p["fingerprint"]
    if not schema_rejects(_req_cmd(_trusted("child_event", "c1", p)), "request"):
        errors.append("NEG D2D child_event missing fingerprint: expected schema rejection")
    # extra field.
    p = _child_event_payload()
    p["independence"] = "decorrelated"
    if not schema_rejects(_req_cmd(_trusted("child_event", "c1", p)), "request"):
        errors.append("NEG D2D child_event extra field: expected schema rejection")
    # nested event alias.
    if not schema_rejects(_req_cmd(_trusted("child_event", "c1",
            {"event": {"state": "completed", "native_id": "n"},
             "fingerprint": FP, "result_digest": RD})), "request"):
        errors.append("NEG D2D child_event nested event alias: expected schema rejection")
    # launch_rejected with non-null native_id.
    if not schema_rejects(_req_cmd(_trusted("child_event", "c1",
            {"state": "launch_rejected", "native_id": "n", "fingerprint": FP,
             "result_digest": None})), "request"):
        errors.append("NEG D2D child_event launch_rejected non-null native_id: expected schema rejection")
    # completed with null result_digest.
    if not schema_rejects(_req_cmd(_trusted("child_event", "c1",
            {"state": "completed", "native_id": "n", "fingerprint": FP,
             "result_digest": None})), "request"):
        errors.append("NEG D2D child_event completed null result_digest: expected schema rejection")
    # pending with non-null result_digest.
    if not schema_rejects(_req_cmd(_trusted("child_event", "c1",
            {"state": "pending", "native_id": "n", "fingerprint": FP,
             "result_digest": RD})), "request"):
        errors.append("NEG D2D child_event pending non-null result_digest: expected schema rejection")

    # --- attribution raw-schema negatives (one mutation each) ---
    # unknown subject_kind.
    p = _attribution_payload(subject_kind="author")
    if not schema_rejects(_req_cmd(_trusted("attribution", None, p)), "request"):
        errors.append("NEG D2D attribution unknown subject_kind: expected schema rejection")
    # forbidden independence field.
    p = _attribution_payload()
    p["independence"] = "decorrelated"
    if not schema_rejects(_req_cmd(_trusted("attribution", None, p)), "request"):
        errors.append("NEG D2D attribution independence field: expected schema rejection")
    # auditor with non-null covered_artifact_ids.
    p = _attribution_payload(subject_kind="auditor", child_id="c1", covered=[R1])
    if not schema_rejects(_req_cmd(_trusted("attribution", None, p)), "request"):
        errors.append("NEG D2D attribution auditor with covered ids: expected schema rejection")
    # covered_actor with non-null child_id.
    p = _attribution_payload(subject_kind="covered_actor", child_id="c1")
    if not schema_rejects(_req_cmd(_trusted("attribution", None, p)), "request"):
        errors.append("NEG D2D attribution covered_actor with child_id: expected schema rejection")
    # provider null but model non-null.
    p = _attribution_payload(provider_id=None, model_id="m-1")
    if not schema_rejects(_req_cmd(_trusted("attribution", None, p)), "request"):
        errors.append("NEG D2D attribution provider null model non-null: expected schema rejection")
    # private/native leak via native_id.
    p = _attribution_payload()
    p["native_id"] = "agent-7"
    if not schema_rejects(_req_cmd(_trusted("attribution", None, p)), "request"):
        errors.append("NEG D2D attribution native_id leak: expected schema rejection")

    # --- audit_verdict raw-schema negatives (one mutation each) ---
    # forbidden independence field.
    p = _audit_verdict_payload()
    p["independence"] = "decorrelated"
    if not schema_rejects(_req_cmd(_trusted("audit_verdict", "c1", p)), "request"):
        errors.append("NEG D2D audit_verdict independence field: expected schema rejection")
    # unknown verdict.
    p = _audit_verdict_payload()
    p["verdict"] = "unknown"
    if not schema_rejects(_req_cmd(_trusted("audit_verdict", "c1", p)), "request"):
        errors.append("NEG D2D audit_verdict unknown verdict: expected schema rejection")
    # frozen non-null but scope_review null.
    if not schema_rejects(_req_cmd(_trusted("audit_verdict", "c1",
            _audit_verdict_payload(frozen=ARG, scope_review=None))), "request"):
        errors.append("NEG D2D audit_verdict frozen non-null scope_review null: expected schema rejection")
    # frozen null but scope_review non-null.
    if not schema_rejects(_req_cmd(_trusted("audit_verdict", "c1",
            _audit_verdict_payload(frozen=None, scope_review="pass"))), "request"):
        errors.append("NEG D2D audit_verdict frozen null scope_review non-null: expected schema rejection")
    # forbidden provider_id field.
    p = _audit_verdict_payload()
    p["provider_id"] = "p-a"
    if not schema_rejects(_req_cmd(_trusted("audit_verdict", "c1", p)), "request"):
        errors.append("NEG D2D audit_verdict provider_id field: expected schema rejection")
    # forbidden gate field.
    p = _audit_verdict_payload()
    p["gate"] = "pass"
    if not schema_rejects(_req_cmd(_trusted("audit_verdict", "c1", p)), "request"):
        errors.append("NEG D2D audit_verdict gate field: expected schema rejection")
    # deferred reviewed claim (extra claim not in gating scope) -- procedural via bundle below;
    # here raw-schema: reviewed_claims item with extra field.
    p = _audit_verdict_payload()
    p["reviewed_claims"] = [{"claim_id": "G0", "evidence_digest": EV,
                             "deferred": True}]
    if not schema_rejects(_req_cmd(_trusted("audit_verdict", "c1", p)), "request"):
        errors.append("NEG D2D audit_verdict reviewed_claim extra field: expected schema rejection")

    # --- evidence_leaf raw-schema negatives (one mutation each) ---
    # forbidden gate field (gate is derived, not supplied).
    p = _evidence_leaf_payload()
    p["gate"] = "pass"
    if not schema_rejects(_req_cmd(_trusted("evidence_leaf", None, p)), "request"):
        errors.append("NEG D2D evidence_leaf gate field: expected schema rejection")
    # missing result_digest.
    p = _evidence_leaf_payload()
    del p["result_digest"]
    if not schema_rejects(_req_cmd(_trusted("evidence_leaf", None, p)), "request"):
        errors.append("NEG D2D evidence_leaf missing result_digest: expected schema rejection")
    # empty prerequisite_research_ids.
    p = _evidence_leaf_payload()
    p["prerequisite_research_ids"] = []
    if not schema_rejects(_req_cmd(_trusted("evidence_leaf", None, p)), "request"):
        errors.append("NEG D2D evidence_leaf empty prerequisites: expected schema rejection")
    # empty file_bindings.
    p = _evidence_leaf_payload()
    p["file_bindings"] = []
    if not schema_rejects(_req_cmd(_trusted("evidence_leaf", None, p)), "request"):
        errors.append("NEG D2D evidence_leaf empty file_bindings: expected schema rejection")
    # extra claim_id field (identity comes from sealed request).
    p = _evidence_leaf_payload()
    p["claim_id"] = "G0"
    if not schema_rejects(_req_cmd(_trusted("evidence_leaf", None, p)), "request"):
        errors.append("NEG D2D evidence_leaf claim_id field: expected schema rejection")
    # missing payload entirely.
    a = {"kind": "evidence_leaf",
         "trusted": {"capability_ref": "<redacted>", "boundary": "host"}}
    if not schema_rejects(_req_cmd(a), "request"):
        errors.append("NEG D2D evidence_leaf missing payload: expected schema rejection")

    # --- D2D procedural correlation negatives (one mutation each) ---
    # Build a valid correlated request/response bundle and mutate exactly one fact.
    def _run_view(status="active", sections=None, children=None) -> dict:
        return {"id": "run-fx", "goal": "g", "status": status,
                "modes": {"multi_provider": False, "cli_exec": False},
                "contract": {"id": "empirica/public", "version": "2.0.0",
                              "digest": d64, "relevant_sections": sections or ["audit"]},
                "obligations": {"active": [], "deferred": []}, "residuals": [],
                "freshness": {"changes": []}, "children": children or [],
                "host": {"profile_id": "claude-code@2.1.270", "tier": "foreground_only",
                          "missing_capabilities": []}}

    def _artifacts() -> list:
        return [
            {"sequence": 0, "artifact_id": R1, "claim_id": "G0", "claim_digest": WORD,
             "kind": "research", "statement_digest": STMT, "active": True,
             "outcome": "supporting", "source_kind": "web", "source_ref": "src/a.py",
             "citation": "Observed test source."},
            {"sequence": 1, "artifact_id": Q1, "claim_id": "G0", "claim_digest": WORD,
             "kind": "spike_request", "statement_digest": STMT,
             "harness_request_id": "h1", "command": "make test", "command_digest": CMD,
             "dependent_files": list(REQ_FILES), "prerequisite_research_ids": [R1]},
            {"sequence": 2, "artifact_id": P1, "claim_id": "G0", "claim_digest": WORD,
             "kind": "spike", "statement_digest": STMT, "active": True,
             "outcome": "pass", "harness_request_id": "h1", "command": "make test",
             "command_digest": CMD, "prerequisite_research_ids": [R1],
             "file_bindings": [{"path": "src/a.py", "sha256": FSHA},
                               {"path": "tests/a_test.py", "sha256": FSHA}],
             "exit_code": 0, "spike_gate": "pass", "supersedes": None}]

    def _arg_view(frozen=None, audit_state="passed") -> dict:
        covered = audit_state in ("passed", "failed", "stale")
        return {"root_claim_id": "G0", "argument_digest": ARG, "goal_digest": GOAL,
                "frozen_scope_digest": frozen, "deferred_scope_digest": ARG,
                "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                    "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
                "claims": [{"claim_id": "G0", "text": "t", "wording_digest": WORD,
                    "state": "approved", "gating": True, "evidence_digest": EV,
                    "active_evidence_ids": [R1, P1], "kind": "needs-experiment"}],
                "edges": [], "artifacts": _artifacts(),
                "audit": {"state": audit_state, "independence": "decorrelated",
                    "reviewed_argument_digest": ARG if covered else None,
                    "reviewed_goal_digest": GOAL if covered else None,
                    "reviewed_frozen_scope_digest": (frozen if (covered and frozen) else None),
                    "reviewed_deferred_scope_digest": ARG if covered else None,
                    "reviewed_claims": [{"claim_id": "G0", "evidence_digest": EV}] if covered else []}}

    # child_event wrong child (no matching run child).
    req = _req_cmd(_trusted("child_event", "ch-missing", _child_event_payload()))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}])},
        registry, e, "neg"), "does not match a run child", "child_event wrong child")
    # child_event state mismatch vs run child.
    req = _req_cmd(_trusted("child_event", "c1", _child_event_payload(state="failed")))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}])},
        registry, e, "neg"), "!= run child state", "child_event state mismatch")
    # attribution covered_actor wrong artifact (not in argument).
    req = _req_cmd(_trusted("attribution", None, _attribution_payload(covered=["sha256:" + "z" * 64])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "does not resolve", "attribution wrong artifact")
    # attribution covered_actor inactive artifact.
    req = _req_cmd(_trusted("attribution", None, _attribution_payload(covered=[Q1])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "not an active artifact", "attribution inactive artifact")
    # attribution covered_actor non-gating claim.
    req = _req_cmd(_trusted("attribution", None, _attribution_payload(covered=[R1])))
    # Make R1 a spike_request-kind artifact to fail the gating check indirectly is complex;
    # instead mutate claim G0 to non-gating.
    arg = _arg_view()
    arg["claims"][0]["gating"] = False
    arg["claims"][0]["state"] = "open"
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": arg}, registry, e, "neg"),
        "does not belong to an approved gating claim", "attribution non-gating artifact")
    # attribution auditor wrong child.
    req = _req_cmd(_trusted("attribution", None,
        _attribution_payload(subject_kind="auditor", child_id="ch-missing", covered=[])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "pending"}])},
        registry, e, "neg"), "does not match an admitted audit child", "attribution auditor wrong child")
    # attribution auditor wrong state (audit child completed, not pending) — the
    # verdict, not the attribution, binds the pending child's completion.
    req = _req_cmd(_trusted("attribution", None,
        _attribution_payload(subject_kind="auditor", child_id="c1", covered=[])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}])},
        registry, e, "neg"), "is not pending", "attribution auditor wrong state")
    # audit_verdict wrong argument_digest (valid completed audit child isolates the mutation).
    req = _req_cmd(_trusted("audit_verdict", "c1",
        _audit_verdict_payload(frozen=None, scope_review=None)))
    arg = _arg_view()
    arg["argument_digest"] = "sha256:" + "9" * 64
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}]),
        "argument": arg}, registry, e, "neg"),
        "argument_digest", "audit_verdict wrong argument_digest")
    # audit_verdict wrong reviewed coverage (extra non-gating claim).
    req = _req_cmd(_trusted("audit_verdict", "c1",
        _audit_verdict_payload(reviewed_claims=[
            {"claim_id": "G0", "evidence_digest": EV},
            {"claim_id": "G1", "evidence_digest": EV}])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}]),
        "argument": _arg_view()}, registry, e, "neg"),
        "!= gating coverage", "audit_verdict extra reviewed claim")
    # audit_verdict stale reviewed evidence_digest.
    req = _req_cmd(_trusted("audit_verdict", "c1",
        _audit_verdict_payload(reviewed_claims=[
            {"claim_id": "G0", "evidence_digest": "sha256:" + "e" * 64}])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}]),
        "argument": _arg_view()}, registry, e, "neg"),
        "!= current", "audit_verdict stale evidence digest")
    # audit_verdict wrong child (child_id not in run) — postcondition negative.
    req = _req_cmd(_trusted("audit_verdict", "ch-missing",
        _audit_verdict_payload(frozen=None, scope_review=None)))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}]),
        "argument": _arg_view()}, registry, e, "neg"),
        "does not match a run child", "audit_verdict wrong child")
    # audit_verdict wrong purpose (matching child is not audit-purpose).
    req = _req_cmd(_trusted("audit_verdict", "c1",
        _audit_verdict_payload(frozen=None, scope_review=None)))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "research", "state": "completed"}]),
        "argument": _arg_view()}, registry, e, "neg"),
        "not audit-purpose", "audit_verdict wrong purpose")
    # audit_verdict non-completed child (audit child still pending) — D4 must not
    # admit a verdict before the host-observed completion.
    req = _req_cmd(_trusted("audit_verdict", "c1",
        _audit_verdict_payload(frozen=None, scope_review=None)))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "pending"}]),
        "argument": _arg_view()}, registry, e, "neg"),
        "is not completed", "audit_verdict non-completed child")
    # audit_verdict duplicate claim_id (lossy-dict bypass: must reject before map).
    req = _req_cmd(_trusted("audit_verdict", "c1",
        _audit_verdict_payload(reviewed_claims=[
            {"claim_id": "G0", "evidence_digest": EV},
            {"claim_id": "G0", "evidence_digest": EV}])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}]),
        "argument": _arg_view()}, registry, e, "neg"),
        "duplicate claim_id", "audit_verdict duplicate reviewed claim_id")
    # audit_verdict reordered coverage (same set, wrong order) — ordered-list bypass.
    _EV0 = "sha256:" + "1" * 64
    _EV1 = "sha256:" + "2" * 64
    _arg2 = _arg_view()
    _arg2["claims"] = [
        {"claim_id": "G0", "text": "t", "wording_digest": WORD, "state": "approved",
         "gating": True, "evidence_digest": _EV0, "active_evidence_ids": [R1, P1],
         "kind": "needs-experiment"},
        {"claim_id": "G1", "text": "t", "wording_digest": "sha256:" + "3" * 64,
         "state": "approved", "gating": True, "evidence_digest": _EV1,
         "active_evidence_ids": [], "kind": "ordinary"}]
    req = _req_cmd(_trusted("audit_verdict", "c1",
        _audit_verdict_payload(reviewed_claims=[
            {"claim_id": "G1", "evidence_digest": _EV1},
            {"claim_id": "G0", "evidence_digest": _EV0}])))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(children=[
        {"child_id": "c1", "purpose": "audit", "state": "completed"}]),
        "argument": _arg2}, registry, e, "neg"),
        "gating coverage", "audit_verdict reordered coverage")
    # evidence_leaf wrong command_digest vs sealed request.
    req = _req_cmd(_trusted("evidence_leaf", None,
        _evidence_leaf_payload(command_digest="sha256:" + "z" * 64)))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "command_digest", "evidence_leaf wrong command_digest")
    # evidence_leaf wrong result_digest vs spike result.
    req = _req_cmd(_trusted("evidence_leaf", None,
        _evidence_leaf_payload(result_digest="sha256:" + "z" * 64)))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "result_digest", "evidence_leaf wrong result_digest")
    # evidence_leaf wrong exit_code vs spike result.
    req = _req_cmd(_trusted("evidence_leaf", None, _evidence_leaf_payload(exit_code=1)))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "exit_code", "evidence_leaf wrong exit_code")
    # evidence_leaf file_bindings != spike result bindings (wrong path).
    p = _evidence_leaf_payload()
    p["file_bindings"] = [{"path": "other.py", "sha256": FSHA}]
    req = _req_cmd(_trusted("evidence_leaf", None, p))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "spike result bindings", "evidence_leaf wrong file_bindings")
    # evidence_leaf wrong hash (paths match, sha256 != spike result binding).
    p = _evidence_leaf_payload()
    p["file_bindings"][0]["sha256"] = "sha256:" + "z" * 64
    req = _req_cmd(_trusted("evidence_leaf", None, p))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "spike result bindings", "evidence_leaf wrong binding hash")
    # evidence_leaf duplicate path (even when hashes differ) — raw uniqueItems
    # catches only identical objects; procedural check names the duplicate path.
    p = _evidence_leaf_payload()
    p["file_bindings"] = [{"path": "src/a.py", "sha256": FSHA},
                          {"path": "src/a.py", "sha256": "sha256:" + "z" * 64}]
    req = _req_cmd(_trusted("evidence_leaf", None, p))
    expect(lambda e: check_trusted_payload(req, {"run": _run_view(),
        "argument": _arg_view()}, registry, e, "neg"),
        "duplicate path", "evidence_leaf duplicate path")

    # --- D2D mirror negatives (one-sided registry/schema mutation each) ---
    bad_reg = copy.deepcopy(registry)
    bad_reg["attribution_subject_kinds"] = ["covered_actor", "auditor", "author"]
    expect(lambda e: check_schema_mirror(req_schema, res_schema, bad_reg, e, "neg"),
           "attribution_subject_kinds", "mirror subject_kinds drift")
    bad_reg = copy.deepcopy(registry)
    bad_reg["audit_verdicts"] = ["pass", "fail", "unknown"]
    expect(lambda e: check_schema_mirror(req_schema, res_schema, bad_reg, e, "neg"),
           "audit_verdicts", "mirror audit_verdicts drift")
    bad_reg = copy.deepcopy(registry)
    bad_reg["scope_reviews"] = ["pass", "fail", "unknown"]
    expect(lambda e: check_schema_mirror(req_schema, res_schema, bad_reg, e, "neg"),
           "scope_reviews", "mirror scope_reviews drift")
    bad_reg = copy.deepcopy(registry)
    bad_reg["attribution_observers"] = ["host", "configuration", "agent"]
    expect(lambda e: check_schema_mirror(req_schema, res_schema, bad_reg, e, "neg"),
           "attribution_observers", "mirror observers drift")

    # --- D2E presentation_selector negatives (one mutation each) ---
    # The JSON registry is the SSOT; these prove the procedural integrity check rejects
    # each class of presentation_selector corruption, and that the reviewed digest
    # freezes it. No selector algorithm or Python mapping is implemented here.
    bad = copy.deepcopy(registry)
    bad["presentation_selector"]["context_sections"].pop("block", None)
    expect(lambda e: check_presentation_selector(bad, e, "neg"),
           "context_sections keys", "presentation: missing/extra context")
    bad = copy.deepcopy(registry)
    bad["presentation_selector"]["context_sections"]["extra_ctx"] = ["core"]
    expect(lambda e: check_presentation_selector(bad, e, "neg"),
           "context_sections keys", "presentation: extra context key")
    bad = copy.deepcopy(registry)
    bad["presentation_selector"]["context_sections"]["bootstrap"].append("bogus/section")
    expect(lambda e: check_presentation_selector(bad, e, "neg"),
           "does not resolve", "presentation: unknown section")
    bad = copy.deepcopy(registry)
    bad["presentation_selector"]["context_sections"]["bootstrap"].append("core")
    expect(lambda e: check_presentation_selector(bad, e, "neg"),
           "duplicate section", "presentation: duplicate section")
    bad = copy.deepcopy(registry)
    bad["presentation_selector"]["unknown_reason_sections"] = []
    expect(lambda e: check_presentation_selector(bad, e, "neg"),
           "unknown_reason_sections", "presentation: empty fallback")
    bad = copy.deepcopy(registry)
    bad["reasons"]["audit.pending"]["sections"].append("bogus/section")
    expect(lambda e: check_referential(bad, e, "neg"),
           "unknown section", "presentation: unknown reason section")
    bad = copy.deepcopy(registry)
    bad["presentation_selector"]["terminal_sections"].append("core")
    expect(lambda e: check_registry_digest(bad, e, "neg"),
           "registry digest", "presentation: digest drift")

    # --- D6-A state-schema negatives (one mutation each) ---
    if state_schema:
        bad_ss = copy.deepcopy(state_schema)
        bad_ss["properties"]["status"]["enum"] = ["active", "bogus"]
        expect(lambda e: check_state_schema_mirror(bad_ss, registry, req_schema, res_schema, e, "neg"),
               "state.status enum", "state: status enum drift")
        bad_ss = copy.deepcopy(state_schema)
        bad_ss["$defs"]["childRecord"]["properties"]["state"]["enum"] = ["reserved", "bogus"]
        expect(lambda e: check_state_schema_mirror(bad_ss, registry, req_schema, res_schema, e, "neg"),
               "childRecord.state enum", "state: child-state enum drift")
        bad_ss = copy.deepcopy(state_schema)
        bad_ss["properties"]["protocol"]["const"] = "empirica/v3"
        expect(lambda e: check_state_schema_mirror(bad_ss, registry, req_schema, res_schema, e, "neg"),
               "protocol", "state: protocol const drift")
        # purpose shape drift vs request: state purpose differs from request actionChildReserve.purpose
        bad_ss = copy.deepcopy(state_schema)
        bad_ss["$defs"]["childRecord"]["properties"]["purpose"] = {"type": "integer"}
        expect(lambda e: check_state_schema_mirror(bad_ss, registry, req_schema, res_schema, e, "neg"),
               "request actionChildReserve.purpose", "state: purpose request drift")
        # purpose shape drift vs response: state purpose differs from response childSummary.purpose
        bad_ss = copy.deepcopy(state_schema)
        bad_ss["$defs"]["childRecord"]["properties"]["purpose"] = {"type": "string", "minLength": 0}
        expect(lambda e: check_state_schema_mirror(bad_ss, registry, req_schema, res_schema, e, "neg"),
               "response childSummary.purpose", "state: purpose response drift")
        # child-branch coverage drift: remove a branch selector
        bad_ss = copy.deepcopy(state_schema)
        bad_ss["$defs"]["childRecord"]["allOf"] = bad_ss["$defs"]["childRecord"]["allOf"][:2]
        expect(lambda e: check_state_schema_mirror(bad_ss, registry, req_schema, res_schema, e, "neg"),
               "child branch selectors", "state: child-branch coverage drift")
        # terminal-fingerprint coverage drift: add a non-terminal state to the
        # fingerprint-required set by mutating a branch's fingerprint to a non-required type
        bad_ss = copy.deepcopy(state_schema)
        for b in bad_ss["$defs"]["childRecord"]["allOf"]:
            if_state = b.get("if", {}).get("properties", {}).get("state", {})
            if if_state.get("const") == "reserved":
                b["then"]["properties"]["first_terminal_fingerprint"] = {
                    "$ref": "#/$defs/digest256"}
        expect(lambda e: check_state_schema_mirror(bad_ss, registry, req_schema, res_schema, e, "neg"),
               "terminal-fingerprint", "state: terminal-fingerprint coverage drift")
    bad_state = {"budgets": {"max_passes": 1, "passes_used": 5,
                            "max_spawns": 0, "spawns_used": 0}}
    expect(lambda e: check_state_invariants(bad_state, e, "neg"),
           "passes_used", "state: passes_used > max_passes")
    bad_state = {"budgets": {"max_passes": 1, "passes_used": 0,
                            "max_spawns": 1, "spawns_used": 3}}
    expect(lambda e: check_state_invariants(bad_state, e, "neg"),
           "spawns_used", "state: spawns_used > max_spawns")
    bad_state = {"stamp_seq": 2, "route_stamp": 5, "investigation_stamp": None}
    expect(lambda e: check_state_invariants(bad_state, e, "neg"),
           "route_stamp", "state: route_stamp > stamp_seq")
    bad_state = {"stamp_seq": 2, "route_stamp": None, "investigation_stamp": 9}
    expect(lambda e: check_state_invariants(bad_state, e, "neg"),
           "investigation_stamp", "state: investigation_stamp > stamp_seq")
    bad_state = {"children": [
        {"child_id": "dup"}, {"child_id": "dup"}]}
    expect(lambda e: check_state_invariants(bad_state, e, "neg"),
           "duplicate child_id", "state: duplicate child_id")
    # non-finite deadline rejection (NaN, +Inf, -Inf)
    for bad_dl in (float("nan"), float("inf"), float("-inf")):
        bad_state = {"children": [{"child_id": "c1", "deadline": bad_dl}]}
        expect(lambda e: check_state_invariants(bad_state, e, "neg"),
               "finite number", f"state: non-finite deadline {bad_dl}")
    # child-matrix mutation negatives: remove a state-selector branch's required field.
    if state_schema:
        bad_ss = copy.deepcopy(state_schema)
        for b in bad_ss["$defs"]["childRecord"]["allOf"]:
            if_state = b.get("if", {}).get("properties", {}).get("state", {})
            if if_state.get("const") == "reserved":
                b["then"]["required"] = ["spent", "refunded", "native_id"]
        expect(lambda e: check_state_child_branch_matrix(bad_ss, registry, e, "neg"),
               "all four relation", "state: child-matrix missing relation field")
    # child-matrix mutation: add a duplicate state-selector (two branches select same state).
    if state_schema:
        bad_ss = copy.deepcopy(state_schema)
        dup = copy.deepcopy(bad_ss["$defs"]["childRecord"]["allOf"][0])
        bad_ss["$defs"]["childRecord"]["allOf"].append(dup)
        expect(lambda e: check_state_child_branch_matrix(bad_ss, registry, e, "neg"),
               "exactly one", "state: child-matrix duplicate state-selector")
    # child-matrix mutation: relax a relation (reserved spent=false → const true) so the
    # valid child for reserved is rejected by schema (the valid-child assertion fails).
    if state_schema:
        bad_ss = copy.deepcopy(state_schema)
        for b in bad_ss["$defs"]["childRecord"]["allOf"]:
            if_state = b.get("if", {}).get("properties", {}).get("state", {})
            if if_state.get("const") == "reserved":
                b["then"]["properties"]["spent"] = {"const": True}
        expect(lambda e: check_state_child_branch_matrix(bad_ss, registry, e, "neg"),
               "valid child for state", "state: child-matrix valid-child rejected by mutated schema")



if __name__ == "__main__":
    raise SystemExit(main())
