"""D6 strict-v2 regression tests for the ``application.protocol``/``application.run_state`` seam.

These cases were introduced red-first on the pre-D6-B tree. They now execute against the shipped
strict-v2 modules and preserve the original failure-specific assertions as regressions.

Coverage (D6 spec section 10):

* raw request variants: null/empty/v1/future/partial protocol, None/{}/non-object, unknown/extra
  top-level/command/action fields;
* every claimed-valid command/action sample validates against request.schema BEFORE production is
  imported; trusted samples come from accepted observe fixtures;
* response self-validation fallback is forced through ``dispatch_request(raw, handler)`` with an
  injected malformed handler, counted exactly one call, and is exact v2 ``unavailable``/closed,
  schema-valid, correlated, and nonrecursive;
* persisted-state classification: exact valid current v2 or one corrupt category; valid roundtrip
  via ``classify_and_decode`` and ``encode_state``;
* strict state roundtrip (no defaults) using the committed valid-active fixture;
* invalid child/counters/stamps/duplicates/NaN/Inf deadlines;
* rejected persisted-state GetRun/RestoreRun/EvaluateRun through the composed service and a
  recording repository: exact canonical corrupt Blocks, fixed safe fields, canaries absent, zero writes;
* valid-current StartRun/GetRun/RestoreRun exact unsupported/closed;
* compact exactly ``{"status":"unsupported"}``, operational_state exactly ``{}``, reload distinct
  with equivalent behavior over the same recording ports.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import jsonschema

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

_PLUGIN_ROOT = _REPO_ROOT / "plugins" / "empirica"
sys.path.insert(0, str(_PLUGIN_ROOT))

_V2 = _REPO_ROOT / "contracts" / "empirica" / "v2"
_PUBLIC_CONTRACT = json.loads((_V2 / "public-contract.json").read_text(encoding="utf-8"))
_REQUEST_SCHEMA = json.loads((_V2 / "request.schema.json").read_text(encoding="utf-8"))
_RESPONSE_SCHEMA = json.loads((_V2 / "response.schema.json").read_text(encoding="utf-8"))
_HOST_PROFILES = json.loads((_V2 / "host-profiles.json").read_text(encoding="utf-8"))

# Load the committed valid-active state fixture (no duplicate of fixture in test code).
_VALID_ACTIVE_STATE = json.loads(
    (_V2 / "state-fixtures" / "valid-active.json").read_text(encoding="utf-8"))

# Canonical failure-safe projection for every rejected persisted aggregate.
_BLOCK_CORRUPT_FIXTURE = json.loads(
    (_V2 / "fixtures" / "block-corrupt-state.json").read_text(encoding="utf-8"))

_PROTOCOL = _PUBLIC_CONTRACT["protocol"]
_COMMANDS = list(_PUBLIC_CONTRACT["commands"])
_AUTHOR_ACTIONS = list(_PUBLIC_CONTRACT["actions"]["author"])
_TRUSTED_ACTIONS = list(_PUBLIC_CONTRACT["actions"]["trusted"])
_STATUSES = list(_PUBLIC_CONTRACT["statuses"])
_CHILD_STATES = list(_PUBLIC_CONTRACT["child_lifecycle"]["states"])
_CHILD_TERMINAL = list(_PUBLIC_CONTRACT["child_lifecycle"]["terminal_states"])
_PROFILES = {p["profile_id"]: p for p in _HOST_PROFILES["profiles"]}
_DEFAULT_PROFILE = "claude-code@2.1.270"

# --- Accepted observe fixture requests for the four trusted actions ---
_TRUSTED_FIXTURE_REQUESTS: list[dict] = []
for _fx_name in ("observe-evidence-leaf", "observe-attribution-covered-actor",
                 "observe-child-event-redacted", "observe-audit-verdict"):
    _fx = json.loads((_V2 / "fixtures" / f"{_fx_name}.json").read_text(encoding="utf-8"))
    _TRUSTED_FIXTURE_REQUESTS.append(_fx["request"])


class V2ModuleAbsent(AssertionError):
    """Raised when a future v2 production module is absent (pre-D6-B tree)."""


def _import_protocol():
    try:
        return __import__("application.protocol", fromlist=["protocol"])
    except ModuleNotFoundError as exc:
        raise V2ModuleAbsent(
            "application.protocol module absent: D6-B must introduce the strict v2 request "
            "protocol (dispatch_request(raw, handler), raw validation, command/action dispatch "
            "discrimination, response self-validation fallback). " + str(exc)) from exc


def _import_run_state():
    try:
        return __import__("application.run_state", fromlist=["run_state"])
    except ModuleNotFoundError as exc:
        raise V2ModuleAbsent(
            "application.run_state module absent: D6-B must introduce the strict v2 state "
            "codec (classify_and_decode returning classification with kind + state.encode()). "
            + str(exc)) from exc


def _import_application_v2():
    try:
        return __import__("application.v2", fromlist=["v2"])
    except ModuleNotFoundError as exc:
        raise V2ModuleAbsent(
            "application.v2 module absent: D6-B must introduce the minimal v2 service "
            "compose() factory. " + str(exc)) from exc


def _digest64(c: str = "0") -> str:
    return "sha256:" + c * 64


def _valid_request(command: dict, request_id: str = "r") -> dict:
    return {"protocol": _PROTOCOL, "request_id": request_id, "command": command}


# ---------------------------------------------------------------------------
# Recording run repository (D4-shaped: read returns object with value/revision;
# create/CAS counted; inject for test setup).
# ---------------------------------------------------------------------------


class _Absent:
    pass


_ABSENT = _Absent()


class _Present:
    __slots__ = ("value", "revision")

    def __init__(self, value, revision):
        self.value = value
        self.revision = revision


class RecordingRunRepository:
    """A recording run repository shaped like the D4 FakeRunRepository.

    ``read`` returns ``_ABSENT`` or ``_Present(value, revision)``. ``create`` and
    ``compare_and_set`` are counted so tests assert zero writes on old/corrupt state.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple] = {}
        self._rev_counter = 0
        self.create_calls = 0
        self.cas_calls = 0

    def _mint_rev(self) -> str:
        self._rev_counter += 1
        return f"rev-{self._rev_counter}"

    def read(self, key):
        entry = self._store.get(key)
        if entry is None:
            return _ABSENT
        return _Present(entry[0], entry[1])

    def create(self, key, value):
        self.create_calls += 1
        if key in self._store:
            raise ValueError(f"key {key!r} already exists")
        rev = self._mint_rev()
        self._store[key] = (value, rev)
        return rev

    def compare_and_set(self, key, value, expected):
        self.cas_calls += 1
        entry = self._store.get(key)
        if entry is None:
            raise ValueError(f"key {key!r} absent")
        _, rev = entry
        if rev != expected:
            raise ValueError(f"stale revision for {key!r}")
        new = self._mint_rev()
        self._store[key] = (value, new)
        return new

    def inject(self, key, value):
        rev = self._mint_rev()
        self._store[key] = (value, rev)
        return rev


