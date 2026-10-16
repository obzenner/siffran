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

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
_CANONICAL_PROFILE = next(p["profile_id"] for p in _HOST_PROFILES["profiles"] if p["host_id"] == "claude-code")

from sandbox_isolation import (  # noqa: E402
    ABSENT_SANDBOX_HOME, force_absent_sandbox_home)

from adapters import bridge  # noqa: E402
from adapters.sandbox import selection  # noqa: E402
from governance_setup import invocation_for  # noqa: E402
force_absent_sandbox_home()


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

    def test_start_refusal_uses_message_code_and_final_fallback(self) -> None:
        self.assertEqual(bridge.start_refusal({"type": "Block", "reasons": [
            {"code": "run.goal_required", "message": "supply goal"}]}), "supply goal")
        self.assertEqual(bridge.start_refusal({"type": "Block", "reasons": [
            {"code": "run.goal_required"}]}), "run.goal_required")
        self.assertEqual(bridge.start_refusal({"type": "Block", "reasons": [{}]}),
                         "start refused")
        from adapters.claude.transport import Result
        typed = Result.from_mapping({"type": "Block", "reasons": [{
            "code": "run.goal_required", "parameters": {}, "next_actions": [], "sections": [],
            "message": "remedy"}]})
        self.assertEqual(bridge.start_refusal(typed), "remedy")
        self.assertIsNone(bridge.start_refusal({"type": "Block", "run": {},
                                                "reasons": [{"code": "other"}]}))

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

    def test_bridge_test_modules_are_isolated_from_developer_sandbox_authority(self):
        bridge_tests = (
            _PLUGIN_ROOT / "tests/test_bridge_v2.py",
            _PLUGIN_ROOT / "tests/test_host_runtime.py",
            _PLUGIN_ROOT / "tests/test_governance_hosts.py",
            _PLUGIN_ROOT / "tests/test_context_economy.py",
            _PLUGIN_ROOT / "tests/test_observer.py",
            _PLUGIN_ROOT / "tests/v2/driver.py",
            _PLUGIN_ROOT / "tests/v2/fs_store.py",
            _PLUGIN_ROOT / "tests/v2/observer_store.py",
            _PLUGIN_ROOT / "tests/v2/test_timeline.py",
            _PLUGIN_ROOT / "adapters/claude/tests/test_claude_adapter.py",
            _PLUGIN_ROOT / "adapters/claude/tests/test_activation_lifecycle.py",
            _PLUGIN_ROOT / "adapters/codex/tests/test_codex_adapter.py",
        )
        for path in bridge_tests:
            source = path.read_text(encoding="utf-8")
            self.assertIn("from sandbox_isolation import", source, path)
            self.assertIn("force_absent_sandbox_home()", source, path)

        test_roots = (
            _PLUGIN_ROOT / "tests",
            _PLUGIN_ROOT / "adapters/claude/tests",
            _PLUGIN_ROOT / "adapters/codex/tests",
        )
        for root in test_roots:
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                aliases = {
                    alias.asname or alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module == "adapters"
                    for alias in node.names if alias.name == "bridge"
                }
                reaches_bridge = any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"build_service", "handle"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in aliases
                    for node in ast.walk(tree)
                )
                if reaches_bridge:
                    self.assertIn(path, bridge_tests, path)
                    source = path.read_text(encoding="utf-8")
                    self.assertIn("from sandbox_isolation import", source, path)
                    self.assertIn("force_absent_sandbox_home()", source, path)
        excluded_semantic_tests = (
            _PLUGIN_ROOT / "tests/test_sandbox_adapter.py",
            _REPO_ROOT / "scripts/tests/test_sandbox_qualification.py",
        )
        for path in excluded_semantic_tests:
            self.assertNotIn(
                "from sandbox_isolation import", path.read_text(encoding="utf-8"), path)
        self.assertEqual(Path(os.environ["EMPIRICA_SANDBOX_HOME"]), ABSENT_SANDBOX_HOME)
        self.assertFalse(ABSENT_SANDBOX_HOME.exists())

    def test_build_service_composes_one_shared_store_selector_and_docker_harness(self):
        store, selector, harness, service = object(), object(), object(), object()
        with (patch.object(bridge, "QualificationStore", return_value=store) as store_type,
              patch.object(
                  bridge, "QualificationAuthoritySelector", return_value=selector) as selector_type,
              patch.object(bridge, "DockerSpikeHarness", return_value=harness) as harness_type,
              patch.object(bridge._v2, "compose", return_value=service) as compose):
            self.assertIs(bridge.build_service(_CANONICAL_PROFILE), service)
        store_type.assert_called_once_with()
        selector_type.assert_called_once_with(store)
        harness_type.assert_called_once_with(store)
        self.assertIs(compose.call_args.kwargs["harness"], harness)
        self.assertIs(compose.call_args.kwargs["spike_authority_selector"], selector)

    def test_sandbox_constructor_failure_degrades_only_spikes(self):
        for failing in ("QualificationStore", "QualificationAuthoritySelector", "DockerSpikeHarness"):
            with self.subTest(failing=failing), \
                    patch.object(bridge, "QualificationStore", return_value=object()) as store, \
                    patch.object(
                        bridge, "QualificationAuthoritySelector", return_value=object()) as selector, \
                    patch.object(bridge, "DockerSpikeHarness", return_value=object()) as harness:
                {"QualificationStore": store, "QualificationAuthoritySelector": selector,
                 "DockerSpikeHarness": harness}[failing].side_effect = PermissionError("unsafe")
                service = bridge.build_service(_CANONICAL_PROFILE)
            from application.ports import NullSpikeAuthoritySelector, UnavailableSpikeHarness
            self.assertIsInstance(service._harness, UnavailableSpikeHarness)
            self.assertIsInstance(
                service._coordinator.spike_authority_selector, NullSpikeAuthoritySelector)
            response = service.dispatch(_valid_request({"type": "GetContract", "target": "index"}))
            self.assertEqual(response["result"]["type"], "Allow", response)

    def test_bridge_construction_performs_no_docker_discovery(self):
        with patch.object(
                bridge.QualificationAuthoritySelector, "select",
                side_effect=AssertionError("selection is StartRun-only")) as selected:
            service = bridge.build_service(_CANONICAL_PROFILE)
        self.assertTrue(callable(service.dispatch))
        selected.assert_not_called()

    def test_start_run_with_absent_authority_binds_unavailable_without_docker_reads(self):
        self.assertEqual(Path(os.environ["EMPIRICA_SANDBOX_HOME"]), ABSENT_SANDBOX_HOME)
        self.assertFalse(ABSENT_SANDBOX_HOME.exists())
        with tempfile.TemporaryDirectory() as home, patch.object(
                selection.docker, "discover_docker",
                side_effect=AssertionError("absent authority must precede Docker discovery")) as docker:
            saved = os.environ.get("EMPIRICA_HOME")
            os.environ["EMPIRICA_HOME"] = str(Path(home).resolve())
            try:
                service = bridge.build_service(_CANONICAL_PROFILE)
                response = service.dispatch(_valid_request({
                    "type": "StartRun",
                    "control_mode": "deliberative",
                    "selector": {"project": "composition", "session": "absent"},
                    "goal": "reason without Docker",
                    "invocation": invocation_for(_CANONICAL_PROFILE),
                }, "start-absent"))
            finally:
                if saved is None:
                    os.environ.pop("EMPIRICA_HOME", None)
                else:
                    os.environ["EMPIRICA_HOME"] = saved
        self.assertIn(response["result"]["type"], {"Allow", "Block"}, response)
        self.assertEqual(
            service._coordinator.last_state.spike_backend, {"kind": "spikes_unavailable"})
        docker.assert_not_called()

    def test_unsafe_authority_does_not_block_nonspike_read(self):
        saved = os.environ["EMPIRICA_SANDBOX_HOME"]
        os.environ["EMPIRICA_SANDBOX_HOME"] = "relative/unsafe"
        try:
            response = bridge.handle(_get_run(request_id="unsafe-read"), _CANONICAL_PROFILE)
        finally:
            os.environ["EMPIRICA_SANDBOX_HOME"] = saved
        _assert_inert_no_run(response)

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
