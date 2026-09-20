"""Shared assertion helpers for the Empirica 2.0 D4 conformance suite.

Loads the accepted D2 schemas and the canonical PublicContract/host-profile registry from disk (the
SSOT — tests never copy reason/action/section/profile/transition/identity tables), and provides:

* :class:`ConformanceCase` — the unittest base every test file subclasses. ``bind_driver`` is the
  single place the suite reaches the SUT factory; an absent v2 seam becomes a per-case FAILURE
  (named owner + expected behavior), never a collection abort.
* :meth:`ConformanceCase.dispatch` — central normal request dispatch: validates every request
  against the v2 schema before the SUT sees it and validates the response before semantic
  assertions (D4 spec §4).
* :meth:`ConformanceCase.raw_dispatch` — explicit raw-wire dispatch for the strict-decoder
  negative tests (45–47): no request validation (the envelope is intentionally malformed/old/
  unknown), response shape still validated.
* envelope builders for valid ``empirica/v2`` requests matching the request schema;
* response-schema validation and stable structured-fact assertions (result type, status, reason
  code/parameters, affected obligation/witness, next-action/section IDs, artifact append count,
  child state, contract identity/digest, host profile/tier).

No domain algorithm is duplicated here and no expected-state machine lives in tests: helpers perform
schema/registry lookups and structural assertions only.
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

import jsonschema

from driver import V2SeamAbsent, new_driver

# ---------------------------------------------------------------------------
# Locate the accepted D2 contract registry (the SSOT — never copied into tests)
# ---------------------------------------------------------------------------
#
# The canonical protocol string, contract identity, and every reason/action/section/profile/
# tier/transition inventory are DERIVED from the loaded JSON registry (the SSOT); tests and
# harness helpers never copy a full inventory as a literal table. A small selection (one profile,
# one reason, a few terminal variants) is a test scenario literal, not a mirror table.

_HERE = Path(__file__).resolve()
_REPO_ROOT: Path | None = None
_cursor = _HERE.parent
for _depth in range(8):
    if (_cursor / "contracts" / "empirica" / "v2" / "public-contract.json").exists():
        _REPO_ROOT = _cursor
        break
    _cursor = _cursor.parent
if _REPO_ROOT is None:
    raise RuntimeError("could not locate contracts/empirica/v2 from %s" % _HERE)

_V2 = _REPO_ROOT / "contracts" / "empirica" / "v2"


def _load_json(name: str) -> dict:
    return json.loads((_V2 / name).read_text(encoding="utf-8"))


_PUBLIC_CONTRACT = _load_json("public-contract.json")
_HOST_PROFILES = _load_json("host-profiles.json")
_REQUEST_SCHEMA = _load_json("request.schema.json")
_RESPONSE_SCHEMA = _load_json("response.schema.json")

# Canonical protocol/contract identity DERIVED from the registry (SSOT), never copied as constants.
_PROTOCOL = _PUBLIC_CONTRACT["protocol"]
_CONTRACT_ID = _PUBLIC_CONTRACT["id"]
_CONTRACT_VERSION = _PUBLIC_CONTRACT["version"]
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Canonical registry IDs, loaded (not copied) from the PublicContract.
REASONS: dict[str, dict] = _PUBLIC_CONTRACT["reasons"]
NEXT_ACTIONS: dict[str, dict] = _PUBLIC_CONTRACT["next_actions"]
SECTIONS: dict[str, dict] = _PUBLIC_CONTRACT["sections"]
STATUSES: list[str] = _PUBLIC_CONTRACT["statuses"]
DECISIONS: list[str] = _PUBLIC_CONTRACT["decisions"]
CHILD_STATES: list[str] = _PUBLIC_CONTRACT["child_lifecycle"]["states"]
CHILD_TERMINAL: list[str] = _PUBLIC_CONTRACT["child_lifecycle"]["terminal_states"]
CHILD_TRANSITIONS: list = _PUBLIC_CONTRACT["child_lifecycle"]["transitions"]
HOST_TIERS: list[str] = _PUBLIC_CONTRACT["host_tiers"]
AUTHOR_ACTIONS: list[str] = _PUBLIC_CONTRACT["actions"]["author"]
TRUSTED_ACTIONS: list[str] = _PUBLIC_CONTRACT["actions"]["trusted"]

_PROFILES = {p["profile_id"]: p for p in _HOST_PROFILES["profiles"]}
PROFILE_IDS = sorted(_PROFILES)
_PROFILE_TIER = {pid: p["current_tier"] for pid, p in _PROFILES.items()}
_PROFILE_UNSUPPORTED = {pid: set(p.get("unsupported_reason_ids", []))
                        for pid, p in _PROFILES.items()}
_PROFILE_MISSING = {pid: list(p.get("unsupported_reason_ids", []))
                    for pid, p in _PROFILES.items()}

# Adverse terminal child states REQUIRE a recovery_action; completed FORBIDS it (D2A §3).
CHILD_ADVERSE_TERMINAL = [s for s in CHILD_TERMINAL if s != "completed"]
# Canonical operation contexts DERIVED from the registry (D2A §2) — used by the selector seam.
OPERATION_CONTEXTS: list[str] = _PUBLIC_CONTRACT["operation_contexts"]
UNTRUSTED_OPEN = _PUBLIC_CONTRACT["untrusted_delimiters"]["open"]
UNTRUSTED_CLOSE = _PUBLIC_CONTRACT["untrusted_delimiters"]["close"]
# D2C canonical artifact vocabularies, DERIVED from the registry (never copied as constants).
CLAIM_KINDS: list[str] = _PUBLIC_CONTRACT["claim_kinds"]
ARTIFACT_KINDS: list[str] = _PUBLIC_CONTRACT["artifact_kinds"]
ARTIFACT_OUTCOMES: list[str] = _PUBLIC_CONTRACT["artifact_outcomes"]
SPIKE_GATES: list[str] = _PUBLIC_CONTRACT["spike_gates"]
# D2E canonical presentation-selector registry, DERIVED from the PublicContract (the SSOT).
# Tests compare real selector output directly to these loaded arrays; no context/reason
# mapping or fallback table is copied into a test file. Every context_sections key equals a
# registered operation_context; every value resolves to a canonical section (preflight).
PRESENTATION_SELECTOR: dict = _PUBLIC_CONTRACT["presentation_selector"]
SELECTOR_CONTEXT_SECTIONS: dict[str, list[str]] = PRESENTATION_SELECTOR["context_sections"]
SELECTOR_TERMINAL_SECTIONS: list[str] = PRESENTATION_SELECTOR["terminal_sections"]
SELECTOR_UNKNOWN_REASON_SECTIONS: list[str] = PRESENTATION_SELECTOR["unknown_reason_sections"]
# Terminal RUN statuses (D2E): a status is terminal iff the run is no longer active. Derived
# from the loaded STATUSES vocabulary, never copied as a literal table.
TERMINAL_RUN_STATUSES: list[str] = [s for s in STATUSES if s != "active"]


# ---------------------------------------------------------------------------
# HarnessDefect (D4 spec §6) — a future real-SUT prerequisite diagnostic
# ---------------------------------------------------------------------------
#
# Prerequisite/setup helpers (``require_*``) raise :class:`HarnessDefect`, NEVER
# ``AssertionError``. unittest therefore routes a failed prerequisite to ``result.errors`` (a
# harness defect), not to ``result.failures`` (a test assertion failure). This keeps a broken
# setup or an absent seam from masquerading as a passing assertion.


class HarnessDefect(Exception):
    """Raised by ``require_*`` prerequisite helpers when a public setup precondition did not hold.

    It is deliberately NOT an ``AssertionError``: a failed prerequisite is a harness defect (the
    SUT seam is absent or a public setup step did not admit), not a behavioral test failure.
    """


# Deterministic canonical digest of the PublicContract (D9 computes this at runtime; the static
# fixtures record the same value, so tests assert the response digest matches the registry digest).
_CONTRACT_DIGEST = "sha256:" + hashlib.sha256(
    json.dumps(_PUBLIC_CONTRACT, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def contract_digest() -> str:
    return _CONTRACT_DIGEST


def active_set_digest(active_evidence_ids: list[str]) -> str:
    """Canonical registry digest of the exact ordered ``active_evidence_ids`` JSON array (D2C
    invariant §10). This is the single central helper tests use instead of per-case
    self-comparison: ``claim.evidence_digest`` must equal this value."""
    canonical = json.dumps(active_evidence_ids, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def protocol() -> str:
    """The canonical protocol string (``empirica/v2``), derived from the PublicContract registry.

    Tests that must inject raw persisted state (the strict-decoder negatives 45–47) use this helper
    instead of a literal ``"empirica/v2"`` string so the canonical identity has one owner (the
    registry). Negative envelopes that intentionally carry a NON-v2 protocol stay as raw literals.
    """
    return _PROTOCOL


def contract_id() -> str:
    return _CONTRACT_ID


def contract_version() -> str:
    return _CONTRACT_VERSION


def ordered_dedupe(*lists) -> list:
    """Ordered first-occurrence dedupe across caller-supplied registry lists (D2E).

    Contains no context/reason mapping: callers pass the exact loaded registry lists (the
    context-section base, each reason's registered sections, the terminal sections) and this
    helper only concatenates and deduplicates preserving first-occurrence order. The canonical
    presentation_selector union algorithm is owned by the real D9 selector; this helper builds
    the expected output the selector must match."""
    out: list = []
    for lst in lists:
        for item in lst:
            if item not in out:
                out.append(item)
    return out


def reason(code: str) -> dict:
    return REASONS[code]


def next_action(action_id: str) -> dict:
    return NEXT_ACTIONS[action_id]


def section(section_id: str) -> dict:
    return SECTIONS[section_id]


def profile(profile_id: str) -> dict:
    return _PROFILES[profile_id]


def profile_tier(profile_id: str) -> str:
    return _PROFILE_TIER[profile_id]


def profile_unsupported(profile_id: str) -> set[str]:
    return _PROFILE_UNSUPPORTED[profile_id]


def profile_missing_capabilities(profile_id: str) -> list[str]:
    """The exact ordered list of unsupported (missing) capability reason IDs for the profile
    (D1-H). Derived from the registry, never copied; compared exactly (order matters)."""
    return list(_PROFILE_MISSING[profile_id])


# ---------------------------------------------------------------------------
# Envelope builders — valid empirica/v2 requests matching the request schema
# ---------------------------------------------------------------------------

_seq = 0


def _rid(label: str) -> str:
    global _seq
    _seq += 1
    return f"req-{label}-{_seq}"


def _envelope(request_id: str, command: dict) -> dict:
    return {"protocol": _PROTOCOL, "request_id": request_id, "command": command}


def start_run(*, goal: str, project: str = "demo", session: str = "s1",
              request_id: str | None = None, budgets: dict | None = None,
              modes: dict | None = None) -> dict:
    """Build a valid StartRun envelope. Host profile selection is a driver-factory fact, never an
    invented StartRun field — there is deliberately no ``profile_id`` parameter (D4 spec §4)."""
    cmd: dict = {"type": "StartRun",
                 "selector": {"project": project, "session": session},
                 "goal": goal}
    if budgets is not None:
        cmd["budgets"] = budgets
    if modes is not None:
        cmd["modes"] = modes
    return _envelope(request_id or _rid("start"), cmd)


def resolve_run(*, project: str = "demo", session: str = "s1",
                request_id: str | None = None) -> dict:
    return _envelope(request_id or _rid("resolve"),
                     {"type": "ResolveRun",
                      "selector": {"project": project, "session": session}})


def observe_action(*, run_id: str, action: dict, observed_at: str | None = None,
                   request_id: str | None = None) -> dict:
    cmd: dict = {"type": "ObserveAction", "run_id": run_id, "action": action}
    if observed_at is not None:
        cmd["observed_at"] = observed_at
    return _envelope(request_id or _rid("observe"), cmd)


def evaluate(*, run_id: str, intent: str = "continue", observed_at: str | None = None,
             request_id: str | None = None) -> dict:
    cmd: dict = {"type": "EvaluateRun", "run_id": run_id, "intent": intent}
    if observed_at is not None:
        cmd["observed_at"] = observed_at
    return _envelope(request_id or _rid("eval"), cmd)


def get_run(*, run_id: str, request_id: str | None = None) -> dict:
    return _envelope(request_id or _rid("getrun"), {"type": "GetRun", "run_id": run_id})


def get_argument(*, run_id: str, request_id: str | None = None) -> dict:
    return _envelope(request_id or _rid("getarg"),
                     {"type": "GetArgument", "run_id": run_id})


def restore_run(*, run_id: str, request_id: str | None = None) -> dict:
    return _envelope(request_id or _rid("restore"),
                     {"type": "RestoreRun", "run_id": run_id})


def get_contract(*, target: str = "index", section_id: str | None = None,
                 request_id: str | None = None) -> dict:
    cmd: dict = {"type": "GetContract", "target": target}
    if target == "section":
        if not section_id:
            raise ValueError("section target requires section_id")
        cmd["section_id"] = section_id
    return _envelope(request_id or _rid("contract"), cmd)


# Author action builders (closed control objects per the request schema).

def action_graph(payload: dict | None = None) -> dict:
    a = {"kind": "graph"}
    if payload is not None:
        a["payload"] = payload
    return a


def action_research(*, claim_id: str, source_kind: str, result: str,
                    payload: dict | None = None) -> dict:
    a = {"kind": "research", "claim_id": claim_id,
         "source_kind": source_kind, "result": result}
    if payload is not None:
        a["payload"] = payload
    return a


def action_spike_request(*, claim_id: str, command: str, dependent_files: list[str]) -> dict:
    """Canonical spike_request action. This is also the canonical request path for ``spike.regate``:
    re-gate of a stale active head is requested by a fresh ``spike_request`` for the same claim over
    current observations (D4 spec §4). There is no separate ``regate`` wire action kind."""
    return {"kind": "spike_request", "claim_id": claim_id,
            "command": command, "dependent_files": list(dependent_files)}


def action_configure_run(*, budgets: dict | None = None,
                         modes: dict | None = None) -> dict:
    a: dict = {"kind": "configure_run"}
    if budgets is not None:
        a["budgets"] = budgets
    if modes is not None:
        a["modes"] = modes
    return a


def action_route(*, reason: str) -> dict:
    return {"kind": "route", "reason": reason}


def action_investigate() -> dict:
    return {"kind": "investigate"}


def action_freeze() -> dict:
    return {"kind": "freeze"}


def action_dispatch(*, target: str, claim_id: str | None = None) -> dict:
    a: dict = {"kind": "dispatch", "target": target}
    if claim_id is not None:
        a["claim_id"] = claim_id
    return a


def action_child_reserve(*, purpose: str, role_profile: str, execution: str,
                        deadline: str | None = None) -> dict:
    a: dict = {"kind": "child_reserve", "purpose": purpose,
               "role_profile": role_profile, "execution": execution}
    if deadline is not None:
        a["deadline"] = deadline
    return a


def action_evidence_leaf(*, payload: dict | None = None,
                         boundary: str = "application") -> dict:
    a = {"kind": "evidence_leaf",
         "trusted": {"capability_ref": "<redacted>", "boundary": boundary}}
    if payload is not None:
        a["payload"] = payload
    return a


def action_attribution(*, payload: dict | None = None,
                       boundary: str = "host") -> dict:
    a = {"kind": "attribution",
         "trusted": {"capability_ref": "<redacted>", "boundary": boundary}}
    if payload is not None:
        a["payload"] = payload
    return a


def action_child_event(*, child_id: str, payload: dict,
                        boundary: str = "host") -> dict:
    return {"kind": "child_event", "child_id": child_id,
            "trusted": {"capability_ref": "<redacted>", "boundary": boundary},
            "payload": payload}


def action_audit_verdict(*, child_id: str, payload: dict,
                         boundary: str = "host") -> dict:
    return {"kind": "audit_verdict", "child_id": child_id,
            "trusted": {"capability_ref": "<redacted>", "boundary": boundary},
            "payload": payload}


# ---------------------------------------------------------------------------
# Canonical graph/scenario helper (D4-S2 spec; D2C/review correction)
# ---------------------------------------------------------------------------
#
# One stable-prefix canonical graph builder. Adding C1 never changes C0 text/digest. Claim IDs
# and texts used by later research/spikes must come from this graph; tests must not rely on an
# undeclared implicit C0. Use fresh driver instances for independent variants.
#
# Each canonical claim carries a D2C ``kind`` (``ordinary``, ``needs-experiment``,
# ``needs-decision``), DERIVED from the registry ``CLAIM_KINDS``. The default kind is
# ``needs-experiment`` for two-fold evidence/freshness cases (research + spike); research-only
# freeze/terminal cases pass ``kind="ordinary"``. The kind is configurable so a single stable
# builder serves both scenario families without duplicating claim text/IDs.

_CANONICAL_CLAIMS = [
    {"id": "C0", "text": "The public contract is internally consistent.", "gating": True},
    {"id": "C1", "text": "Claim C1 supports the root.", "gating": True},
]
_CANONICAL_ROOT = "C0"
_DEFAULT_CLAIM_KIND = "needs-experiment"


def canonical_graph(*, n_claims: int = 1, kind: str | None = None) -> dict:
    """Return the canonical graph with ``n_claims`` gating claims. C0 text/digest is stable
    across all projections; adding C1 never changes it.

    Each claim carries the D2C ``kind`` field (configurable, default ``needs-experiment`` for
    two-fold evidence/freshness cases). Research-only freeze/terminal cases pass
    ``kind="ordinary"``. The kind must be a canonical ``CLAIM_KINDS`` value.
    """
    claim_kind = kind if kind is not None else _DEFAULT_CLAIM_KIND
    if claim_kind not in CLAIM_KINDS:
        raise ValueError(
            f"canonical claim kind {claim_kind!r} is not canonical; "
            f"expected one of {CLAIM_KINDS}")
    claims = []
    for c in _CANONICAL_CLAIMS[:n_claims]:
        claim = dict(c)
        claim["kind"] = claim_kind
        claims.append(claim)
    edges = []
    if n_claims > 1:
        edges.append({"from": _CANONICAL_ROOT, "to": "C1", "type": "SupportedBy"})
    return {"root": _CANONICAL_ROOT, "claims": claims, "edges": edges}


def canonical_claim_id(*, index: int = 0) -> str:
    """Return the canonical claim ID at the given index (stable prefix)."""
    return _CANONICAL_CLAIMS[index]["id"]


# ---------------------------------------------------------------------------
# D2D closed trusted-ingress payload builders (D4-S3a-R)
# ---------------------------------------------------------------------------
#
# These module-level helpers construct accepted D2D exact payloads. They are the single
# construction point for trusted child_event, attribution, and evidence_leaf payloads used
# by private ingress and the preflight representative envelopes. Fingerprints are stable
# canonical digests of event facts so identical calls produce identical fingerprints.

def child_event_fingerprint(state: str, native_id: str | None) -> str:
    """Stable canonical digest of child-event facts (state, native_id) so identical calls
    produce identical fingerprints (D2D). Used for identical-replay detection."""
    facts = json.dumps({"state": state, "native_id": native_id},
                       sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(facts).hexdigest()


def build_child_event_payload(state: str, *,
                              native_id: str | None = None,
                              result_digest: str | None = None) -> dict:
    """Construct a valid D2D ``child_event`` closed payload.

    - ``launch_rejected`` requires ``native_id`` null and ``result_digest`` null;
    - ``completed`` requires non-null ``result_digest``;
    - all other states require non-null ``native_id`` and null ``result_digest``.
    The fingerprint is a stable canonical digest of (state, native_id).
    """
    fp = child_event_fingerprint(state, native_id)
    return {"state": state, "native_id": native_id,
            "fingerprint": fp, "result_digest": result_digest}


def build_attribution_payload(*, subject_kind: str, subject_id: str,
                              child_id: str | None = None,
                              provider_id: str | None = None,
                              model_id: str | None = None,
                              observed_by: str = "host",
                              covered_artifact_ids: list[str] | None = None) -> dict:
    """Construct a valid D2D ``attribution`` closed payload.

    - ``auditor`` requires non-null ``child_id`` and empty ``covered_artifact_ids``;
    - ``covered_actor`` requires ``child_id`` null and nonempty ``covered_artifact_ids``;
    - ``provider_id``/``model_id`` are both non-null or both null.
    """
    return {
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "child_id": child_id,
        "provider_id": provider_id,
        "model_id": model_id,
        "observed_by": observed_by,
        "covered_artifact_ids": list(covered_artifact_ids or []),
    }


def build_evidence_leaf_payload(*, harness_request_id: str,
                                command_digest: str,
                                prerequisite_research_ids: list[str],
                                file_bindings: list[dict],
                                exit_code: int,
                                result_digest: str) -> dict:
    """Construct a valid D2D ``evidence_leaf`` closed payload (deterministic harness result).

    Gate is derived from ``exit_code`` and is not supplied. Claim/command identity comes
    from the sealed request and is not caller-repeated.
    """
    return {
        "harness_request_id": harness_request_id,
        "command_digest": command_digest,
        "prerequisite_research_ids": list(prerequisite_research_ids),
        "file_bindings": list(file_bindings),
        "exit_code": exit_code,
        "result_digest": result_digest,
    }


# ---------------------------------------------------------------------------
# ConformanceCase — unittest base
# ---------------------------------------------------------------------------


class ConformanceCase(unittest.TestCase):
    """Base for every D4 conformance test.

    ``bind_driver`` is the single SUT entry: it calls ``driver.new_driver`` and, on an absent v2
    seam, fails the case by name (owner + behavior) so the suite collects and executes fully and
    reports each absent behavior rather than aborting on a global import error. Host profile
    selection is bound through the factory only (no invented StartRun field).
    """

    # Default profile used by cases that do not assert a specific host tier.
    DEFAULT_PROFILE = "claude-code@2.1.270"
    DEFAULT_GOAL = "Prove the public contract is internally consistent."

    # ---- SUT binding -------------------------------------------------------

    def bind_driver(self, owner: str, case_id: str, behavior: str,
                    profile_id: str | None = None, limits: dict | None = None):
        pid = profile_id or self.DEFAULT_PROFILE
        try:
            drv = new_driver(profile_id=pid, limits=limits)
        except V2SeamAbsent as exc:
            self.fail(f"[{owner}] {case_id}: {behavior} — v2 SUT seam absent: {exc}")
        return drv

    # ---- central dispatch (D4 spec §4) -------------------------------------

    def dispatch(self, drv, envelope: dict) -> dict:
        """Normal dispatch: validate the request against the v2 schema, send it to the SUT,
        validate the response, return it. Every normal request goes through here so a malformed
        normal request is a test harness defect, never silently passed to the SUT."""
        self.assert_valid_request(envelope)
        resp = drv.request(envelope)
        self.assert_valid_response(resp)
        return resp

    def raw_dispatch(self, drv, envelope: dict) -> dict:
        """Raw-wire dispatch for strict-decoder negative tests (45–47): the envelope is
        intentionally malformed/old/unknown, so the request is NOT validated before the SUT sees
        it; the response shape is still validated."""
        resp = drv.request(envelope)
        self.assert_valid_response(resp)
        return resp

    # ---- schema validation -------------------------------------------------

    def assert_valid_request(self, envelope: dict) -> None:
        jsonschema.validate(instance=envelope, schema=_REQUEST_SCHEMA)

    def assert_valid_response(self, resp: dict) -> None:
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)

    def assert_protocol_identity(self, env: dict, request_id: str) -> None:
        self.assertEqual(env.get("protocol"), _PROTOCOL, "protocol must be empirica/v2")
        self.assertEqual(env.get("request_id"), request_id, "request_id must round-trip")

    # ---- registry-backed structural assertions -----------------------------

    def assert_contract_identity(self, run: dict, *, sections: list[str] | None = None) -> None:
        c = run["contract"]
        self.assertEqual(c["id"], _CONTRACT_ID)
        self.assertEqual(c["version"], _CONTRACT_VERSION)
        self.assertEqual(c["digest"], _CONTRACT_DIGEST,
                         "contract digest must equal the canonical registry digest")
        self.assertRegex(c["digest"], _DIGEST_RE, "contract digest must be sha256:<hex>")
        self.assertEqual(sorted(c["relevant_sections"]), sorted(set(c["relevant_sections"])),
                         "relevant_sections must be unique")
        for sid in c["relevant_sections"]:
            self.assertIn(sid, SECTIONS, f"relevant_section {sid!r} must be a canonical section")
        if sections is not None:
            self.assertEqual(sorted(c["relevant_sections"]), sorted(sections),
                             f"relevant_sections must be exactly {sections}")

    def assert_status(self, run: dict, status: str) -> None:
        self.assertIn(run["status"], STATUSES, "run.status must be a canonical status")
        self.assertEqual(run["status"], status)

    def assert_allow(self, resp: dict, *, converged: bool | None = None) -> dict:
        result = resp["result"]
        self.assertEqual(result["type"], "Allow", f"expected Allow, got {result.get('type')}")
        if "contract_result" in result:
            return result
        run = result["run"]
        self.assertEqual(result["converged"], (run["status"] == "converged"),
                         "Allow.converged must equal (run.status == 'converged')")
        if converged is not None:
            self.assertEqual(result["converged"], converged)
        self.assert_contract_identity(run)
        return result

    def assert_block(self, resp: dict) -> dict:
        result = resp["result"]
        self.assertEqual(result["type"], "Block", f"expected Block, got {result.get('type')}")
        self.assertGreaterEqual(len(result["reasons"]), 1, "Block must carry >=1 reason")
        for r in result["reasons"]:
            self.assertIn(r["code"], REASONS, f"reason code {r['code']!r} must be canonical")
            spec = REASONS[r["code"]]
            self.assertEqual(r["next_actions"], spec["next_actions"],
                             f"reason {r['code']} next_actions must match registry order")
            self.assertEqual(r["sections"], spec["sections"],
                             f"reason {r['code']} sections must match registry order")
            if "affected" in r:
                aff = r["affected"]
                self.assertTrue(aff.keys() <= {"obligation_id", "witness_ref"},
                                "affected must be obligation_id/witness_ref only")
        self.assert_contract_identity(result["run"])
        return result

    def assert_block_reason(self, resp: dict, code: str, *,
                             parameters: dict | None = None,
                             affected: dict | None = None) -> dict:
        result = self.assert_block(resp)
        match = [r for r in result["reasons"] if r["code"] == code]
        self.assertTrue(match, f"Block must include reason {code!r}; got {[r['code'] for r in result['reasons']]}")
        r = match[0]
        if parameters is not None:
            self.assertEqual(r.get("parameters", {}), parameters,
                             f"reason {code} parameters mismatch")
        if affected is not None:
            self.assertEqual(r.get("affected"), affected)
        return result

    def assert_block_only(self, resp: dict, codes: list[str]) -> dict:
        """Assert the Block carries exactly the given reason codes in order.

        Compares the actual ordered list of reason codes to the expected list exactly:
        a duplicate reason or a reordered shortcut both fail (a set-only comparison would
        hide either). Rejects a no-op that emits nothing or unrelated reasons."""
        result = self.assert_block(resp)
        got = [r["code"] for r in result["reasons"]]
        self.assertEqual(got, list(codes),
                         f"Block reason codes must be exactly {list(codes)} in order; got {got}")
        return result

    def assert_block_sole_reason(self, resp: dict, code: str, *,
                                 parameters: dict | None = None) -> dict:
        """Assert a Block carries EXACTLY ONE reason with the given code, empty/exact canonical
        parameters, ordered next_actions/sections equal the registry, and a nonempty message
        (D4-S3b cases 36/45–47). Strictly stronger than ``assert_block``: rejects multi-reason
        Blocks and a missing/nonempty message. No Fault|Block union."""
        result = self.assert_block(resp)
        self.assertEqual(len(result["reasons"]), 1,
                         f"Block must carry exactly one reason; got {len(result['reasons'])}")
        r = result["reasons"][0]
        self.assertEqual(r["code"], code,
                         f"sole reason must be {code!r}; got {r['code']!r}")
        spec = REASONS[code]
        if parameters is not None:
            self.assertEqual(r.get("parameters", {}), parameters,
                             f"reason {code} parameters must equal {parameters!r}")
        else:
            self.assertEqual(r.get("parameters", {}), {},
                             f"reason {code} parameters must be empty")
        self.assertEqual(r["next_actions"], spec["next_actions"],
                         f"reason {code} next_actions must match registry order")
        self.assertEqual(r["sections"], spec["sections"],
                         f"reason {code} sections must match registry order")
        self.assertTrue(r.get("message"),
                        f"reason {code} must carry a nonempty message")
        return result

    def assert_fault(self, resp: dict, *, code: str | None = None,
                     fail_direction: str | None = None) -> dict:
        result = resp["result"]
        self.assertEqual(result["type"], "Fault", f"expected Fault, got {result.get('type')}")
        if code is not None:
            self.assertEqual(result["code"], code)
        if fail_direction is not None:
            self.assertEqual(result["fail_direction"], fail_direction)
        return result

    def assert_inert(self, resp: dict, *, reason: str | None = None) -> dict:
        result = resp["result"]
        self.assertEqual(result["type"], "Inert", f"expected Inert, got {result.get('type')}")
        if reason is not None:
            self.assertEqual(result["reason"], reason)
        return result

    def assert_host_view(self, run: dict, profile_id: str) -> None:
        host = run["host"]
        self.assertEqual(host["profile_id"], profile_id)
        self.assertIn(host["tier"], HOST_TIERS)
        self.assertEqual(host["tier"], profile_tier(profile_id))
        for cap in host.get("missing_capabilities", []):
            self.assertIn(cap, profile_unsupported(profile_id),
                           "missing capability must be a declared unsupported reason for the profile")

    def assert_child_summary(self, run: dict, child_id: str, *, state: str | None = None,
                             purpose: str | None = None) -> dict:
        kids = run.get("children", [])
        match = [c for c in kids if c["child_id"] == child_id]
        self.assertTrue(match, f"run must list child {child_id!r}")
        c = match[0]
        if state is not None:
            self.assertIn(c["state"], CHILD_STATES)
            self.assertEqual(c["state"], state)
        if purpose is not None:
            self.assertEqual(c["purpose"], purpose)
        # D2A §3: adverse terminal states REQUIRE a canonical recovery_action; completed FORBIDS it.
        if c["state"] in CHILD_ADVERSE_TERMINAL:
            self.assertIn("recovery_action", c,
                           "an adverse terminal child must carry a recovery action")
            self.assertIn(c["recovery_action"], NEXT_ACTIONS,
                           "recovery_action must be a canonical next action")
        elif c["state"] == "completed":
            self.assertNotIn("recovery_action", c,
                              "a completed child must NOT carry a recovery action")
        return c

    def assert_no_private_capability(self, obj: dict) -> None:
        """Private capability material must never appear in public views/artifacts/diagnostics.

        Recursively asserts no banned private KEY appears in any dict and no banned VALUE
        substring appears in any string value. Key matching is exact so canonical public
        fields like ``missing_capabilities`` never match the banned key ``capability``;
        this is strictly stronger than the prior flat-substring scan (D4-S3b)."""
        banned_keys = (
            "capability_ref", "capability", "nonce", "ticket", "reservation_id",
            "transcript_path", "native_id", "fingerprint", "provider_id", "model_id",
            "secret",
        )
        banned_value_substrings = ("capability_ref", "<redacted>", "topsecret")

        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in banned_keys:
                        self.fail(
                            f"private/banned field {k!r} must not appear in a public view")
                    _walk(v)
            elif isinstance(o, list):
                for item in o:
                    _walk(item)
            elif isinstance(o, str):
                for sub in banned_value_substrings:
                    if sub in o:
                        self.fail(
                            f"private/banned value {sub!r} must not appear in a public view")
        _walk(obj)

    def assert_no_full_contract_dump(self, run: dict) -> None:
        """Recursively assert no full-contract dump appears in any ordinary/compaction/reload
        object (D4-S3b cases 42–44).

        Rejects the exact keys ``full_contract``, ``public_contract``, and ``full`` wherever they
        appear in any nested dict or list; ordinary/compaction/reload surfaces must project only
        bounded contract fragments, never the full contract inline. Purpose-specific: the
        full-contract dump is owned solely here; private/native/provider capability keys remain
        :meth:`assert_no_private_capability` and persisted operational/revision fields remain
        :meth:`assert_no_persisted_operational_fields`. Cases 42–44 call these distinct helpers
        directly."""
        banned_keys = ("full_contract", "public_contract", "full")

        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in banned_keys:
                        self.fail(
                            f"full-contract dump key {k!r} must not appear in an "
                            f"ordinary/compaction/reload surface")
                    _walk(v)
            elif isinstance(o, list):
                for item in o:
                    _walk(item)
        _walk(run)

    def assert_no_persisted_operational_fields(self, obj) -> None:
        """Recursively assert no persisted operational/revision field appears in ``obj``
        (D4-S3b cases 42–44).

        Purpose-specific: owns ONLY revisions (exact ``revision`` and ``*_revision``), pointers
        (exact ``pointer`` and ``*_pointer``), history (exact ``history`` and ``*_history``),
        exact ``phase``, operational counters (``*_used``), the persisted
        ``first_terminal_fingerprint``, and persisted hash forms (exact ``hash``/``sha256`` and
        keys ending ``_hash``). Does NOT ban any ``*_digest`` field — content digests are public
        identity. Private/native/provider capability keys remain solely
        :meth:`assert_no_private_capability`; the full-contract dump remains solely
        :meth:`assert_no_full_contract_dump`. Cases 42–44 call these distinct helpers directly."""
        def _is_persisted_operational(k: str) -> bool:
            return (k.endswith("_revision") or k.endswith("_pointer")
                    or k.endswith("_history") or k == "phase" or k.endswith("_used")
                    or k == "first_terminal_fingerprint"
                    or k == "hash" or k == "sha256" or k.endswith("_hash")
                    or k == "revision" or k == "pointer" or k == "history")

        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if _is_persisted_operational(k):
                        self.fail(
                            f"persisted operational field {k!r} must not appear in a "
                            f"public/compaction surface")
                    _walk(v)
            elif isinstance(o, list):
                for item in o:
                    _walk(item)
        _walk(obj)

    def assert_claim_kind(self, claim: dict, kind: str) -> None:
        """Assert a D2C claim item carries the expected canonical ``kind`` (D2C). The kind is
        derived from the graph and never inferred by the validator from available artifacts."""
        self.assertIn(kind, CLAIM_KINDS, f"expected kind {kind!r} must be canonical")
        self.assertEqual(claim.get("kind"), kind,
                         f"claim {claim.get('claim_id')!r} kind must be {kind!r}, got {claim.get('kind')!r}")

    # ---- policy-free prerequisite helpers (D4 spec §4, §8; D4-S2/D2C corrections) ---
    # These drive AND assert public prerequisites (started run, admitted child, routed claim,
    # supporting research, completed passing baseline spike, frozen scope, deferred claim, reserved
    # audit child) WITHOUT computing expected policy. Each raises HarnessDefect (never
    # AssertionError) when a public precondition did not hold, so a failed setup is a harness
    # defect, not a behavioral test failure. They expose actual SUT-admitted IDs rather than
    # fabricating them.

    # ---- D2C artifact lookup helpers (D4-S2/D2C) ---------------------------
    # All failures remain HarnessDefect diagnostics. These look up artifacts from the canonical
    # D2C ``ArgumentView.artifacts`` ordered union by kind/claim_id and preserve D2C sequence.
    # They never use raw ``drv.artifacts()``, JSON substring search, or count growth alone.

    def get_argument_artifacts(self, drv, run_id: str) -> list[dict]:
        """Dispatch GetArgument and return the canonical D2C ``artifacts`` list (ordered by
        ``sequence``). Raises HarnessDefect if the argument view is absent."""
        resp = self.dispatch(drv, get_argument(run_id=run_id))
        try:
            arg = self.assert_argument_view(resp["result"])
        except AssertionError as exc:
            raise HarnessDefect(f"GetArgument must return a typed argument (prerequisite): {exc}") from exc
        arts = arg.get("artifacts")
        if not isinstance(arts, list):
            raise HarnessDefect("ArgumentView.artifacts must be a list (prerequisite)")
        return arts

    def find_argument_artifacts(self, arts, *, kind: str | None = None,
                                claim_id: str | None = None,
                                active: bool | None = None,
                                outcome: str | None = None) -> list[dict]:
        """Return artifacts from a D2C artifacts list matching kind/claim_id/active/outcome
        in sequence order."""
        out = []
        for a in arts:
            if not isinstance(a, dict):
                continue
            if kind is not None and a.get("kind") != kind:
                continue
            if claim_id is not None and a.get("claim_id") != claim_id:
                continue
            if active is not None and a.get("active") != active:
                continue
            if outcome is not None and a.get("outcome") != outcome:
                continue
            out.append(a)
        return out

    def find_one_argument_artifact(self, arts, *, kind: str | None = None,
                                   claim_id: str | None = None,
                                   active: bool | None = None,
                                   outcome: str | None = None) -> dict:
        """Return the single matching D2C artifact; HarnessDefect if zero or >1."""
        matches = self.find_argument_artifacts(arts, kind=kind, claim_id=claim_id,
                                                active=active, outcome=outcome)
        if not matches:
            label = f"{kind!r}"
            if claim_id is not None:
                label += f" claim_id={claim_id!r}"
            if active is not None:
                label += f" active={active!r}"
            raise HarnessDefect(f"no {label} D2C artifact found (prerequisite)")
        if len(matches) > 1:
            label = f"{kind!r}"
            if claim_id is not None:
                label += f" claim_id={claim_id!r}"
            raise HarnessDefect(f"expected one {label} D2C artifact, found {len(matches)} (prerequisite)")
        return matches[0]

    # ---- D2C artifact assertions (D4-S2/D2C) ------------------------------
    # Assert artifact kind, sequence, content-addressed IDs, claim/digest, command/digest,
    # dependent files, prerequisite research IDs, exit_code/spike_gate, file bindings, and
    # optional supersedes — using direct field access on the D2C ``ArgumentView.artifacts``
    # union, never raw storage or JSON substring search.

    def assert_research_artifact(self, art: dict, *, claim_id: str | None = None,
                                 outcome: str | None = None, active: bool | None = None) -> None:
        self.assertIsInstance(art, dict, "research artifact must be a dict")
        self.assertEqual(art.get("kind"), "research",
                         "research artifact kind must be 'research'")
        self.assertIn("sequence", art, "research artifact must carry D2C sequence")
        self.assertTrue(art.get("artifact_id"),
                        "research artifact must carry a content-addressed artifact_id")
        self.assertTrue(art.get("claim_digest"),
                        "research artifact must carry a claim_digest")
        self.assertTrue(art.get("statement_digest"),
                        "research artifact must carry a statement_digest")
        self.assertTrue(art.get("source_ref"),
                        "research artifact must carry a source_ref")
        if claim_id is not None:
            self.assertEqual(art.get("claim_id"), claim_id,
                             "research artifact claim_id must match")
        if outcome is not None:
            self.assertEqual(art.get("outcome"), outcome,
                             "research artifact outcome must match")
        if active is not None:
            self.assertEqual(art.get("active"), active,
                             "research artifact active must match")

    def assert_spike_request_artifact(self, art: dict, *, claim_id: str | None = None,
                                       command: str | None = None,
                                       dependent_files: list[str] | None = None,
                                       prerequisite_ids: list[str] | None = None) -> None:
        self.assertIsInstance(art, dict, "spike_request artifact must be a dict")
        self.assertEqual(art.get("kind"), "spike_request",
                         "spike_request artifact kind must be 'spike_request'")
        self.assertIn("sequence", art, "spike_request artifact must carry D2C sequence")
        self.assertTrue(art.get("harness_request_id"),
                        "spike_request must carry a server-generated nonempty harness_request_id")
        self.assertTrue(art.get("artifact_id"),
                        "spike_request must carry a content-addressed artifact_id")
        self.assertTrue(art.get("claim_digest"),
                        "spike_request must carry a claim_digest")
        self.assertTrue(art.get("command_digest"),
                        "spike_request must carry a command_digest")
        self.assertTrue(art.get("statement_digest"),
                        "spike_request must carry a statement_digest")
        self.assertTrue(art.get("dependent_files"),
                        "spike_request must carry exact nonempty dependent files")
        self.assertTrue(art.get("prerequisite_research_ids"),
                        "spike_request must carry ordered nonempty prerequisite research IDs")
        if claim_id is not None:
            self.assertEqual(art.get("claim_id"), claim_id,
                             "spike_request claim_id must match")
        if command is not None:
            self.assertEqual(art.get("command"), command,
                             "spike_request command must match")
        if dependent_files is not None:
            self.assertEqual(list(art.get("dependent_files", [])), list(dependent_files),
                             "spike_request dependent files must match exactly")
        if prerequisite_ids is not None:
            self.assertEqual(list(art.get("prerequisite_research_ids", [])),
                             list(prerequisite_ids),
                             "spike_request prerequisite research IDs must match in order")

    def assert_spike_result_artifact(self, art: dict, *, harness_request_id: str | None = None,
                                     claim_id: str | None = None, exit_code: int | None = None,
                                     spike_gate: str | None = None,
                                     supersedes: str | None = None,
                                     command: str | None = None,
                                     command_digest: str | None = None,
                                     prerequisite_ids: list[str] | None = None,
                                     file_bindings: list[dict] | None = None,
                                     active: bool | None = None) -> None:
        self.assertIsInstance(art, dict, "spike result artifact must be a dict")
        self.assertEqual(art.get("kind"), "spike",
                         "spike result artifact kind must be 'spike'")
        self.assertIn("sequence", art, "spike result artifact must carry D2C sequence")
        self.assertTrue(art.get("artifact_id"),
                        "spike result must carry a content-addressed artifact_id")
        self.assertTrue(art.get("claim_digest"),
                        "spike result must carry a claim_digest")
        self.assertTrue(art.get("statement_digest"),
                        "spike result must carry a statement_digest")
        self.assertTrue(art.get("command"), "spike result must carry the command")
        self.assertTrue(art.get("command_digest"),
                        "spike result must carry a command_digest")
        self.assertIsInstance(art.get("file_bindings"), list,
                              "spike result must carry file_bindings as a list")
        self.assertTrue(art.get("prerequisite_research_ids"),
                        "spike result must carry ordered nonempty prerequisite research IDs")
        if harness_request_id is not None:
            self.assertEqual(art.get("harness_request_id"), harness_request_id,
                             "spike result harness_request_id must match the request")
        if claim_id is not None:
            self.assertEqual(art.get("claim_id"), claim_id,
                             "spike result claim_id must match")
        if exit_code is not None:
            self.assertEqual(art.get("exit_code"), exit_code,
                             "spike result exit_code must match")
        if spike_gate is not None:
            self.assertEqual(art.get("spike_gate"), spike_gate,
                             "spike result spike_gate must match")
        if supersedes is not None:
            self.assertEqual(art.get("supersedes"), supersedes,
                             "spike result supersedes must match")
        if command is not None:
            self.assertEqual(art.get("command"), command,
                             "spike result command must match")
        if command_digest is not None:
            self.assertEqual(art.get("command_digest"), command_digest,
                             "spike result command_digest must match")
        if prerequisite_ids is not None:
            self.assertEqual(list(art.get("prerequisite_research_ids", [])),
                             list(prerequisite_ids),
                             "spike result prerequisite research IDs must match in order")
        if file_bindings is not None:
            self.assertEqual(list(art.get("file_bindings", [])), list(file_bindings),
                             "spike result file bindings must match exactly")
        if active is not None:
            self.assertEqual(art.get("active"), active,
                             "spike result active must match")

    def assert_artifact_sequence_order(self, arts, first_kind: str, second_kind: str) -> None:
        """Assert a ``first_kind`` artifact has a lower D2C ``sequence`` than a ``second_kind``
        artifact."""
        first = next((a for a in arts if isinstance(a, dict) and a.get("kind") == first_kind), None)
        second = next((a for a in arts if isinstance(a, dict) and a.get("kind") == second_kind), None)
        if first is None:
            raise HarnessDefect(f"no {first_kind!r} D2C artifact found (prerequisite)")
        if second is None:
            raise HarnessDefect(f"no {second_kind!r} D2C artifact found (prerequisite)")
        if first.get("sequence", -1) >= second.get("sequence", -1):
            raise HarnessDefect(
                f"{first_kind!r} (seq {first.get('sequence')}) must precede {second_kind!r} "
                f"(seq {second.get('sequence')}) in D2C sequence order")

    def get_argument_claim(self, drv, run_id: str, claim_id: str,
                           *, kind: str | None = None) -> dict:
        """Dispatch GetArgument and return the single claim item for ``claim_id``.

        When ``kind`` is given, assert the projected D2C claim carries that canonical kind.
        Always asserts the central ``evidence_digest`` invariant (D2C §10): the claim's
        ``evidence_digest`` must equal the canonical registry digest of its exact ordered
        ``active_evidence_ids`` (D4-S2 correction)."""
        resp = self.dispatch(drv, get_argument(run_id=run_id))
        arg = self.assert_argument_view(resp["result"])
        claims = [c for c in arg["claims"] if c["claim_id"] == claim_id]
        if not claims:
            raise HarnessDefect(f"claim {claim_id!r} not found in ArgumentView (prerequisite)")
        claim = claims[0]
        if kind is not None:
            self.assert_claim_kind(claim, kind)
        self.assert_evidence_digest(claim)
        return claim

    # ---- canonical graph/scenario helper (D4-S2/D2C) ----------------------

    def require_graph_admitted(self, drv, run_id: str, graph: dict | None = None) -> dict:
        """Submit a canonical selected graph with explicit root, ordered claims (id, text,
        gating) and edges, then prove admission through the typed ArgumentView claim/edge
        projection (D2C). Returns the graph payload. Raises HarnessDefect if not admitted."""
        payload = graph if graph is not None else canonical_graph(n_claims=1)
        resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_graph(payload=payload)))
        if resp["result"]["type"] != "Allow":
            raise HarnessDefect(
                f"a valid graph must be admitted (prerequisite); got {resp['result'].get('type')}")
        # Graph admission is proved by the typed ArgumentView claim/edge projection (D2C).
        arg_resp = self.dispatch(drv, get_argument(run_id=run_id))
        try:
            arg = self.assert_argument_view(arg_resp["result"])
        except AssertionError as exc:
            raise HarnessDefect(f"graph admission must project ArgumentView claims (prerequisite): {exc}") from exc
        root_id = payload["root"]
        claim = [c for c in arg["claims"] if c["claim_id"] == root_id]
        if not claim:
            raise HarnessDefect(f"graph admission must project root claim {root_id!r} (prerequisite)")
        # Assert the projected root claim carries the D2C kind from the graph payload.
        expected_kind = None
        for pc in payload.get("claims", []):
            if pc.get("id") == root_id:
                expected_kind = pc.get("kind")
                break
        if expected_kind is not None:
            self.assert_claim_kind(claim[0], expected_kind)
        return payload

    def start_run(self, drv, *, goal: str | None = None, **kw) -> str:
        """Start a run and return its run id. Asserts only the StartRun prerequisite (a valid
        admitted active run), not convergence/status policy, so cases reach their primary
        assertion."""
        resp = self.dispatch(drv, start_run(goal=goal or self.DEFAULT_GOAL, **kw))
        result = resp["result"]
        if result["type"] != "Allow":
            raise HarnessDefect(f"StartRun must admit an active run (prerequisite); got {result.get('type')}")
        run = result.get("run")
        if not (run and run.get("id")):
            raise HarnessDefect("StartRun must return a run id (prerequisite)")
        if run["status"] != "active":
            raise HarnessDefect(f"StartRun must admit an ACTIVE run (prerequisite); got status={run.get('status')!r}")
        return run["id"]

    def require_admitted_child(self, drv, run_id: str, *, execution: str = "foreground") -> str:
        """Reserve a child and return the SUT-admitted child_id (prerequisite: one child admitted).

        Install the minimal argument graph first because later lifecycle assertions inspect the
        typed ArgumentView; a missing selected graph must continue to fail closed.
        """
        argument = self.dispatch(drv, get_argument(run_id=run_id))
        if argument["result"]["type"] != "Allow":
            self.require_graph_admitted(drv, run_id)
            argument = self.dispatch(drv, get_argument(run_id=run_id))
        before = {child["child_id"] for child in argument["result"].get("run", {}).get("children", [])}
        resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_child_reserve(
            purpose="audit", role_profile=self.DEFAULT_PROFILE, execution=execution)))
        run = resp["result"].get("run", {})
        kids = [child for child in run.get("children", []) if child.get("child_id") not in before]
        if len(kids) != 1:
            raise HarnessDefect(
                f"child reserve must admit exactly one new child (prerequisite); got {len(kids)}")
        c = kids[0]
        if c.get("state") not in CHILD_STATES:
            raise HarnessDefect(f"admitted child must carry a canonical state (prerequisite); got {c.get('state')!r}")
        return c["child_id"]

    def require_child_state(self, drv, run_id: str, child_id: str, state: str) -> None:
        """Drive a child_event through trusted ingress and assert the child reached ``state``
        (prerequisite). Invokes ``trusted_child_event(run_id, child_id, payload)`` with a valid
        D2D closed payload and asserts the returned response contains the exact child state
        (D4-S3a-R correction: private ingress with accepted D2D payload, not public dispatch)."""
        native = None if state == "launch_rejected" else "n-pre"
        result_digest = None
        if state == "completed":
            result_digest = "sha256:" + hashlib.sha256(
                b"result:" + native.encode("utf-8")).hexdigest()
        payload = build_child_event_payload(
            state, native_id=native, result_digest=result_digest)
        resp = drv.trusted_child_event(run_id, child_id, payload)
        self.assert_valid_response(resp)
        try:
            c = self.assert_child_summary(resp["result"]["run"], child_id, state=state)
        except AssertionError as exc:
            raise HarnessDefect(f"child must reach state {state!r} (prerequisite): {exc}") from exc
        if state in CHILD_ADVERSE_TERMINAL and "recovery_action" not in c:
            raise HarnessDefect(f"child must reach adverse terminal {state!r} with a recovery action (prerequisite)")

    # ---- D4-S3a child-summary and operational-snapshot helpers --------------------
    # Flatten/index child summaries for exact lookup; require exact operational integer fields
    # (spawns_used, passes_used) without fallback defaults; snapshot complete public RunView
    # plus operational side-effect facts for before/after equality. The single child
    # index/assertion path is ``index_children`` + ``assert_child_summary`` (D4-S3a-R: removed
    # unused duplicate ``require_child_summary``).

    @staticmethod
    def index_children(run: dict) -> dict[str, dict]:
        """Index children by child_id for exact lookup (D4-S3a)."""
        return {c["child_id"]: c for c in run.get("children", [])}

    @staticmethod
    def require_operational_int(drv, field: str) -> int:
        """Require an exact operational integer field (``spawns_used``/``passes_used``) without
        a fallback default (D4-S3a). Raises HarnessDefect if the field is absent or not an int."""
        st = drv.operational_state()
        val = st.get(field)
        if not isinstance(val, int):
            raise HarnessDefect(
                f"operational_state must expose a present integer {field!r}; got {val!r} (prerequisite)")
        return int(val)

    def snapshot_run_state(self, drv, run_id: str) -> dict:
        """Capture the complete public RunView plus operational side-effect facts (D4-S3a).
        Returns a dict with ``run`` and ``operational`` keys for before/after equality."""
        resp = self.dispatch(drv, get_run(run_id=run_id))
        return {"run": resp["result"]["run"], "operational": drv.operational_state()}

    @staticmethod
    def assert_run_state_unchanged(before: dict, after: dict) -> None:
        """Assert the complete public RunView and operational fingerprint are unchanged."""
        assert isinstance(before, dict) and "run" in before and "operational" in before
        assert isinstance(after, dict) and "run" in after and "operational" in after
        if after["run"] != before["run"]:
            raise AssertionError("complete RunView must be unchanged")
        if after["operational"] != before["operational"]:
            raise AssertionError("operational fingerprint must be unchanged")

    # ---- D4-S3a audit setup helpers (D2C public provenance) ----------------------
    # Build an audit-ready frozen scope using public D2C: canonical graph with ordinary C0
    # and supporting research, freeze, optional deferred C1 (configurable kind), and typed
    # GetArgument dossier. Admit an audit child and drive reserved→launching→pending via
    # trusted ingress. Build an exact verdict payload from current public argument/claim
    # digests. Independence is never supplied by the verdict; it is derived from trusted
    # attribution ingress. Helpers assert prerequisites and expose SUT-admitted IDs only.

    def require_audit_scope(self, drv, run_id: str, *, deferred_kind: str | None = None) -> dict:
        """Build an audit-ready frozen scope (prerequisite) using public D2C.

        Admits a canonical graph with ordinary C0, records supporting research (C0 ordinary
        approved), freezes the scope, and optionally adds a deferred C1 with the given kind
        (``ordinary`` or ``needs-experiment``). Returns a dict with ``root_id``, ``c1_id``
        (or None), ``c0_artifact_id`` (the exact active supporting C0 artifact ID so
        attribution can bind its producer), and the typed GetArgument dossier. C1 added
        after freeze is explicitly ``gating=false``. Asserts prerequisites and exposes
        SUT-admitted IDs only."""
        graph = self.require_graph_admitted(
            drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        root_id = graph["root"]
        c0_artifact_id = self.require_research_recorded(drv, run_id, root_id)
        self.require_frozen_scope(drv, run_id)
        c1_id = None
        if deferred_kind is not None:
            two_claim = canonical_graph(n_claims=2, kind=deferred_kind)
            two_claim["claims"][0] = graph["claims"][0]
            c1_id = two_claim["claims"][1]["id"]
            self.dispatch(drv, observe_action(
                run_id=run_id, action=action_graph(payload=two_claim)))
        dossier = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        if c1_id is not None:
            c1_claims = [c for c in dossier["claims"] if c["claim_id"] == c1_id]
            if not c1_claims:
                raise HarnessDefect(
                    f"deferred C1 {c1_id!r} must appear in ArgumentView (prerequisite)")
            if c1_claims[0].get("gating"):
                raise HarnessDefect(
                    f"deferred C1 {c1_id!r} must be gating=false after freeze (prerequisite)")
        return {"root_id": root_id, "c1_id": c1_id,
                "c0_artifact_id": c0_artifact_id, "dossier": dossier}

    def require_pending_audit_child(self, drv, run_id: str) -> str:
        """Reserve an audit child and drive it to ``pending`` via trusted ingress
        (reserved→launching→pending). Returns the SUT-admitted child_id (never fabricated).
        Raises HarnessDefect if the child is not admitted or cannot reach pending."""
        child_id = self.require_admitted_child(drv, run_id, execution="foreground")
        self.require_child_state(drv, run_id, child_id, "launching")
        self.require_child_state(drv, run_id, child_id, "pending")
        return child_id

    def require_trusted_audit_attribution(self, drv, run_id: str, child_id: str,
                                          c0_artifact_id: str | list[str], *,
                                          variant: str) -> None:
        """Submit trusted covered-actor and auditor attribution through private ingress
        (D4-S3a-R).

        Covered-actor attribution is bound to exact active approved C0 artifact IDs.
        Auditor attribution is bound to the pending SUT-admitted audit child. Same-model
        uses equal normalized provider/model pairs; decorrelated uses distinct pairs;
        unverified makes one pair null. Every trusted response is validated/asserted.
        Raises HarnessDefect if any response is not valid."""
        covered_observer = auditor_observer = "host"
        if variant == "same_model":
            covered_provider, covered_model = "p1", "m1"
            auditor_provider, auditor_model = "p1", "m1"
        elif variant == "decorrelated":
            covered_provider, covered_model = "p1", "m1"
            auditor_provider, auditor_model = "p2", "m2"
        elif variant == "unverified":
            covered_provider, covered_model = "p1", "m1"
            auditor_provider, auditor_model = None, None
        elif variant == "alias":
            covered_provider, covered_model = "p1", "m1"
            auditor_provider, auditor_model = "p2", "opus"
        elif variant == "configuration":
            covered_provider, covered_model = "p1", "m1"
            auditor_provider, auditor_model = "p2", "m2"
            auditor_observer = "configuration"
        else:
            raise HarnessDefect(f"unknown attribution variant {variant!r}")
        # Covered-actor attribution: bound to exact active approved C0 artifact IDs.
        covered_ids = ([c0_artifact_id] if isinstance(c0_artifact_id, str)
                       else list(c0_artifact_id))
        covered_payload = build_attribution_payload(
            subject_kind="covered_actor", subject_id="covered-actor-1",
            child_id=None, provider_id=covered_provider, model_id=covered_model,
            observed_by=covered_observer, covered_artifact_ids=covered_ids)
        resp_covered = drv.trusted_attribution(run_id, covered_payload)
        self.assert_valid_response(resp_covered)
        # Auditor attribution: bound to pending SUT-admitted audit child.
        auditor_payload = build_attribution_payload(
            subject_kind="auditor", subject_id="auditor-1",
            child_id=child_id, provider_id=auditor_provider, model_id=auditor_model,
            observed_by=auditor_observer, covered_artifact_ids=[])
        resp_auditor = drv.trusted_attribution(run_id, auditor_payload)
        self.assert_valid_response(resp_auditor)

    def build_audit_verdict_payload(self, drv, run_id: str, *, verdict: str,
                                    scope_review: str | None = None,
                                    findings: list[str] | None = None) -> dict:
        """Build an exact audit verdict payload from current public argument/claim digests.

        Uses canonical field ``reviewed_claims``: exact current approved gating claims in
        ArgumentView order. Deferred claims never appear in ``reviewed_claims``; they are
        bound only by ``deferred_scope_digest`` plus ``scope_review`` and public residual
        claim IDs. Always includes the current argument/goal/frozen/deferred aggregate tuple
        and ``scope_review`` relation (null when ``frozen_scope_digest`` is null).
        Independence is never included in the verdict payload (it is derived from trusted
        attribution ingress). No deferred reviewed-claim or identity field."""
        arg = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        frozen = arg.get("frozen_scope_digest")
        if frozen is None:
            sr: str | None = None
        else:
            if scope_review is None:
                raise HarnessDefect(
                    "scope_review is required (pass|fail) when frozen_scope_digest is "
                    "non-null (prerequisite)")
            sr = scope_review
        payload: dict = {
            "verdict": verdict,
            "findings": list(findings) if findings is not None else ["audit complete"],
            "argument_digest": arg["argument_digest"],
            "goal_digest": arg["goal_digest"],
            "frozen_scope_digest": frozen,
            "deferred_scope_digest": arg["deferred_scope_digest"],
            "reviewed_claims": [],
            "scope_review": sr,
        }
        for claim in arg["claims"]:
            if claim.get("gating") and claim.get("state") == "approved":
                payload["reviewed_claims"].append({
                    "claim_id": claim["claim_id"],
                    "evidence_digest": claim.get("evidence_digest"),
                })
        return payload

    def assert_audit_view(self, arg: dict, *, state: str | None = None,
                           independence: str | None = None) -> dict:
        """Assert the typed ArgumentView carries an audit view with the expected state and
        independence. Returns the audit dict for further exact assertions."""
        audit = arg.get("audit")
        if not isinstance(audit, dict):
            raise self.failureException("ArgumentView must carry a typed audit view (D2C)")
        if state is not None:
            self.assertEqual(audit.get("state"), state,
                             f"audit.state must be {state!r}; got {audit.get('state')!r}")
        if independence is not None:
            self.assertEqual(audit.get("independence"), independence,
                             f"audit.independence must be {independence!r}; "
                             f"got {audit.get('independence')!r}")
        return audit

    @staticmethod
    def _obligation_index(run: dict) -> dict:
        """Index active+deferred obligations by id -> full row dict (D4-S2 correction)."""
        obs = run.get("obligations", {})
        idx: dict = {}
        for o in obs.get("active", []) + obs.get("deferred", []):
            oid = o.get("id")
            if oid is not None:
                idx[oid] = o
        return idx

    @staticmethod
    def _changed_obligation_ids(before: dict, after: dict) -> set:
        """Return obligation IDs added OR changed (full row differs) from before to after
        (D4-S2 correction). Compares full obligation rows, not just IDs, across active+deferred."""
        changed: set = set()
        for oid, after_row in after.items():
            before_row = before.get(oid)
            if before_row is None or before_row != after_row:
                changed.add(oid)
        return changed

    def require_route_admitted(self, drv, run_id: str, reason: str = "primary-claim") -> dict:
        """Assert a route witness was admitted (prerequisite). Returns the response so callers
        can inspect the returned RunView."""
        resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_route(reason=reason)))
        if resp["result"]["type"] != "Allow":
            raise HarnessDefect(f"a valid route must be admitted (prerequisite); got {resp['result'].get('type')}")
        return resp

    def require_investigate_admitted(self, drv, run_id: str) -> None:
        """Assert investigation was admitted (Allow) through derived RunView obligation state.
        Route and investigation witnesses are proved by derived RunView obligation statuses, not
        private artifact IDs (D2C)."""
        resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_investigate()))
        if resp["result"]["type"] != "Allow":
            raise HarnessDefect(
                f"investigation must be admitted (prerequisite); got {resp['result'].get('type')}")
        self.assert_allow(resp, converged=False)

    def require_research_recorded(self, drv, run_id: str, claim_id: str,
                                  result: str = "supports") -> str:
        """Assert supporting research was recorded for the claim and return the typed research
        artifact ID from the D2C ``ArgumentView.artifacts`` union. Validates claim/outcome on the
        typed artifact (D4-S2/D2C correction)."""
        self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
            claim_id=claim_id, source_kind="code", result=result)))
        arts = self.get_argument_artifacts(drv, run_id)
        outcome = "supporting" if result == "supports" else "refuting"
        research = self.find_argument_artifacts(arts, kind="research", claim_id=claim_id,
                                                 active=True, outcome=outcome)
        if not research:
            raise HarnessDefect(
                f"supporting research must append a typed active research artifact for {claim_id!r} (prerequisite)")
        art = research[-1]  # latest matching
        self.assert_research_artifact(art, claim_id=claim_id, outcome=outcome, active=True)
        artifact_id = art.get("artifact_id")
        if not artifact_id:
            raise HarnessDefect(
                "research artifact must carry a content-addressed artifact_id (prerequisite)")
        return artifact_id

    def require_approved_spike(self, drv, run_id: str, claim_id: str, dependent_files: list[str],
                               *, content: bytes = b"v1", exit_code: int = 0) -> tuple[dict, dict]:
        """Stage harness completion BEFORE the spike_request (D4-S2), dispatch the request, then
        use ONE GetArgument response to find the typed request and result artifacts from the D2C
        ``ArgumentView.artifacts`` union and return them. For a passing exit, require the claim
        state ``approved`` with matching spike evidence and freshness. Asserts no
        freshness.changes path intersects dependent_files. Must NOT claim approval after
        observing only a request artifact (D4-S2/D2C correction)."""
        for p in dependent_files:
            drv.workspace_write(p, content)
        # Stage harness completion BEFORE dispatching spike_request (D4-S2 spec).
        drv.harness_complete("python -m pytest", exit_code)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_spike_request(
            claim_id=claim_id, command="python -m pytest", dependent_files=dependent_files)))
        # Use ONE GetArgument response for artifacts, claim state, and freshness (D4-S2
        # correction): no second GetArgument dispatch.
        resp = self.dispatch(drv, get_argument(run_id=run_id))
        try:
            arg = self.assert_argument_view(resp["result"])
        except AssertionError as exc:
            raise HarnessDefect(
                f"GetArgument must return a typed argument (prerequisite): {exc}") from exc
        arts = arg.get("artifacts")
        if not isinstance(arts, list):
            raise HarnessDefect("ArgumentView.artifacts must be a list (prerequisite)")
        req_arts = self.find_argument_artifacts(arts, kind="spike_request", claim_id=claim_id)
        res_arts = self.find_argument_artifacts(arts, kind="spike", claim_id=claim_id)
        if not req_arts:
            raise HarnessDefect("the sealed spike_request must be in ArgumentView.artifacts (prerequisite)")
        if not res_arts:
            raise HarnessDefect(
                "the spike result must be in ArgumentView.artifacts after the request (prerequisite)")
        req_art = req_arts[-1]  # latest
        res_art = res_arts[-1]  # latest
        req_id = req_art.get("harness_request_id")
        res_id = res_art.get("harness_request_id")
        if not req_id:
            raise HarnessDefect(
                "spike_request must carry a server-generated harness_request_id (prerequisite)")
        if req_id != res_id:
            raise HarnessDefect(
                "spike result harness_request_id must match the request (prerequisite)")
        # Assert no freshness.changes path intersects dependent_files (D4-S2 correction):
        # a freshly approved spike over current observations must not report stale freshness.
        run = resp["result"].get("run", {})
        changed_paths = {c.get("path") for c in run.get("freshness", {}).get("changes", [])}
        for dep in dependent_files:
            self.assertNotIn(dep, changed_paths,
                             f"a freshly approved spike must not report stale freshness for "
                             f"dependent file {dep!r} (prerequisite)")
        # Must NOT claim approval after observing only a request artifact.
        if exit_code == 0:
            claims = [c for c in arg["claims"] if c["claim_id"] == claim_id]
            if not claims:
                raise HarnessDefect(f"claim {claim_id!r} not found in ArgumentView (prerequisite)")
            claim = claims[0]
            if claim["state"] != "approved":
                raise HarnessDefect(
                    f"claim {claim_id!r} must be approved (prerequisite); "
                    f"got {claim['state']!r}")
            # Assert the spike result is active and passing in the complete active set.
            self.assert_spike_result_artifact(res_art, claim_id=claim_id,
                                               exit_code=exit_code, spike_gate="pass",
                                               active=True)
            # Central evidence_digest assertion (D4-S2 correction).
            self.assert_evidence_digest(claim)
        return req_art, res_art

    def require_frozen_scope(self, drv, run_id: str) -> None:
        """Assert freeze committed a scope (prerequisite)."""
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        st = drv.operational_state()
        if not st.get("frozen_scope"):
            raise HarnessDefect("freeze must commit a scope (prerequisite)")

    # ---- selector seam (D4 spec §8) ----------------------------------------
    # Cases 37–40 expose the real pure D9 selector with ONLY canonical operation_context, ordered
    # reason codes, and terminal status inputs — they do not infer context by manufacturing domain
    # state. The driver delegates to ``sut_adapter.bind_selector`` (raises V2SeamAbsent when the D9
    # selector module is absent) so cases 37–40 fail at the seam rather than aborting collection.

    def select_sections(self, drv, operation_context: str, reason_codes: list[str],
                        terminal_status: str | None) -> list[str]:
        """Invoke the D9 context selector through the driver's selector seam (D4 spec §8)."""
        if operation_context not in OPERATION_CONTEXTS:
            raise HarnessDefect(f"operation_context {operation_context!r} is not canonical")
        out = drv.select_sections(operation_context, list(reason_codes), terminal_status)
        if not isinstance(out, list):
            raise HarnessDefect(f"selector must return a list of section ids; got {type(out).__name__}")
        return out

    # ---- typed-view assertions (D2A §3, §4) --------------------------------

    def assert_freshness_has_path(self, run: dict, path: str, *, state: str | None = None) -> None:
        """Assert the run's freshness.changes observes ``path`` (per-path workspace observation)."""
        changes = run.get("freshness", {}).get("changes", [])
        match = [c for c in changes if c.get("path") == path]
        if not match:
            raise self.failureException(f"freshness.changes must observe bound path {path!r}; got {changes}")
        if state is not None:
            self.assertEqual(match[0].get("state"), state,
                             f"freshness change for {path!r} must report state {state!r}")

    def assert_evidence_digest(self, claim: dict) -> None:
        """Assert claim ``evidence_digest`` equals the canonical registry digest of its exact
        ordered ``active_evidence_ids`` (D2C invariant §10). This is the single central assertion
        that replaces per-case self-comparison."""
        active_ids = claim.get("active_evidence_ids")
        if not isinstance(active_ids, list):
            raise self.failureException(
                "claim must carry active_evidence_ids as a list (D2C)")
        expected = active_set_digest(active_ids)
        self.assertEqual(claim.get("evidence_digest"), expected,
                         "evidence_digest must equal the canonical registry digest of "
                         "active_evidence_ids")

    def assert_argument_view(self, result: dict) -> dict:
        """Assert a GetArgument Allow carries the typed ArgumentView (D2C). Requires ``artifacts``
        and rejects the removed ``evidence`` field."""
        if result["type"] != "Allow":
            raise self.failureException(f"GetArgument must Allow with an argument; got {result.get('type')}")
        if "argument" not in result:
            raise self.failureException("GetArgument Allow must carry the typed argument (D2C)")
        arg = result["argument"]
        for key in ("root_claim_id", "argument_digest", "goal_digest", "frozen_scope_digest",
                    "deferred_scope_digest", "untrusted_delimiters", "claims", "edges",
                    "artifacts", "audit"):
            if key not in arg:
                raise self.failureException(f"ArgumentView must carry {key!r} (D2C)")
        self.assertNotIn("evidence", arg,
                         "ArgumentView must not carry the removed 'evidence' field (D2C)")
        self.assertEqual(arg["untrusted_delimiters"], {"open": UNTRUSTED_OPEN, "close": UNTRUSTED_CLOSE},
                         "ArgumentView delimiters must equal the canonical PublicContract values")
        return arg

    def assert_contract_result(self, resp: dict, target: str, *, section_id: str | None = None) -> dict:
        """Assert a GetContract response carries the exact requested projection + canonical
        identity/digest, and no sibling target payload (D4-S3b case 41). Strengthened to assert
        the contract_result digest equals the canonical registry digest."""
        cr = resp["result"].get("contract_result")
        if cr is None:
            raise self.failureException("GetContract must return a contract_result projection")
        self.assertEqual(cr["target"], target, "contract_result target must match the request")
        self.assertEqual(cr.get("digest"), _CONTRACT_DIGEST,
                         "contract_result digest must equal the canonical registry digest")
        if target == "index":
            self.assertEqual(cr["index"]["id"], _CONTRACT_ID)
            self.assertEqual(cr["index"]["version"], _CONTRACT_VERSION)
            self.assertEqual({s["id"] for s in cr["index"]["sections"]}, set(SECTIONS),
                             "GetContract index must project exactly the canonical sections")
            self.assertNotIn("full", cr, "index projection must not carry a sibling full payload")
            self.assertNotIn("section", cr, "index projection must not carry a sibling section payload")
        elif target == "section":
            self.assertEqual(cr["section_id"], section_id)
            self.assertEqual(cr["section"]["id"], section_id)
            self.assertIn(section_id, SECTIONS)
            self.assertNotIn("index", cr, "section projection must not carry a sibling index payload")
            self.assertNotIn("full", cr, "section projection must not carry a sibling full payload")
        elif target == "full":
            full = cr["full"]
            self.assertEqual(full["id"], _CONTRACT_ID)
            self.assertEqual(full["version"], _CONTRACT_VERSION)
            self.assertNotIn("index", cr, "full projection must not carry a sibling index payload")
            self.assertNotIn("section_id", cr, "full projection must not carry a sibling section payload")
        return cr