def _expected_corrupt_block(run_id: str, goal: str = "Unsupported run state.",
                            request_id: str = "r") -> dict:
    """Build the exact canonical Block for any rejected persisted aggregate."""
    import copy
    block = copy.deepcopy(_BLOCK_CORRUPT_FIXTURE["expected"])
    block["request_id"] = request_id
    block["result"]["run"]["id"] = run_id
    block["result"]["run"]["goal"] = goal
    return block


class D6ProtocolPrevalidation(unittest.TestCase):
    """Pre-validation: schema-validate every claimed-valid sample BEFORE importing production.

    These tests are GREEN and do not import production modules; they prove the test's own
    sample envelopes are valid against the accepted request schema so that a red failure
    is attributable to the absent module, not an invalid test sample.
    """

    def test_all_command_samples_validate_request_schema(self):
        """Every command discriminator sample validates against request.schema.json."""
        for cmd_type in _COMMANDS:
            with self.subTest(command=cmd_type):
                env = _valid_request(_raw_command_of(cmd_type))
                jsonschema.validate(instance=env, schema=_REQUEST_SCHEMA)

    def test_all_author_action_samples_validate_request_schema(self):
        """Every author action sample validates; research provenance is mandatory."""
        for action_kind in _AUTHOR_ACTIONS:
            with self.subTest(action=action_kind):
                action = _build_action_sample(action_kind)
                env = _valid_request({"type": "ObserveAction", "run_id": "r1",
                                       "action": action})
                jsonschema.validate(instance=env, schema=_REQUEST_SCHEMA)
        complete = _build_action_sample("research")
        for label, payload in (
            ("missing_payload", None),
            ("missing_source_ref", {"citation": "Observed line."}),
            ("missing_citation", {"source_ref": "src/example.py:1"}),
            ("empty_source_ref", {"source_ref": "", "citation": "Observed line."}),
            ("empty_citation", {"source_ref": "src/example.py:1", "citation": ""}),
        ):
            with self.subTest(research=label):
                action = dict(complete)
                if payload is None:
                    action.pop("payload")
                else:
                    action["payload"] = payload
                env = _valid_request({"type": "ObserveAction", "run_id": "r1",
                                       "action": action})
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(instance=env, schema=_REQUEST_SCHEMA)

    def test_trusted_fixture_requests_validate_request_schema(self):
        """The four accepted observe-fixture requests (trusted actions) validate against
        the request schema."""
        for fx_req in _TRUSTED_FIXTURE_REQUESTS:
            with self.subTest(request_id=fx_req.get("request_id")):
                jsonschema.validate(instance=fx_req, schema=_REQUEST_SCHEMA)

    def test_malformed_response_rejected_by_response_schema(self):
        """A malformed response is rejected by the response schema (proves the fallback
        target is schema-valid and the malformed handler response is NOT)."""
        malformed = {"protocol": _PROTOCOL, "request_id": "x",
                     "result": {"type": "NotARealType"}}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=malformed, schema=_RESPONSE_SCHEMA)

    def test_committed_valid_active_state_loads(self):
        """The committed valid-active state fixture loads and has exact identity."""
        self.assertEqual(_VALID_ACTIVE_STATE["protocol"], _PROTOCOL)
        self.assertEqual(_VALID_ACTIVE_STATE["state_schema"], "empirica.run/2")
        self.assertEqual(_VALID_ACTIVE_STATE["status"], "active")
        self.assertTrue(_VALID_ACTIVE_STATE["children"])

    def test_expected_corrupt_block_validates_response_schema(self):
        """The canonical rejected-state Block validates independently of production."""
        corrupt_block = _expected_corrupt_block("r-corrupt")
        jsonschema.validate(instance=corrupt_block, schema=_RESPONSE_SCHEMA)
        # Verify corrupt parameters is an instance, not a schema object.
        corrupt_reason = corrupt_block["result"]["reasons"][0]
        self.assertEqual(corrupt_reason["parameters"], {},
                         "corrupt reason parameters must be {} (valid instance), "
                         "not the params schema")
        self.assertEqual(corrupt_reason["code"], "run.corrupt")


