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
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
from adapters.sandbox import common as sandbox_common  # noqa: E402
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
            _PLUGIN_ROOT / "tests/test_pi_private_bridge.py",
            _PLUGIN_ROOT / "tests/test_observer.py",
            _PLUGIN_ROOT / "tests/test_public_tools.py",
            _PLUGIN_ROOT / "tests/v2/driver.py",
            _PLUGIN_ROOT / "tests/v2/fs_store.py",
            _PLUGIN_ROOT / "tests/v2/observer_store.py",
            _PLUGIN_ROOT / "tests/v2/test_observer_fs.py",
            _PLUGIN_ROOT / "tests/v2/test_timeline.py",
            _PLUGIN_ROOT / "adapters/claude/tests/test_claude_adapter.py",
            _PLUGIN_ROOT / "adapters/claude/tests/test_activation_lifecycle.py",
            _PLUGIN_ROOT / "adapters/claude/tests/test_claude_adapter_conformance.py",
            _PLUGIN_ROOT / "adapters/codex/tests/test_codex_adapter.py",
            _PLUGIN_ROOT / "adapters/codex/tests/test_codex_adapter_conformance.py",
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
                ) or any(
                    # PublicTools without dispatch= defaults to bridge.handle
                    # (adapters/public_tools.py); a **kwargs call counts too.
                    isinstance(node, ast.Call)
                    and (getattr(node.func, "id", None) == "PublicTools"
                         or getattr(node.func, "attr", None) == "PublicTools")
                    and not any(kw.arg == "dispatch" for kw in node.keywords)
                    for node in ast.walk(tree)
                )
                if reaches_bridge:
                    self.assertIn(path, bridge_tests, path)
                    source = path.read_text(encoding="utf-8")
                    self.assertIn("from sandbox_isolation import", source, path)
                    self.assertIn("force_absent_sandbox_home()", source, path)
        self.assertEqual(Path(os.environ["EMPIRICA_SANDBOX_HOME"]), ABSENT_SANDBOX_HOME)
        self.assertFalse(ABSENT_SANDBOX_HOME.exists())

    def test_build_service_composes_exact_v2_authority_image_resolver_and_harness(self):
        execution_store = object()
        store = type("Store", (), {"execution_store": execution_store})()
        selector, resolver, image_resolver, docker_harness, service = (
            object(), object(), object(), object(), object())
        loaded_release = "sha256:" + "0" * 64
        with (patch.object(bridge, "GenericQualificationStore", return_value=store) as store_type,
              patch.object(
                  bridge, "GenericBackendSelector", return_value=selector) as selector_type,
              patch.object(bridge, "freeze_loaded_release", return_value=loaded_release) as frozen,
              patch.object(
                  bridge, "GenericAuthorityResolver", return_value=resolver) as resolver_type,
              patch.object(
                  bridge, "DockerLocalImageResolver", return_value=image_resolver) as image_type,
              patch.object(
                  bridge, "GenericDockerHarness", return_value=docker_harness) as harness_type,
              patch.object(bridge._v2, "compose", return_value=service) as compose):
            self.assertIs(bridge.build_service(_CANONICAL_PROFILE), service)
        store_type.assert_called_once_with()
        selector_type.assert_called_once_with(store)
        frozen.assert_called_once_with()
        resolver_type.assert_called_once_with(store, loaded_release=loaded_release)
        image_type.assert_called_once_with(resolver)
        harness_type.assert_called_once_with(execution_store, resolver)
        self.assertIs(compose.call_args.kwargs["harness"], docker_harness)
        self.assertIs(compose.call_args.kwargs["image_resolver"], image_resolver)
        self.assertIs(compose.call_args.kwargs["spike_authority_selector"], selector)
        # E9: the host qualification port is composed exactly where the generic Docker backend
        # composed, and it is the bridge's own run-scoped port type.
        port = compose.call_args.kwargs["qualification_port"]
        self.assertIsInstance(port, bridge._RunQualificationPort)

    def test_sandbox_constructor_failure_degrades_only_spikes(self):
        names = ("GenericQualificationStore", "GenericBackendSelector", "freeze_loaded_release",
                 "GenericAuthorityResolver", "DockerLocalImageResolver", "GenericDockerHarness")
        for failing in names:
            store_value = type("Store", (), {"execution_store": object()})()
            with self.subTest(failing=failing), \
                    patch.object(bridge, "GenericQualificationStore",
                                 return_value=store_value) as store, \
                    patch.object(bridge, "GenericBackendSelector",
                                 return_value=object()) as selector, \
                    patch.object(bridge, "freeze_loaded_release",
                                 return_value="sha256:" + "0" * 64) as loaded_release, \
                    patch.object(bridge, "GenericAuthorityResolver",
                                 return_value=object()) as resolver, \
                    patch.object(bridge, "DockerLocalImageResolver",
                                 return_value=object()) as image_resolver, \
                    patch.object(bridge, "GenericDockerHarness",
                                 return_value=object()) as docker_harness:
                {"GenericQualificationStore": store, "GenericBackendSelector": selector,
                 "freeze_loaded_release": loaded_release,
                 "GenericAuthorityResolver": resolver,
                 "DockerLocalImageResolver": image_resolver,
                 "GenericDockerHarness": docker_harness}[failing].side_effect = PermissionError(
                     "unsafe")
                service = bridge.build_service(_CANONICAL_PROFILE)
            from application.ports import NullSpikeAuthoritySelector
            self.assertIsNone(service._harness)
            self.assertIsInstance(
                service._coordinator.spike_authority_selector, NullSpikeAuthoritySelector)
            self.assertIsNone(service._coordinator.qualification_port,
                               "a degraded composition composes no qualification port")
            response = service.dispatch(_valid_request({"type": "GetContract", "target": "index"}))
            self.assertEqual(response["result"]["type"], "Allow", response)

    def test_failed_first_snapshot_latches_spikes_unavailable_for_the_process(self):
        # Same process: the first composition meets a malformed manifest at the loaded-release
        # boundary, and a valid newer release then lands on disk. No later composition may
        # adopt it: spikes stay unavailable for the process's life, and non-spike operations
        # keep composing.
        execution_store = object()
        store = type("Store", (), {"execution_store": execution_store})()
        landed = {"implementation_digest": "sha256:" + "0" * 64}
        disk = [{"implementation_digest": {"files": []}}]  # the malformed first manifest
        reads = []

        def disk_manifest():
            reads.append(1)
            return disk[-1]

        sandbox_common._loaded_release = None
        sandbox_common._loaded_release_failed = None
        self.addCleanup(lambda: setattr(sandbox_common, "_loaded_release", None))
        self.addCleanup(lambda: setattr(sandbox_common, "_loaded_release_failed", None))

        def composed_service():
            return bridge.build_service(_CANONICAL_PROFILE)

        from application.ports import NullSpikeAuthoritySelector
        with (patch.object(bridge, "GenericQualificationStore", return_value=store) as store_type,
              patch.object(
                  bridge, "GenericBackendSelector", return_value=object()) as selector_type,
              patch.object(bridge, "GenericAuthorityResolver", return_value=object()),
              patch.object(bridge, "DockerLocalImageResolver", return_value=object()),
              patch.object(bridge, "GenericDockerHarness", return_value=object()),
              patch.object(sandbox_common, "implementation_manifest", disk_manifest)):
            services = [composed_service()]
            # The loaded-release snapshot may be retried never: no composition after the
            # failed first one reads the disk, constructs a store, or blesses the new digest.
            for _ in range(2):
                disk.append(landed)  # a valid release lands on disk under the live process
                services.append(composed_service())
            store_type.assert_not_called()
            selector_type.assert_not_called()
        for service in services:
            self.assertIsNone(service._harness)
            self.assertIsInstance(
                service._coordinator.spike_authority_selector, NullSpikeAuthoritySelector)
            self.assertIsNone(service._coordinator.qualification_port)
            response = service.dispatch(_valid_request({"type": "GetContract", "target": "index"}))
            self.assertEqual(response["result"]["type"], "Allow", response)
        self.assertEqual(reads, [1])  # one snapshot attempt, at the failed first composition
        self.assertTrue(sandbox_common._loaded_release_failed)
        self.assertIsNone(sandbox_common.loaded_release_digest())

    def test_bridge_construction_performs_no_docker_discovery(self):
        store = SimpleNamespace(execution_store=object())
        selector = SimpleNamespace(select=Mock(
            side_effect=AssertionError("selection is StartRun-only")))
        with patch.object(bridge, "GenericQualificationStore", return_value=store), \
                patch.object(bridge, "GenericBackendSelector", return_value=selector), \
                patch.object(bridge, "GenericAuthorityResolver", return_value=object()), \
                patch.object(bridge, "DockerLocalImageResolver", return_value=object()), \
                patch.object(bridge, "GenericDockerHarness", return_value=object()):
            service = bridge.build_service(_CANONICAL_PROFILE)
        self.assertTrue(callable(service.dispatch))
        selector.select.assert_not_called()

    def test_start_run_binds_the_run_scoped_reference_without_docker_reads(self):
        self.assertEqual(Path(os.environ["EMPIRICA_SANDBOX_HOME"]), ABSENT_SANDBOX_HOME)
        self.assertFalse(ABSENT_SANDBOX_HOME.exists())
        with tempfile.TemporaryDirectory() as home:
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
        # E9: the composed generic Docker backend makes the run unqualified — the machine-wide
        # authority record was never read (and in this test's sandbox home it does not exist).
        self.assertEqual(
            service._coordinator.last_state.spike_backend, {"kind": "unqualified"})

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


