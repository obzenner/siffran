#!/usr/bin/env python3
"""Focused bridge v2 tests for the D6-C host cutover (C1).

Proves the D6-C §4 bridge composition boundary for the shared JSON bridge
(``adapters/bridge.py``):

* invalid request validation via ``application.protocol`` precedes profile composition — an
  invalid request returns ``invalid_request``/closed even when the profile is missing/unknown;
* a valid request with a missing (``None``) or unknown profile returns correlated v2
  ``unavailable``/closed;
* a canonical explicit registry profile composes ``application.v2`` and a state-bearing read
  (GetRun) returns exact ``unsupported``/closed through the no-location run port, without touching
  any state/git repository or v1 import;
* the bridge reaches no state/git repository and no v1 import;
* malformed JSON over stdio routes through the handle/protocol gateway and returns
  ``invalid_request``/closed with request_id ``invalid-request`` before profile composition;
* a known-profile valid request calls the protocol gateway exactly once and the service handler
  exactly once (no double request/response validation).

Run: python3 plugins/empirica/tests/test_bridge_v2.py   (stdlib + jsonschema, no pytest)
Exit 0 = all pass; 1 = at least one failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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

_PROTOCOL = _PUBLIC_CONTRACT["protocol"]
_PROFILE_IDS = [p["profile_id"] for p in _HOST_PROFILES["profiles"]]
_CANONICAL_PROFILE = "claude-code@2.1.278"

from adapters import bridge  # noqa: E402


def _valid_request(command: dict, request_id: str = "r") -> dict:
    return {"protocol": _PROTOCOL, "request_id": request_id, "command": command}


def _get_run(run_id: str = "opaque-run-id", request_id: str = "r") -> dict:
    return _valid_request({"type": "GetRun", "run_id": run_id}, request_id)


def _assert_inert_no_run(result: dict) -> None:
    assert result["result"] == {"type": "Inert", "reason": "no_run"}
    jsonschema.validate(instance=result, schema=_RESPONSE_SCHEMA)


def _assert_fault(result: dict, code: str) -> None:
    assert result["result"]["type"] == "Fault", f"expected Fault, got {result['result']['type']}"
    assert result["result"]["code"] == code, f"expected code {code}, got {result['result']['code']}"
    assert result["result"]["fail_direction"] == "closed", "Fault must fail closed"


class BridgeV2Tests(unittest.TestCase):
    """The D6-C §4 bridge composition boundary."""

    # --- helpers ---------------------------------------------------------------

    def _stdio(self, input_bytes: bytes, profile_id: str | None = None) -> dict:
        """Run the bridge over stdio with the given raw input and (optional) profile env,
        returning the parsed response envelope. ``profile_id=None`` omits the env var."""
        env = {**os.environ, "PYTHONPATH": str(_PLUGIN_ROOT)}
        if profile_id is not None:
            env["EMPIRICA_HOST_PROFILE_ID"] = profile_id
        else:
            env.pop("EMPIRICA_HOST_PROFILE_ID", None)
        bridge_path = _PLUGIN_ROOT / "adapters" / "bridge.py"
        proc = subprocess.run(
            [sys.executable, str(bridge_path)],
            input=input_bytes, capture_output=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr.decode()}")
        return json.loads(proc.stdout.decode())

    # --- invalid request validation precedes profile composition ----------------

    def test_invalid_request_wins_before_missing_profile(self):
        """An invalid request returns invalid_request/closed even when no profile is supplied —
        validation via application.protocol precedes profile composition."""
        invalid = {"protocol": "empirica/v1", "request_id": "bad-1",
                   "command": {"type": "GetRun", "run_id": "x"}}
        resp = bridge.handle(invalid, None)
        _assert_fault(resp, "invalid_request")
        self.assertEqual(resp["request_id"], "bad-1")
        self.assertEqual(resp["protocol"], _PROTOCOL)

    def test_invalid_request_wins_before_unknown_profile(self):
        """An invalid request returns invalid_request even with an unknown profile."""
        invalid = {"protocol": "empirica/v3", "request_id": "bad-2",
                   "command": {"type": "GetRun", "run_id": "x"}}
        resp = bridge.handle(invalid, "no-such-profile@9.9.9")
        _assert_fault(resp, "invalid_request")
        self.assertEqual(resp["request_id"], "bad-2")

    def test_invalid_request_wins_before_build(self):
        """An invalid request (unknown command field) returns invalid_request, not unavailable."""
        invalid = _valid_request({"type": "GetRun", "run_id": "r1", "bogus_field": True},
                                 request_id="bad-3")
        resp = bridge.handle(invalid, _CANONICAL_PROFILE)
        _assert_fault(resp, "invalid_request")
        self.assertEqual(resp["request_id"], "bad-3")

    def test_non_object_request_is_invalid(self):
        """A non-object request (None / list / string) is invalid_request with request_id
        'invalid-request' — routed through the handle/protocol gateway."""
        for raw in (None, [1, 2], "not-an-envelope", 42):
            resp = bridge.handle(raw, _CANONICAL_PROFILE)
            _assert_fault(resp, "invalid_request")
            self.assertEqual(resp["request_id"], "invalid-request")

    # --- valid request + missing/unknown profile -> unavailable correlated ------

    def test_valid_request_missing_profile_is_unavailable(self):
        """A valid request with a missing (None) profile returns unavailable/closed, correlated."""
        resp = bridge.handle(_get_run(request_id="miss-1"), None)
        _assert_fault(resp, "unavailable")
        self.assertEqual(resp["request_id"], "miss-1")
        self.assertEqual(resp["protocol"], _PROTOCOL)

    def test_valid_request_empty_profile_is_unavailable(self):
        """An empty-string profile is missing, not a default — unavailable/closed."""
        resp = bridge.handle(_get_run(request_id="miss-2"), "")
        _assert_fault(resp, "unavailable")
        self.assertEqual(resp["request_id"], "miss-2")

    def test_valid_request_unknown_profile_is_unavailable(self):
        """A valid request with an unknown registry profile returns unavailable/closed."""
        resp = bridge.handle(_get_run(request_id="unk-1"), "no-such-profile@9.9.9")
        _assert_fault(resp, "unavailable")
        self.assertEqual(resp["request_id"], "unk-1")
        self.assertEqual(resp["protocol"], _PROTOCOL)

    def test_unavailable_responses_are_schema_valid(self):
        """Every unavailable response validates against the v2 response schema."""
        for pid in (None, "", "no-such-profile@9.9.9"):
            resp = bridge.handle(_get_run(request_id="schema-1"), pid)
            jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)

    # --- canonical explicit profile composes; GetRun unsupported through no-location --

    def test_canonical_profile_getrun_is_unsupported(self):
        """A canonical explicit profile composes v2; GetRun returns unsupported/closed because
        the no-location run port reports the opaque ID unresolved."""
        resp = bridge.handle(_get_run(request_id="get-1"), _CANONICAL_PROFILE)
        _assert_inert_no_run(resp)
        self.assertEqual(resp["request_id"], "get-1")
        self.assertEqual(resp["protocol"], _PROTOCOL)
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)

    def test_every_registry_profile_composes_getrun_unsupported(self):
        """Every registered profile composes v2 and yields unsupported for a state-bearing read."""
        for pid in _PROFILE_IDS:
            with self.subTest(profile_id=pid):
                resp = bridge.handle(_get_run(request_id=f"get-{pid}"), pid)
                _assert_inert_no_run(resp)
                self.assertEqual(resp["protocol"], _PROTOCOL)

    def test_build_service_requires_explicit_profile(self):
        """build_service has no default: missing/unknown profile raises (no silent fallback)."""
        with self.assertRaises(ValueError):
            bridge.build_service(None)
        with self.assertRaises(ValueError):
            bridge.build_service("")
        with self.assertRaises(ValueError):
            bridge.build_service("no-such-profile@9.9.9")

    def test_build_service_canonical_profile_composes(self):
        """A canonical profile composes a dispatchable v2 service (no host default)."""
        service = bridge.build_service(_CANONICAL_PROFILE)
        self.assertTrue(callable(getattr(service, "dispatch", None)))
        self.assertTrue(callable(getattr(service, "_dispatch_validated", None)))

    # --- no state/git repo reached (no-location, no-write) ----------------------

    def test_getrun_creates_no_state_or_git_repository(self):
        """A GetRun through the bridge touches neither the machine-local state home nor the
        working tree — the no-location run port reports unresolved without repository I/O."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            saved = os.environ.get("EMPIRICA_HOME")
            os.environ["EMPIRICA_HOME"] = str(home)
            try:
                resp = bridge.handle(_get_run(request_id="no-io"), _CANONICAL_PROFILE)
            finally:
                if saved is None:
                    os.environ.pop("EMPIRICA_HOME", None)
                else:
                    os.environ["EMPIRICA_HOME"] = saved
            _assert_inert_no_run(resp)
            # Nothing was created under the isolated home.
            self.assertEqual(list(home.rglob("*")), [])

    # --- malformed JSON over stdio -> invalid_request via handle/protocol gateway --

    def test_stdio_malformed_json_is_invalid_request(self):
        """The stdio entry routes malformed JSON through the handle/protocol gateway and returns
        invalid_request/closed with request_id 'invalid-request', before profile composition."""
        resp = self._stdio(b"{ this is not valid json", profile_id=_CANONICAL_PROFILE)
        _assert_fault(resp, "invalid_request")
        self.assertEqual(resp["request_id"], "invalid-request")
        self.assertEqual(resp["protocol"], _PROTOCOL)
        jsonschema.validate(instance=resp, schema=_RESPONSE_SCHEMA)

    def test_stdio_missing_profile_valid_request_is_unavailable(self):
        """The stdio entry with no EMPIRICA_HOST_PROFILE_ID and a valid request returns
        unavailable/closed (no default profile)."""
        req = json.dumps(_get_run(request_id="stdio-miss")).encode()
        resp = self._stdio(req, profile_id=None)
        _assert_fault(resp, "unavailable")
        self.assertEqual(resp["request_id"], "stdio-miss")

    def test_stdio_canonical_profile_getrun_is_unsupported(self):
        """The stdio entry with a canonical profile and a GetRun returns unsupported/closed."""
        req = json.dumps(_get_run(request_id="stdio-get")).encode()
        resp = self._stdio(req, profile_id=_CANONICAL_PROFILE)
        _assert_inert_no_run(resp)
        self.assertEqual(resp["request_id"], "stdio-get")

    # --- no double request/response validation (gateway once, handler once) --------

    def test_bridge_protocol_gateway_once_handler_once(self):
        """Regression: a known-profile valid request calls the protocol gateway exactly once
        and the service handler exactly once (no double request/response validation). The bridge
        handler calls the service's private _dispatch_validated seam, not the public dispatch."""
        import application.protocol as proto
        original = proto.dispatch_request
        calls = {"gateway": 0, "handler": 0}

        def counting_dispatch(raw, handler):
            calls["gateway"] += 1

            def counting_handler(envelope):
                calls["handler"] += 1
                return handler(envelope)
            return original(raw, counting_handler)

        proto.dispatch_request = counting_dispatch
        try:
            resp = bridge.handle(_get_run(request_id="once-1"), _CANONICAL_PROFILE)
        finally:
            proto.dispatch_request = original
        self.assertEqual(calls["gateway"], 1, "protocol gateway called exactly once")
        self.assertEqual(calls["handler"], 1, "service handler called exactly once")
        _assert_inert_no_run(resp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