def _raw_command_of(cmd_type: str, run_id: str = "r1") -> dict:
    if cmd_type == "StartRun":
        return {"type": "StartRun", "selector": {"project": "p", "session": "s"}, "goal": "g"}
    if cmd_type == "ResolveRun":
        return {"type": "ResolveRun", "selector": {"project": "p", "session": "s"}}
    if cmd_type == "GetRun":
        return {"type": "GetRun", "run_id": run_id}
    if cmd_type == "GetArgument":
        return {"type": "GetArgument", "run_id": run_id}
    if cmd_type == "GetContract":
        return {"type": "GetContract", "target": "index"}
    if cmd_type == "RestoreRun":
        return {"type": "RestoreRun", "run_id": run_id}
    if cmd_type == "EvaluateRun":
        return {"type": "EvaluateRun", "run_id": run_id, "intent": "continue"}
    if cmd_type == "ObserveAction":
        return {"type": "ObserveAction", "run_id": run_id, "action": {"kind": "graph"}}
    raise ValueError(f"unknown command {cmd_type!r}")


def _build_action_sample(kind: str) -> dict:
    if kind == "graph":
        return {"kind": "graph"}
    if kind == "research":
        return {"kind": "research", "claim_id": "C0", "source_kind": "code",
                "result": "supports", "payload": {
                    "source_ref": "src/example.py:1", "citation": "Observed line."}}
    if kind == "spike_request":
        return {"kind": "spike_request", "claim_id": "C0", "command": "pytest",
                "dependent_files": ["f.py"]}
    if kind == "configure_run":
        return {"kind": "configure_run", "budgets": {"max_passes": 1}}
    if kind == "route":
        return {"kind": "route", "reason": "primary"}
    if kind == "investigate":
        return {"kind": "investigate"}
    if kind == "freeze":
        return {"kind": "freeze"}
    if kind == "dispatch":
        return {"kind": "dispatch", "target": "claim"}
    if kind == "child_reserve":
        return {"kind": "child_reserve", "purpose": "audit",
                "role_profile": _DEFAULT_PROFILE, "execution": "foreground"}
    raise ValueError(f"no sample for {kind!r}")