class QualificationPortCompositionTests(unittest.TestCase):
    """The qualification port is an execution path: it is composed where the generic Docker
    composition succeeded, and never for a profile the registry marks unsupported."""

    def _compose(self, profile_id):
        store = type("Store", (), {"execution_store": object()})()
        with (patch.object(bridge, "GenericQualificationStore", return_value=store),
              patch.object(bridge, "GenericBackendSelector", return_value=object()),
              patch.object(bridge, "freeze_loaded_release", return_value="sha256:" + "0" * 64),
              patch.object(bridge, "GenericAuthorityResolver", return_value=object()),
              patch.object(bridge, "DockerLocalImageResolver", return_value=object()),
              patch.object(bridge, "GenericDockerHarness", return_value=object())):
            return bridge.build_service(profile_id)

    def test_the_port_is_composed_for_every_supported_profile(self):
        for profile in _HOST_PROFILES["profiles"]:
            profile_id = profile["profile_id"]
            with self.subTest(profile=profile_id):
                port = self._compose(profile_id)._coordinator.qualification_port
                if profile["promotion_status"] == bridge._QUALIFICATION_WITHHELD_STATUS:
                    self.assertIsNone(port)
                else:
                    self.assertIsInstance(port, bridge._RunQualificationPort)

    def test_an_unsupported_profile_pointed_at_an_approved_run_refuses_unsupported_and_reserves_nothing(self):
        from test_d7_transactions import Workspace
        from test_environment_actions import QUALIFY_ACTION
        from test_qualification_transaction import FakeQualificationPort, QualificationEnv
        env = QualificationEnv(FakeQualificationPort())
        unsupported = next(p["profile_id"] for p in _HOST_PROFILES["profiles"]
                           if p["promotion_status"] == bridge._QUALIFICATION_WITHHELD_STATUS)
        coordinator = self._compose(unsupported)._coordinator
        coordinator.runs, coordinator.artifacts, coordinator.workspace = (
            env.runs, env.artifacts_repo, Workspace())
        before = env.runs.data[env.key]
        response = coordinator.handle(
            {"type": "ObserveAction", "run_id": env.run_id,
             "action": json.loads(json.dumps(QUALIFY_ACTION))}, "unsupported-qualify")["result"]
        jsonschema.validate(instance={"protocol": _PROTOCOL, "request_id": "unsupported-qualify",
                                      "result": response}, schema=_RESPONSE_SCHEMA)
        self.assertEqual((response["type"], response["code"]), ("Fault", "unsupported"), response)
        self.assertEqual(env.runs.data[env.key], before, "nothing was reserved or committed")
        self.assertEqual((env.rows(), env.port.calls), ([], 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