class D6StrictProtocolTests(unittest.TestCase):
    """D6-A red strict tests for ``application.protocol`` (future module).

    The frozen protocol seam is ``dispatch_request(raw, handler)``: it validates the raw
    value, calls ``handler`` only with a valid discriminated envelope, validates the handler
    response, and applies the response fallback.
    """

    GOAL = "Prove strict v2 request protocol validation and dispatch."

    def test_raw_request_protocol_variants_fault_closed(self):
        """raw request null/empty/v1/future/partial protocol → exact Fault invalid_request/closed."""
        mod = _import_protocol()
        for proto in (None, "", "empirica/v1", "empirica/v3", "empirica/v2-rc", "empirica/v"):
            with self.subTest(protocol=proto):
                env = {"protocol": proto, "request_id": "probe",
                       "command": {"type": "GetContract", "target": "index"}}
                resp = mod.dispatch_request(env, lambda envelope: {"bad": "handler"})
                self.assert_fault_closed(resp, "invalid_request")

    def test_raw_non_object_and_empty_fault_closed(self):
        """None, {}, and non-object raw → exact Fault invalid_request/closed."""
        mod = _import_protocol()
        for raw in (None, {}, "not-an-object", 42, [], ""):
            with self.subTest(raw=raw):
                resp = mod.dispatch_request(raw, lambda envelope: {"bad": "handler"})
                self.assert_fault_closed(resp, "invalid_request")

    def test_unknown_top_level_field_fault_closed(self):
        mod = _import_protocol()
        env = _valid_request({"type": "GetRun", "run_id": "r1"})
        env["unknown_top_field"] = True
        resp = mod.dispatch_request(env, lambda envelope: {"bad": "handler"})
        self.assert_fault_closed(resp, "invalid_request")

    def test_unknown_command_field_fault_closed(self):
        mod = _import_protocol()
        env = _valid_request({"type": "GetRun", "run_id": "r1", "unknown_cmd_field": True})
        resp = mod.dispatch_request(env, lambda envelope: {"bad": "handler"})
        self.assert_fault_closed(resp, "invalid_request")

    def test_unknown_action_kind_fault_closed(self):
        mod = _import_protocol()
        env = _valid_request({"type": "ObserveAction", "run_id": "r1",
                              "action": {"kind": "not_a_real_action"}})
        resp = mod.dispatch_request(env, lambda envelope: {"bad": "handler"})
        self.assert_fault_closed(resp, "invalid_request")

    def test_unknown_action_field_fault_closed(self):
        mod = _import_protocol()
        env = _valid_request({"type": "ObserveAction", "run_id": "r1",
                              "action": {"kind": "graph", "unknown_action_field": True}})
        resp = mod.dispatch_request(env, lambda envelope: {"bad": "handler"})
        self.assert_fault_closed(resp, "invalid_request")

    def test_every_command_discriminator_passes_validation(self):
        """Every valid command discriminator sample must pass validation and reach the
        handler (not be a Fault invalid_request)."""
        mod = _import_protocol()
        for cmd_type in _COMMANDS:
            with self.subTest(command=cmd_type):
                env = _valid_request(_raw_command_of(cmd_type))
                calls = []
                def handler(envelope, _cmd=cmd_type):
                    calls.append(envelope)
                    return {"protocol": _PROTOCOL, "request_id": envelope["request_id"],
                            "result": {"type": "Fault", "code": "unsupported",
                                       "fail_direction": "closed"}}
                resp = mod.dispatch_request(env, handler)
                jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
                self.assertEqual(len(calls), 1,
                                 f"handler must be called exactly once for {cmd_type!r}")
                self.assertNotEqual(
                    resp["result"].get("code"), "invalid_request",
                    f"command {cmd_type!r} valid discriminator must pass, not invalid_request")

    def test_every_author_action_discriminator_passes_validation(self):
        """Every valid author action sample must pass validation and reach the handler."""
        mod = _import_protocol()
        for action_kind in _AUTHOR_ACTIONS:
            with self.subTest(action=action_kind):
                action = _build_action_sample(action_kind)
                env = _valid_request({"type": "ObserveAction", "run_id": "r1",
                                       "action": action})
                calls = []
                def handler(envelope):
                    calls.append(envelope)
                    return {"protocol": _PROTOCOL, "request_id": envelope["request_id"],
                            "result": {"type": "Fault", "code": "unsupported",
                                       "fail_direction": "closed"}}
                resp = mod.dispatch_request(env, handler)
                jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
                self.assertEqual(len(calls), 1,
                                 f"handler must be called exactly once for {action_kind!r}")
                self.assertNotEqual(
                    resp["result"].get("code"), "invalid_request",
                    f"action {action_kind!r} valid discriminator must pass, not invalid_request")

    def test_trusted_fixture_actions_pass_validation(self):
        """The four accepted observe-fixture trusted-action requests pass validation and
        reach the handler."""
        mod = _import_protocol()
        for fx_req in _TRUSTED_FIXTURE_REQUESTS:
            with self.subTest(request_id=fx_req.get("request_id")):
                calls = []
                def handler(envelope):
                    calls.append(envelope)
                    return {"protocol": _PROTOCOL, "request_id": envelope["request_id"],
                            "result": {"type": "Fault", "code": "unsupported",
                                       "fail_direction": "closed"}}
                resp = mod.dispatch_request(dict(fx_req), handler)
                jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
                self.assertEqual(len(calls), 1, "handler must be called exactly once")

    def test_malformed_handler_response_fallback_unavailable_closed(self):
        """An internally malformed handler response becomes exact schema-valid
        unavailable/closed, correlated to the request, without recursive validation loops.

        The handler is called exactly once; the fallback is exact v2 unavailable/closed and
        schema-valid.
        """
        mod = _import_protocol()
        env = _valid_request({"type": "GetRun", "run_id": "r1"}, request_id="fb-1")
        calls = []

        def malformed_handler(envelope):
            calls.append(envelope)
            return {"protocol": _PROTOCOL, "request_id": envelope["request_id"],
                    "result": {"type": "NotARealType"}}

        resp = mod.dispatch_request(env, malformed_handler)
        self.assertEqual(len(calls), 1, "handler must be called exactly once")
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        result = resp["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "unavailable")
        self.assertEqual(result["fail_direction"], "closed")
        self.assertEqual(resp.get("protocol"), _PROTOCOL,
                         "fallback envelope must speak v2")
        self.assertEqual(resp.get("request_id"), "fb-1",
                         "fallback must correlate to the supplied valid request ID")

    def test_malformed_handler_no_recursion(self):
        """The malformed-handler fallback does not recurse: the handler is called exactly
        once even if its response is deeply malformed."""
        mod = _import_protocol()
        env = _valid_request({"type": "GetRun", "run_id": "r1"}, request_id="fb-2")
        calls = []

        def deeply_malformed(envelope):
            calls.append(envelope)
            return "not-even-a-dict"

        resp = mod.dispatch_request(env, deeply_malformed)
        self.assertEqual(len(calls), 1, "handler must be called exactly once (no recursion)")
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        self.assertEqual(resp["result"]["code"], "unavailable")
        self.assertEqual(resp["result"]["fail_direction"], "closed")

    def test_handler_wrong_request_id_fallback_unavailable_closed(self):
        """A handler that returns a schema-valid response whose request_id does not match
        the request request_id becomes exact schema-valid unavailable/closed, correlated to
        the request, without recursive validation loops. The handler is called exactly once."""
        mod = _import_protocol()
        env = _valid_request({"type": "GetRun", "run_id": "r1"}, request_id="rid-match")
        calls = []

        def wrong_id_handler(envelope):
            calls.append(envelope)
            return {"protocol": _PROTOCOL, "request_id": "wrong-id",
                    "result": {"type": "Fault", "code": "unsupported",
                                "fail_direction": "closed"}}

        resp = mod.dispatch_request(env, wrong_id_handler)
        self.assertEqual(len(calls), 1, "handler must be called exactly once")
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        result = resp["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "unavailable")
        self.assertEqual(result["fail_direction"], "closed")
        self.assertEqual(resp.get("protocol"), _PROTOCOL, "fallback must speak v2")
        self.assertEqual(resp.get("request_id"), "rid-match",
                         "mismatch fallback must correlate to the request request_id")

    def assert_fault_closed(self, resp: dict, code: str | None = None) -> None:
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        result = resp["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["fail_direction"], "closed")
        if code is not None:
            self.assertEqual(result["code"], code)


class D6StrictRunStateTests(unittest.TestCase):
    """D6-A red strict tests for ``application.run_state`` (future module).

    The frozen codec seam is ``classify_and_decode(raw)`` returning a typed immutable
    classification with ``kind`` (``current_corrupt|valid``) and, only when valid, an immutable
    ``state`` whose ``encode()`` reproduces the exact closed document without defaults.
    """

    GOAL = "Prove strict v2 state identity classification and codec."

    def test_every_noncurrent_identity_is_corrupt(self):
        """Missing/null/empty/v1/future/unknown persisted identity is corrupt, never compatible."""
        mod = _import_run_state()
        for raw in (
            {"status": "active"},
            {"protocol": None, "status": "active"},
            {"protocol": "", "status": "active"},
            {"protocol": "empirica/v1", "status": "active"},
            {"protocol": "empirica/v3", "status": "active"},
            {"protocol": "empirica/unknown", "status": "active"},
        ):
            with self.subTest(raw=raw):
                classification = mod.classify_and_decode(raw)
                self.assertEqual(classification.kind, "current_corrupt",
                                 f"raw {raw!r} must classify current_corrupt")

    def test_identity_classification_current_corrupt_wrong_schema(self):
        """exact protocol==empirica/v2 + missing/wrong state_schema → current-corrupt."""
        mod = _import_run_state()
        for raw in (
            {"protocol": "empirica/v2", "status": "active"},
            {"protocol": "empirica/v2", "state_schema": "empirica.run/1", "status": "active"},
            {"protocol": "empirica/v2", "state_schema": "wrong", "status": "active"},
        ):
            with self.subTest(raw=raw):
                classification = mod.classify_and_decode(raw)
                self.assertEqual(classification.kind, "current_corrupt",
                                 f"raw {raw!r} must classify current_corrupt")

    def test_identity_classification_current_corrupt_invalid_state(self):
        """exact protocol + exact schema + invalid state → current-corrupt."""
        mod = _import_run_state()
        bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
        bad["status"] = "not_a_status"
        classification = mod.classify_and_decode(bad)
        self.assertEqual(classification.kind, "current_corrupt")

    def test_identity_classification_current_corrupt_non_object(self):
        """non-object raw → current-corrupt (not old/unsupported)."""
        mod = _import_run_state()
        for raw in ("not-json", None, 42, "[]", ""):
            with self.subTest(raw=raw):
                classification = mod.classify_and_decode(raw)
                self.assertEqual(classification.kind, "current_corrupt",
                                 f"non-object {raw!r} must classify current_corrupt")

    def test_identity_classification_valid_returns_encodeable_state(self):
        """exact protocol + exact schema + valid state → valid classification with an
        immutable state whose encode() reproduces the exact closed document."""
        mod = _import_run_state()
        classification = mod.classify_and_decode(_VALID_ACTIVE_STATE)
        self.assertEqual(classification.kind, "valid")
        self.assertIsNotNone(classification.state,
                            "valid classification must carry an immutable state")
        encoded = mod.encode_state(classification.state)
        self.assertEqual(encoded, _VALID_ACTIVE_STATE,
                         "state.encode() must reproduce the exact closed document with no defaults")

    def test_strict_state_roundtrip_no_defaults(self):
        """Valid committed fixture roundtrips through classify_and_decode → encode with no
        field defaults added."""
        mod = _import_run_state()
        classification = mod.classify_and_decode(_VALID_ACTIVE_STATE)
        self.assertEqual(classification.kind, "valid")
        self.assertEqual(mod.encode_state(classification.state), _VALID_ACTIVE_STATE,
                         "roundtrip must not add/remove/default any field")

    def test_invalid_child_duplicate_id_rejected(self):
        mod = _import_run_state()
        bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
        bad["children"].append(dict(bad["children"][0]))
        classification = mod.classify_and_decode(bad)
        self.assertEqual(classification.kind, "current_corrupt")

    def test_invalid_counter_overflow_rejected(self):
        mod = _import_run_state()
        bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
        bad["budgets"]["passes_used"] = bad["budgets"]["max_passes"] + 5
        classification = mod.classify_and_decode(bad)
        self.assertEqual(classification.kind, "current_corrupt")

    def test_invalid_stamp_overflow_rejected(self):
        mod = _import_run_state()
        bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
        bad["route_stamp"] = bad["stamp_seq"] + 10
        classification = mod.classify_and_decode(bad)
        self.assertEqual(classification.kind, "current_corrupt")

    def test_invalid_child_branch_rejected(self):
        """reserved child with spent=true is a schema violation → current-corrupt."""
        mod = _import_run_state()
        bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
        bad["children"][0]["spent"] = True  # reserved requires spent=false
        classification = mod.classify_and_decode(bad)
        self.assertEqual(classification.kind, "current_corrupt")

    def test_invalid_refund_mismatch_rejected(self):
        """refunded=true on a non-launch_rejected state → current-corrupt."""
        mod = _import_run_state()
        bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
        bad["children"][0]["refunded"] = True  # reserved requires refunded=false
        classification = mod.classify_and_decode(bad)
        self.assertEqual(classification.kind, "current_corrupt")

    def test_invalid_nan_deadline_rejected(self):
        """NaN deadline → current-corrupt."""
        mod = _import_run_state()
        bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
        bad["children"][0]["state"] = "launching"
        bad["children"][0]["spent"] = True
        bad["children"][0]["native_id"] = "n1"
        bad["children"][0]["deadline"] = float("nan")
        classification = mod.classify_and_decode(bad)
        self.assertEqual(classification.kind, "current_corrupt")

    def test_invalid_inf_deadline_rejected(self):
        """+Inf and -Inf deadline → current-corrupt."""
        mod = _import_run_state()
        for inf_val in (float("inf"), float("-inf")):
            with self.subTest(deadline=inf_val):
                bad = json.loads(json.dumps(_VALID_ACTIVE_STATE))
                bad["children"][0]["state"] = "launching"
                bad["children"][0]["spent"] = True
                bad["children"][0]["native_id"] = "n1"
                bad["children"][0]["deadline"] = inf_val
                classification = mod.classify_and_decode(bad)
                self.assertEqual(classification.kind, "current_corrupt")

    def test_classify_does_not_mutate_input(self):
        """classify_and_decode must not mutate the input raw state."""
        mod = _import_run_state()
        old = {"protocol": "empirica/v1", "status": "active", "goal": "legacy"}
        snapshot = json.loads(json.dumps(old))
        mod.classify_and_decode(old)
        self.assertEqual(old, snapshot, "classify_and_decode must not mutate rejected state")


class D6MinimalServiceTests(unittest.TestCase):
    """D6-A red strict tests for ``application.v2.compose`` (future module).

    Tests use a recording run repository (D4-shaped: read returns object with value/revision;
    create/CAS counted) and dispatch through the composed service, not direct codec helpers.
    """

    GOAL = "Prove minimal v2 service unsupported/failure-safe behavior at D6."

    def _compose(self, runs=None):
        mod = _import_application_v2()
        runs = runs or RecordingRunRepository()
        service = mod.compose(workspace=None, harness=None, runs=runs, artifacts=None,
                              host=None, profile_id=_DEFAULT_PROFILE, limits=None, clock=None)
        return service, runs

    # ---- valid-current unsupported ----

    def test_start_run_unsupported(self):
        """StartRun returns exact schema-valid unsupported/closed (owner stage D7)."""
        service, _ = self._compose()
        resp = service.dispatch(_valid_request(
            {"type": "StartRun", "selector": {"project": "p", "session": "s"}, "goal": "g"}))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        result = resp["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "unsupported")
        self.assertEqual(result["fail_direction"], "closed")

    def test_valid_current_get_run_unsupported(self):
        """Valid-current GetRun (no seeded state) returns exact unsupported/closed."""
        service, runs = self._compose()
        # Seed a valid current state so the run exists but is valid-current, not old/corrupt.
        runs.inject("r-valid", _VALID_ACTIVE_STATE)
        resp = service.dispatch(_valid_request({"type": "GetRun", "run_id": "r-valid"}))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        result = resp["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "unsupported")
        self.assertEqual(result["fail_direction"], "closed")

    def test_valid_current_restore_run_unsupported(self):
        """Valid-current RestoreRun (seeded valid state) returns exact unsupported/closed."""
        service, runs = self._compose()
        runs.inject("r-valid", _VALID_ACTIVE_STATE)
        resp = service.dispatch(_valid_request({"type": "RestoreRun", "run_id": "r-valid"}))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        result = resp["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "unsupported")
        self.assertEqual(result["fail_direction"], "closed")

    # ---- rejected persisted state failure-safe projection through service dispatch ----

    def test_noncurrent_state_get_run_is_corrupt(self):
        """Noncurrent GetRun returns run.corrupt with fixed safe fields, no canary, zero writes."""
        runs = RecordingRunRepository()
        canary = "CANARY_OLD_GET_001"
        old_state = {"protocol": "empirica/v1", "status": "converged", "goal": "A legacy goal.",
                     canary: "hostile-payload", "children": [{"x": 1}]}
        runs.inject("r-old", old_state)
        service, runs = self._compose(runs)
        resp = service.dispatch(_valid_request({"type": "GetRun", "run_id": "r-old"},
                                                request_id="r"))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        expected = _expected_corrupt_block("r-old", "Unsupported run state.")
        self.assertEqual(resp, expected,
                         "noncurrent GetRun must return the exact canonical corrupt Block")
        # canary must be absent from the response
        self.assertNotIn(canary, json.dumps(resp),
                         "injected canary must not appear in the failure-safe projection")
        # zero writes
        self.assertEqual(runs.create_calls, 0, "rejected-state read must perform zero creates")
        self.assertEqual(runs.cas_calls, 0, "rejected-state read must perform zero CAS writes")

    def test_noncurrent_state_restore_run_is_corrupt(self):
        """Noncurrent RestoreRun returns run.corrupt with no semantic reuse or writes."""
        runs = RecordingRunRepository()
        canary = "CANARY_OLD_RESTORE_002"
        old_state = {"protocol": "empirica/v1", "status": "converged", "goal": "A legacy goal.",
                     canary: "hostile", "budgets": {"max_passes": 99}}
        runs.inject("r-old", old_state)
        service, runs = self._compose(runs)
        resp = service.dispatch(_valid_request({"type": "RestoreRun", "run_id": "r-old"},
                                                request_id="r"))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        expected = _expected_corrupt_block("r-old", "Unsupported run state.")
        self.assertEqual(resp, expected)
        self.assertNotIn(canary, json.dumps(resp))
        self.assertEqual(runs.create_calls, 0)
        self.assertEqual(runs.cas_calls, 0)

    def test_noncurrent_state_evaluate_run_is_corrupt(self):
        """Noncurrent EvaluateRun returns run.corrupt with no semantic reuse or writes."""
        runs = RecordingRunRepository()
        canary = "CANARY_OLD_EVAL_003"
        old_state = {"protocol": "empirica/v1", "status": "active", "goal": "Legacy.",
                     canary: "hostile"}
        runs.inject("r-old", old_state)
        service, runs = self._compose(runs)
        resp = service.dispatch(_valid_request(
            {"type": "EvaluateRun", "run_id": "r-old", "intent": "report_convergence"},
            request_id="r"))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        expected = _expected_corrupt_block("r-old", "Unsupported run state.")
        self.assertEqual(resp, expected)
        self.assertNotIn(canary, json.dumps(resp))
        self.assertEqual(runs.create_calls, 0)
        self.assertEqual(runs.cas_calls, 0)

    def test_corrupt_state_get_run_failure_safe_block(self):
        """Corrupt-state GetRun (exact v2 identity + invalid state) returns exact failure-safe
        run.corrupt Block; canaries absent; zero writes."""
        runs = RecordingRunRepository()
        canary = "CANARY_CORRUPT_GET_004"
        corrupt = {"protocol": _PROTOCOL, "state_schema": "empirica.run/2",
                   "goal": 12345, "status": "not_a_status", canary: "hostile"}
        runs.inject("r-corrupt", corrupt)
        service, runs = self._compose(runs)
        resp = service.dispatch(_valid_request({"type": "GetRun", "run_id": "r-corrupt"},
                                                request_id="r"))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        expected = _expected_corrupt_block("r-corrupt", "Unsupported run state.")
        self.assertEqual(resp, expected,
                         "corrupt-state GetRun must return the exact canonical failure-safe Block")
        self.assertNotIn(canary, json.dumps(resp))
        self.assertEqual(runs.create_calls, 0)
        self.assertEqual(runs.cas_calls, 0)

    def test_corrupt_state_restore_run_failure_safe_block(self):
        """Corrupt-state RestoreRun returns exact failure-safe run.corrupt Block; zero writes."""
        runs = RecordingRunRepository()
        canary = "CANARY_CORRUPT_RESTORE_005"
        corrupt = {"protocol": _PROTOCOL, "status": "active", canary: "hostile"}
        runs.inject("r-corrupt", corrupt)
        service, runs = self._compose(runs)
        resp = service.dispatch(_valid_request({"type": "RestoreRun", "run_id": "r-corrupt"},
                                                request_id="r"))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        expected = _expected_corrupt_block("r-corrupt", "Unsupported run state.")
        self.assertEqual(resp, expected)
        self.assertNotIn(canary, json.dumps(resp))
        self.assertEqual(runs.create_calls, 0)
        self.assertEqual(runs.cas_calls, 0)

    def test_corrupt_state_evaluate_run_failure_safe_block(self):
        """Corrupt-state EvaluateRun returns exact failure-safe run.corrupt Block; zero writes."""
        runs = RecordingRunRepository()
        canary = "CANARY_CORRUPT_EVAL_006"
        corrupt = {"protocol": _PROTOCOL, "state_schema": "empirica.run/2",
                   "goal": "ok", "status": "not_a_status", canary: "hostile"}
        runs.inject("r-corrupt", corrupt)
        service, runs = self._compose(runs)
        resp = service.dispatch(_valid_request(
            {"type": "EvaluateRun", "run_id": "r-corrupt", "intent": "report_convergence"},
            request_id="r"))
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)
        # goal is already a string ("ok"); fixed safe text applies only when goal is
        # missing or non-string, so the expected goal is the raw goal itself.
        expected = _expected_corrupt_block("r-corrupt")
        self.assertEqual(resp, expected)
        self.assertNotIn(canary, json.dumps(resp))
        self.assertEqual(runs.create_calls, 0)
        self.assertEqual(runs.cas_calls, 0)

    # ---- compact / operational_state / reload ----

    def test_compact_exact_unsupported(self):
        """compact returns exactly {"status": "unsupported"}."""
        service, _ = self._compose()
        result = service.compact()
        self.assertEqual(result, {"status": "unsupported"},
                         "compact must return exactly {'status': 'unsupported'}")

    def test_operational_state_exact_empty(self):
        """operational_state returns exactly {}."""
        service, _ = self._compose()
        state = service.operational_state()
        self.assertEqual(state, {}, "operational_state must return exactly {}")

    def test_reload_distinct_equivalent_behavior(self):
        """reload returns a distinct shell with equivalent behavior over the same recording
        ports."""
        runs = RecordingRunRepository()
        runs.inject("r-old", {"protocol": "empirica/v1", "status": "active", "goal": "legacy"})
        service, runs = self._compose(runs)
        reloaded = service.reload()
        self.assertIsNot(reloaded, service, "reload must return a DISTINCT shell")
        # equivalent behavior: the reloaded shell over the same recording ports produces the
        # same corrupt-state Block for the same rejected persisted run
        resp1 = service.dispatch(_valid_request({"type": "GetRun", "run_id": "r-old"},
                                                 request_id="r"))
        resp2 = reloaded.dispatch(_valid_request({"type": "GetRun", "run_id": "r-old"},
                                                  request_id="r"))
        jsonschema.validate(instance=resp1, schema=_RESPONSE_SCHEMA)
        jsonschema.validate(instance=resp2, schema=_RESPONSE_SCHEMA)
        self.assertEqual(resp1, resp2,
                         "reloaded shell must produce equivalent behavior over the same ports")
        # both use the same recording repo (zero writes)
        self.assertEqual(runs.create_calls, 0)


if __name__ == "__main__":
    unittest.main()
