#!/usr/bin/env python3
"""Strict run/5 backend binding and legacy run/4, run/3, major-version run/2 rejection regressions."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from adapters import author_view
from adapters.state.repository import FilesystemRunRepository
from application import protocol, run_state, v2
from application.location import encode_handle, storage_id
from application.generic_spike import GenericQualifiedBackend
from application.ports import SpikesUnavailable
from application.transaction import Coordinator
from core import environment
from core.run import ATTEMPT_OUTCOMES as ENVIRONMENT_RUN_ATTEMPT_OUTCOMES
from core import spike_plan
from core.records import Conflict, Present, Revision, RunKey

from governance_setup import TEST_INVOCATION
from test_d7_transactions import Artifacts, Harness, Runs, Workspace, chain, initial, spike_action

PROFILE = protocol.host_profile_id("pi")
ROOT = Path(__file__).resolve().parents[3]
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _qualified(qualification=DIGEST_A, execution=DIGEST_B):
    return GenericQualifiedBackend(
        "docker", "docker-isolated-v1", qualification, execution)


def _persisted(value):
    if type(value) is GenericQualifiedBackend:
        return {"kind": "qualified", "backend": value.backend,
                "profile_id": value.profile_id,
                "qualification_record_digest": value.qualification_record_digest,
                "execution_binding_digest": value.execution_binding_digest}
    return value.persisted()


def start_command(project="p", session="s"):
    return {
        "type": "StartRun",
        "control_mode": "deliberative",
        "invocation": dict(TEST_INVOCATION),
        "selector": {"project": project, "session": session},
        "goal": "g",
    }


class CountingSelector:
    def __init__(self, value=None):
        self.value = value or SpikesUnavailable()
        self.calls = 0

    def __bool__(self):
        return False

    def select(self):
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class RequestedProfileSelector:
    def __init__(self):
        self.calls = []

    def select(self, *args):
        self.calls.append(args)
        profile = args[0] if args else "docker-isolated-v1"
        return GenericQualifiedBackend("docker", profile, DIGEST_A, DIGEST_B)


class PublicSpikeBackendTests(unittest.TestCase):
    def _public_views(self, binding):
        selector = CountingSelector(binding)
        coordinator = Coordinator(Workspace(), Harness(), Runs(), Artifacts(), PROFILE, {}, selector)
        binding_data = _persisted(binding)
        started = coordinator.handle(start_command(session=f"public-{binding_data['kind']}"), "start")
        run_id = started["result"]["run"]["id"]
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "route", "reason": "public projection test"}}, "route")
        coordinator.handle({"type": "ObserveAction", "run_id": run_id, "action": {
            "kind": "graph", "payload": {"root": "C0", "claims": [{
                "id": "C0", "text": "The public backend is immutable.",
                "kind": "ordinary", "gating": True}], "edges": []}}}, "graph")
        run = coordinator.handle({"type": "GetRun", "run_id": run_id}, "read-run")
        argument = coordinator.handle({"type": "GetArgument", "run_id": run_id}, "read-argument")
        self.assertEqual(selector.calls, 1, "public reads must not rediscover authority")
        protocol.schema_validator("response").validate(run)
        protocol.schema_validator("response").validate(argument)
        return run["result"]["run"], argument["result"]["argument"], run["result"]

    def test_host_start_profile_is_selected_once_and_persisted_immutably(self):
        for configured, expected_args, expected_profile in (
                (None, (), "docker-isolated-v1"),
                ("docker-isolated-v1", ("docker-isolated-v1",), "docker-isolated-v1"),
                ("docker-networked-v1", ("docker-networked-v1",), "docker-networked-v1")):
            selector = RequestedProfileSelector()
            coordinator = Coordinator(Workspace(), Harness(), Runs(), Artifacts(), PROFILE, {}, selector)
            command = start_command(session=f"profile-{configured or 'default'}")
            if configured is not None:
                command["spike_profile"] = configured
            started = coordinator.handle(command, f"start-{configured}")
            run_id = started["result"]["run"]["id"]
            self.assertEqual(selector.calls, [expected_args])
            self.assertEqual(started["result"]["run"]["spike_backend"]["profile_id"], expected_profile)
            read = coordinator.handle({"type": "GetRun", "run_id": run_id}, f"read-{configured}")
            self.assertEqual(read["result"]["run"]["spike_backend"],
                             started["result"]["run"]["spike_backend"])
            self.assertEqual(selector.calls, [expected_args])

    def test_observe_action_cannot_request_network_profile_authority(self):
        validator = protocol.schema_validator("request")
        action = {**spike_action("probe.py"), "spike_profile": "docker-networked-v1"}
        envelope = {"protocol": "empirica/v2", "request_id": "profile-in-action",
                    "command": {"type": "ObserveAction", "run_id": "r", "action": action}}
        clean = {**action}
        clean.pop("spike_profile")
        self.assertTrue(validator.is_valid({**envelope,
                                           "command": {**envelope["command"], "action": clean}}))
        self.assertFalse(validator.is_valid(envelope))

    def test_closed_binding_projects_identically_into_run_and_audit_dossier(self):
        for selected in (SpikesUnavailable("local diagnostic must not escape"),
                         _qualified(DIGEST_A, DIGEST_B)):
            persisted = _persisted(selected)
            with self.subTest(kind=persisted["kind"]):
                run, argument, result = self._public_views(selected)
                self.assertEqual(run["spike_backend"], persisted)
                self.assertEqual(argument["spike_backend"], persisted)
                public_text = author_view.render_author_view(result, strict=True)
                self.assertNotIn("local diagnostic", public_text)
                if persisted["kind"] == "spikes_unavailable":
                    self.assertIn(
                        "spikes unavailable; no spike requests "
                        "(research, graph, honest stop still allowed)", public_text)
                else:
                    self.assertIn("qualified generic Docker profile binding", public_text)
                    self.assertIn("not a live-health claim", public_text)
                    self.assertIn(DIGEST_A, public_text)
                    self.assertIn(DIGEST_B, public_text)

    def test_generated_claim_spike_reason_fixtures_are_qualified(self):
        seen = []
        fixture_dir = ROOT / "contracts/empirica/v2/fixtures"
        for path in sorted(fixture_dir.glob("*.json")):
            document = json.loads(path.read_text())
            result = document.get("expected", {}).get("result", {})
            codes = [reason.get("code") for reason in result.get("reasons", ())
                     if isinstance(reason, dict)]
            if any(isinstance(code, str) and code.startswith("claim.spike_") for code in codes):
                seen.append(path.name)
                self.assertEqual(result["run"]["spike_backend"]["kind"], "qualified", path.name)
        self.assertEqual(seen, ["block-stale-spike-missing.json", "block-stale-spike.json"])

    def test_response_schema_rejects_every_backend_extension_or_near_match(self):
        run, _argument, result = self._public_views(SpikesUnavailable())
        for backend in (
            {"kind": "spikes_unavailable", "diagnostic": "socket"},
            {"kind": "qualified", "backend": "host", "profile_id": "docker-isolated-v1",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "other",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "docker-rust-v1",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "docker-isolated-v1",
             "qualification_record_digest": "bad", "execution_binding_digest": DIGEST_B},
        ):
            candidate = copy.deepcopy(result)
            candidate["run"]["spike_backend"] = backend
            with self.subTest(backend=backend):
                self.assertFalse(protocol.schema_validator("response").is_valid({
                    "protocol": "empirica/v2", "request_id": "schema", "result": candidate}))
        self.assertNotIn("diagnostic", run["spike_backend"])

    def test_legacy_backend_types_have_no_public_reachability(self):
        plugin = ROOT / "plugins/empirica"
        public_sources = [
            plugin / "application/ports.py",
            plugin / "application/transaction.py",
            plugin / "application/v2.py",
            plugin / "core/run.py",
            plugin / "adapters/bridge.py",
        ]
        forbidden = (
            "QualifiedSpikeBackend", "LegacyDockerBinding", "DockerSpikeHarness",
            "execute_spike_bound", "docker-rust-v1", "dependent_files",
        )
        for source in public_sources:
            text = source.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, text, f"{source}: legacy public reachability")

    SPIKE_PHRASES = ("no spike result evidence", "result-cas", "crash", "separate call", "duplicate execution")

    def spike_rule_problems(self, contract, evidence, tool_description):
        """Where the spike rules live now: the public contract's evidence/spike clauses, references/evidence.md, and the
        spike_request tool description (which points to the limits). The skill restates none of them."""
        problems = []
        clauses = {clause["id"]: clause["text"]
                   for clause in json.loads(contract)["sections"]["evidence/spike"]["clauses"]} \
            if "sections" in json.loads(contract) else {}
        for clause in ("evidence/spike/design", "evidence/spike/outcomes", "evidence/spike/attestation",
                       "evidence/spike/limits"):
            if clause not in clauses:
                problems.append(f"the contract lacks clause {clause}")
        for name, text in (("the contract", contract), ("references/evidence.md", evidence)):
            normalized = " ".join(text.lower().split())
            positions = [text.find(value) for value in ("120", "210", "300", "360")]
            if -1 in positions or positions != sorted(positions):
                problems.append(f"{name} does not state the 120/210/300/360-second limits in order")
            problems += [f"{name} lacks {phrase!r}" for phrase in self.SPIKE_PHRASES if phrase not in normalized]
        if "evidence/spike/limits" not in evidence:
            problems.append("references/evidence.md does not point to evidence/spike/limits")
        if "evidence/spike/limits" not in tool_description:
            problems.append("the spike_request tool description does not point to evidence/spike/limits")
        return problems

    def spike_sources(self):
        evidence = (ROOT / "plugins/empirica/skills/empirica/references/evidence.md").read_text()
        contract = (ROOT / "contracts/empirica/v2/public-contract.json").read_text()
        request = json.loads((ROOT / "contracts/empirica/v2/request.schema.json").read_text())
        return contract, evidence, request["$defs"]["actionSpikeRequest"]["description"]

    def test_agent_spike_rules_live_in_the_contract_the_tool_description_and_evidence_md(self):
        contract, evidence, description = self.spike_sources()
        self.assertEqual(self.spike_rule_problems(contract, evidence, description), [])
        # the flat public tool schema the hosts register carries the same description
        flat = json.loads((ROOT / "contracts/empirica/v2/public-tools.json").read_text())
        self.assertIn(description, json.dumps(flat))

    def test_the_binding_pin_is_red_against_a_contract_missing_the_clause(self):
        contract, evidence, description = self.spike_sources()
        document = json.loads(contract)
        clauses = document["sections"]["evidence/spike"]["clauses"]
        document["sections"]["evidence/spike"]["clauses"] = [c for c in clauses if c["id"] != "evidence/spike/limits"]
        problems = self.spike_rule_problems(json.dumps(document), evidence, description)
        self.assertIn("the contract lacks clause evidence/spike/limits", problems)
        self.assertTrue(self.spike_rule_problems(contract, evidence, description.replace("evidence/spike/limits", "")))

    def test_agent_spike_design_limits_are_bound_to_backend_constants(self):
        evidence = (ROOT / "plugins/empirica/skills/empirica/references/evidence.md").read_text()
        contract = (ROOT / "contracts/empirica/v2/public-contract.json").read_text()
        skill = (ROOT / "plugins/empirica/skills/empirica/SKILL.md").read_text()
        # the skill no longer carries these facts (the activation validator's numeric guard keeps it so)
        # the validator resolves its paths from the repository root; restore the working directory (Python 3.9 has no contextlib.chdir)
        import importlib.util
        import os
        previous = os.getcwd()
        os.chdir(ROOT)
        try:
            spec = importlib.util.spec_from_file_location("empirica_activation_guard", ROOT / "scripts/validate_empirica_activation.py")
            activation = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(activation)
        finally:
            os.chdir(previous)
        self.assertEqual(activation.skill_spike_number_problems(skill), [])
        resources = " ".join((evidence, contract))
        self.assertIn("stdout", resources.lower())
        self.assertIn("stderr", resources.lower())
        self.assertIn("512 MiB", resources)
        self.assertIn("256 PIDs", resources)
        self.assertIn("nofile 1024", resources)
        # The one agent-visible limitation set; scripts/validate_docker_spike_contract.py checks
        # that it equals its rendering from the code constants.
        self.assertIn("evidence/spike/limits", evidence)
        self.assertIn("256 files", resources)
        self.assertIn("64 MiB", resources)
        self.assertIn("1 MiB", resources)
        self.assertEqual(
            (spike_plan.WORKLOAD_TIMEOUT_SECONDS,
             spike_plan.BACKEND_OPERATION_TIMEOUT_SECONDS), (120, 210))
        self.assertEqual(
            (spike_plan.FRAMEWORK_SPIKE_POLICY.max_cpu_count,
             spike_plan.WORK_TMPFS_BYTES, spike_plan.MEMORY_BYTES,
             spike_plan.PIDS, spike_plan.OPEN_FILES,
             spike_plan.MAX_FILE_SIZE_BYTES, spike_plan.STREAM_BYTES),
            (2, 512 * 1024 * 1024, 2 * 1024 * 1024 * 1024,
             256, 1024, 64 * 1024 * 1024, 1024 * 1024))
        mcp = json.loads((ROOT / "plugins/empirica/.mcp.json").read_text())
        self.assertEqual(mcp["mcpServers"]["empirica"]["tool_timeout_sec"], 360)
        pi_transport = (ROOT / "plugins/empirica/adapters/pi/src/stdio-transport.ts").read_text()
        self.assertIn("SPIKE_BRIDGE_TIMEOUT_MS = 300_000", pi_transport)


class Run5SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = protocol.state_schema()
        self.fixture = json.loads((
            ROOT / "contracts/empirica/v2/state-fixtures/valid-active.json"
        ).read_text())

    def test_exact_three_closed_binding_branches(self):
        unavailable = copy.deepcopy(self.fixture)
        unavailable["spike_backend"] = {"kind": "spikes_unavailable"}
        protocol.schema_validator("state").validate(unavailable)
        unqualified = copy.deepcopy(self.fixture)
        unqualified["spike_backend"] = {"kind": "unqualified"}
        protocol.schema_validator("state").validate(unqualified)
        qualified = copy.deepcopy(self.fixture)
        qualified["spike_backend"] = {
            "kind": "qualified", "backend": "docker", "profile_id": "docker-isolated-v1",
            "qualification_record_digest": DIGEST_A,
            "execution_binding_digest": DIGEST_B,
        }
        protocol.schema_validator("state").validate(qualified)
        bad = []
        for binding in (
            {},
            {"kind": "spikes_unavailable", "reason": "absent"},
            {"kind": "unqualified", "reason": "qualification pending"},
            {"kind": "unqualified", "record_digest": DIGEST_A},
            {"kind": "qualified", "backend": "host", "profile_id": "docker-isolated-v1",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "other",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "docker-rust-v1",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "docker-isolated-v1",
             "qualification_record_digest": "bad", "execution_binding_digest": DIGEST_B},
        ):
            candidate = copy.deepcopy(self.fixture)
            candidate["spike_backend"] = binding
            bad.append(candidate)
        missing = copy.deepcopy(self.fixture)
        missing.pop("spike_backend")
        bad.append(missing)
        for candidate in bad:
            with self.subTest(binding=candidate.get("spike_backend")):
                self.assertFalse(protocol.schema_validator("state").is_valid(candidate))
                self.assertEqual(run_state.classify_and_decode(candidate).kind, "current_corrupt")

    def test_the_receipt_document_digest_is_conditioned_on_the_approval_kind(self):
        """PR 13 (ADR-0072): the recorded binding is required evidence of every host approval —
        a host_ui receipt without the member, or carrying null, is refused by the state schema and
        classifies corrupt; an auto receipt must stay free of it (auto answers no human question)."""
        fixture = self.fixture
        receipt = fixture["governance"]["receipts"][0]
        self.assertEqual(receipt["approval_kind"], "host_ui")
        for label, change in (("a host_ui receipt that carries none",
                               {"document_digest": None}),
                              ("a host_ui receipt that omits the member",
                               {"document_digest": "__absent__"})):
            candidate = copy.deepcopy(fixture)
            row = candidate["governance"]["receipts"][0]
            if change["document_digest"] == "__absent__":
                row.pop("document_digest", None)
            else:
                row.update(change)
            with self.subTest(case=label):
                self.assertFalse(protocol.schema_validator("state").is_valid(candidate))
                self.assertEqual(run_state.classify_and_decode(candidate).kind, "current_corrupt")
        # the auto control: a receipt that answers no human's question carries no binding at all
        candidate = copy.deepcopy(fixture)
        row = candidate["governance"]["receipts"][0]
        row.update(approval_kind="auto", presentation_fingerprint=None,
                   document_digest="sha256:" + "c" * 64)
        self.assertFalse(protocol.schema_validator("state").is_valid(candidate),
                         "auto may not claim a document")

    def test_roundtrip_is_deeply_immutable_and_encoder_is_run5_only(self):
        classified = run_state.classify_and_decode(self.fixture)
        self.assertEqual(classified.kind, "valid")
        with self.assertRaises(TypeError):
            classified.state.spike_backend["kind"] = "changed"
        self.assertEqual(run_state.encode_state(classified.state), self.fixture)
        # ADR-0072: every family before the current one stays undecodable with no
        # backwards compatibility or repair; run/4 is the read-only legacy family (E2b),
        # run/3 and the major run/2 are corrupt.
        for discriminator, kind in ((run_state.LEGACY_STATE_SCHEMA_ID, "legacy"),
                                    ("empirica.run/3", "current_corrupt"),
                                    ("empirica.run/2", "current_corrupt")):
            old = copy.deepcopy(self.fixture)
            old["state_schema"] = discriminator
            with self.subTest(state_schema=discriminator):
                self.assertEqual(run_state.classify_and_decode(old).kind, kind)
                self.assertIsNone(run_state.classify_and_decode(old).state)
                with self.assertRaises(ValueError):
                    run_state.encode_state(replace(classified.state, state_schema=discriminator))
        run2 = copy.deepcopy(self.fixture)
        run2["state_schema"] = "empirica.run/2"
        run2.pop("spike_backend")
        self.assertEqual(run_state.classify_and_decode(run2).kind, "current_corrupt")
        run2["spike_backend"] = {"kind": "spikes_unavailable"}
        self.assertEqual(run_state.classify_and_decode(run2).kind, "current_corrupt")

        class StringImpostor:
            def __str__(self):
                return DIGEST_A

        for key in ("qualification_record_digest", "execution_binding_digest"):
            for impostor in (1, True, StringImpostor()):
                with self.subTest(key=key, impostor=type(impostor).__name__):
                    binding = _persisted(_qualified(DIGEST_A, DIGEST_B))
                    binding[key] = impostor
                    with self.assertRaises(ValueError):
                        replace(classified.state, spike_backend=binding)


def _proposal_body():
    """A valid closed environment proposal body (spec §4.1)."""
    return {"profile_id": "docker-isolated-v1", "spike_images": ["python:3.12-slim"],
            "preparation": ["docker pull the pinned probe image"],
            "rationale": "prepare and qualify the run-scoped environment"}


def _environment_member(**changes):
    proposal = _proposal_body()
    for key in ("profile_id", "spike_images", "preparation", "rationale"):
        if key in changes:
            proposal[key] = changes.pop(key)
    try:
        digest = environment.proposal_digest(proposal)
    except environment.EnvironmentProposalError:
        # a malformed body has no coherent digest; the shape under test is everything else
        digest = DIGEST_A
    row = {"revision": 1, "proposal": proposal,
           "digest": digest,
           "state": "pending", "receipt_id": None}
    row.update(changes)
    return row


def _attempt_member(**changes):
    row = {"attempt_id": "qualification-1", "revision": 1, "started_stamp": 1,
           "outcome": "reserved", "record_digest": None, "reason": None}
    row.update(changes)
    return row


class Run5EnvironmentStateTests(unittest.TestCase):
    """run/5's three new state members (spec §4.2, fields only): required by the schema, closed
    and bounded like the pure model, and validated by OperationalState. No behaviour uses them
    yet; StartRun still binds spikes_unavailable (E9)."""

    def setUp(self):
        self.fixture = json.loads((
            ROOT / "contracts/empirica/v2/state-fixtures/valid-active.json"
        ).read_text())

    def _valid_document(self, **overrides):
        candidate = copy.deepcopy(self.fixture)
        candidate["environment"] = overrides.pop("environment", _environment_member())
        candidate["environment_history"] = overrides.pop("history", [])
        candidate["qualification_attempts"] = overrides.pop("attempts", [])
        assert not overrides
        return candidate

    def test_the_three_members_are_required_and_a_valid_revision_roundtrips(self):
        candidate = self._valid_document(environment=_environment_member(state="approved"))
        protocol.schema_validator("state").validate(candidate)
        classified = run_state.classify_and_decode(candidate)
        self.assertEqual(classified.kind, "valid")
        self.assertEqual(run_state.encode_state(classified.state), candidate)
        with self.assertRaises(TypeError):
            classified.state.environment["state"] = "rejected"
        for member in ("environment", "environment_history", "qualification_attempts"):
            missing = copy.deepcopy(candidate)
            missing.pop(member)
            with self.subTest(missing=member):
                self.assertFalse(protocol.schema_validator("state").is_valid(missing))
                self.assertEqual(run_state.classify_and_decode(missing).kind, "current_corrupt")

    def test_the_environments_digest_is_the_canonical_proposal_digest(self):
        # the schema only sees a sha256, so this coherence check is the core's alone
        member = _environment_member()
        member["digest"] = DIGEST_A
        candidate = self._valid_document(environment=member)
        self.assertTrue(protocol.schema_validator("state").is_valid(candidate),
                        "the schema only pins the digest shape")
        self.assertEqual(run_state.classify_and_decode(candidate).kind, "current_corrupt")
        for impostor in (1, True, DIGEST_A.upper()):
            with self.subTest(digest=impostor):
                with self.assertRaises(ValueError):
                    replace(run_state.classify_and_decode(self.fixture).state,
                            environment=_environment_member(digest=impostor))

    def test_operational_state_bounds_the_two_arrays_itself(self):
        state = run_state.classify_and_decode(self.fixture).state
        history = tuple(_environment_member(revision=index + 1, state="rejected")
                       for index in range(environment.MAX_ENVIRONMENT_HISTORY))
        outcomes = tuple(ENVIRONMENT_RUN_ATTEMPT_OUTCOMES)
        attempts = tuple(
            _attempt_member(attempt_id=f"qualification-{index + 1}", started_stamp=index + 1,
                            outcome=outcomes[index % len(outcomes)])
            for index in range(environment.MAX_QUALIFICATION_ATTEMPTS))
        replace(state, qualification_attempts=(
            _attempt_member(attempt_id="q" * 128),
            _attempt_member(reason="environment-required", outcome="failed"),
            *attempts[:max(environment.MAX_QUALIFICATION_ATTEMPTS - 2, 0)]))
        # exactly at the core bound is admitted, one past is refused, schema aside
        replace(state, environment_history=history)
        replace(state, qualification_attempts=attempts)
        with self.assertRaises(ValueError):
            replace(state, environment_history=(
                *history, _environment_member(revision=environment.MAX_ENVIRONMENT_HISTORY + 1)))
        with self.assertRaises(ValueError):
            replace(state, qualification_attempts=(
                *attempts, _attempt_member(attempt_id="over-the-bound")))
        for field, bad in (
            ("environment", 7), ("environment_history", 7), ("qualification_attempts", 7),
            ("environment", {"kind": "not-a-member"}),
            ("environment", _environment_member(revision=True)),
            ("environment", _environment_member(digest="bad")),
            ("environment", _environment_member(state="bound")),
            ("environment", _environment_member(receipt_id="")),
            ("environment_history", (_environment_member(state="bound"),)),
            ("qualification_attempts", (_attempt_member(attempt_id="q" * 129),)),
            ("qualification_attempts", (_attempt_member(revision="1"),)),
            ("qualification_attempts", (_attempt_member(started_stamp=-1),)),
            ("qualification_attempts", (_attempt_member(outcome="orphaned"),)),
            ("qualification_attempts", (_attempt_member(record_digest="bad"),)),
            ("qualification_attempts", (_attempt_member(reason="Bad Reason"),)),
            ("qualification_attempts", ({"outcome": "reserved"},)),
        ):
            with self.subTest(field=field, bad=bad):
                with self.assertRaises(ValueError):
                    replace(state, **{field: bad})

    def test_replacing_an_unrelated_field_keeps_the_populated_members_valid(self):
        """Review finding 1: ``__post_init__`` re-validates a state it had already frozen, the way
        every ordinary transition moves a decoded run (``dataclasses.replace``). The frozen
        proposal (current and history) must pass the same validators its decoded form passed,
        so an unrelated-field replacement survives and nothing is re-derived."""
        document = self._valid_document(
            environment=_environment_member(state="approved"),
            history=[_environment_member(revision=2, state="rejected"),
                     _environment_member(revision=1, state="pending")],
            attempts=[_attempt_member(),
                      _attempt_member(reason="environment-required", outcome="failed")])
        protocol.schema_validator("state").validate(document)
        state = run_state.classify_and_decode(document).state
        moved = replace(state, stamp_seq=state.stamp_seq + 1)
        again = replace(moved, stamp_seq=moved.stamp_seq + 1)
        for subject in (moved, again):
            self.assertEqual(subject.environment["revision"], 1)
            self.assertEqual(subject.environment["digest"], state.environment["digest"])
            self.assertEqual([row["revision"] for row in subject.environment_history], [2, 1])
            self.assertEqual([row["digest"] for row in subject.environment_history],
                             [row["digest"] for row in state.environment_history])
            self.assertEqual(len(subject.qualification_attempts), 2)
        reencoded = run_state.encode_state(again)
        self.assertEqual(reencoded, {**document, "stamp_seq": document["stamp_seq"] + 2})
        self.assertEqual(run_state.classify_and_decode(reencoded).kind, "valid")

    def test_history_rows_are_objects_and_the_two_arrays_are_real_sequences(self):
        """Review finding 2: null speaks for the current member only, and each member demands an
        actual bounded sequence — a mapping is never one (an empty one would read as "no rows"),
        and neither is an unsized iterable (its length is not a fact any transition may check)."""
        state = run_state.classify_and_decode(self.fixture).state
        replace(state, environment=None)
        plain_history, plain_attempts = [_environment_member()], [_attempt_member()]
        for field, row in (("environment_history", plain_history[0]),
                           ("qualification_attempts", plain_attempts[0])):
            with self.subTest(member=field):
                with self.assertRaises(ValueError):
                    # null is the current member's word; a history row is an object, never null
                    replace(state, **{field: (None,)})
                with self.assertRaises(ValueError):
                    replace(state, **{field: {}})
                with self.assertRaises(ValueError):
                    replace(state, **{field: {"0": row}})
                with self.assertRaises(ValueError):
                    replace(state, **{field: iter(())})
                with self.assertRaises(ValueError):
                    replace(state, **{field: {0}})
                with self.assertRaises(ValueError):
                    replace(state, **{field: "rows"})
                with self.assertRaises(ValueError):
                    replace(state, **{field: b"rows"})
        replace(state, environment_history=[], qualification_attempts=())
        replace(state, environment_history=plain_history, qualification_attempts=plain_attempts)

class StartSelectionTests(unittest.TestCase):
    def coordinator(self, selector, *, workspace=None, harness=None, runs=None, artifacts=None):
        values = (workspace or Workspace(), harness or Harness(), runs or Runs(),
                  artifacts or Artifacts())
        return Coordinator(*values, PROFILE, {}, selector), values

    def test_selects_once_persists_exact_binding_and_active_resume_does_no_work(self):
        selector = CountingSelector(_qualified(DIGEST_A, DIGEST_B))
        coordinator, (workspace, _harness, runs, artifacts) = self.coordinator(selector)
        first = coordinator.handle(start_command(), "one")
        self.assertEqual(first["result"]["type"], "Allow")
        key = next(iter(runs.data))
        self.assertEqual(runs.data[key].value["spike_backend"], {
            "kind": "qualified", "backend": "docker", "profile_id": "docker-isolated-v1",
            "qualification_record_digest": DIGEST_A,
            "execution_binding_digest": DIGEST_B,
        })
        self.assertEqual(selector.calls, 1)
        before = (len(workspace.calls), artifacts.append_calls, runs.seq)
        selector.value = _qualified(DIGEST_B, DIGEST_A)
        second = coordinator.handle(start_command(), "two")
        self.assertEqual(second["result"]["run"]["id"], first["result"]["run"]["id"])
        self.assertEqual(selector.calls, 1)
        self.assertEqual((len(workspace.calls), artifacts.append_calls, runs.seq), before)
        self.assertEqual(runs.data[key].value["spike_backend"]["qualification_record_digest"],
                         DIGEST_A)

    def test_existing_unavailable_run_never_adopts_later_authority(self):
        selector = CountingSelector(SpikesUnavailable("absent"))
        coordinator, (_workspace, _harness, runs, _artifacts) = self.coordinator(selector)
        first = coordinator.handle(start_command(session="unavailable-sticky"), "one")
        key = next(iter(runs.data))
        self.assertEqual(runs.data[key].value["spike_backend"], {"kind": "spikes_unavailable"})
        selector.value = _qualified(DIGEST_A, DIGEST_B)
        second = coordinator.handle(start_command(session="unavailable-sticky"), "two")
        self.assertEqual(second["result"]["run"]["id"], first["result"]["run"]["id"])
        self.assertEqual(selector.calls, 1)
        self.assertEqual(runs.data[key].value["spike_backend"], {"kind": "spikes_unavailable"})

    def test_selector_exception_and_unknown_result_cannot_block_reasoning(self):
        class GenericBackendImpostor(GenericQualifiedBackend):
            pass

        impostor = GenericBackendImpostor(
            "docker", "docker-isolated-v1", DIGEST_A, DIGEST_B)
        old_binding = {
            "backend": "docker", "profile_id": "docker-rust-v1",
            "qualification_record_digest": DIGEST_A,
            "execution_binding_digest": DIGEST_B,
        }
        for value in (RuntimeError("broken"), object(), SpikesUnavailable("absent"),
                      old_binding, impostor):
            with self.subTest(value=type(value).__name__):
                selector = CountingSelector(value)
                coordinator, (_workspace, _harness, runs, _artifacts) = self.coordinator(selector)
                result = coordinator.handle(start_command(), "start")
                self.assertEqual(result["result"]["type"], "Allow")
                self.assertEqual(next(iter(runs.data.values())).value["spike_backend"],
                                 {"kind": "spikes_unavailable"})
                routed = coordinator.handle({
                    "type": "ObserveAction", "run_id": result["result"]["run"]["id"],
                    "action": {"kind": "route", "reason": "ordinary reasoning"},
                }, "route")
                self.assertEqual(routed["result"]["type"], "Allow")

    def test_create_race_returns_winners_binding_without_rewrite(self):
        winner_selector = CountingSelector(_qualified(DIGEST_B, DIGEST_A))
        winner, (_workspace, _harness, winner_runs, winner_artifacts) = self.coordinator(
            winner_selector)
        winner.handle(start_command(), "winner")
        key = next(iter(winner_runs.data))
        winner_read = winner_runs.data[key]

        class RaceRuns(Runs):
            def create(self, create_key, value):
                self.data[create_key] = Present(winner_read.value, Revision("winner"))
                raise Conflict(create_key, None, "lost create race")

        race_runs = RaceRuns()
        race_artifacts = Artifacts()
        race_artifacts.values[key] = dict(winner_artifacts.values[key])
        loser_selector = CountingSelector(_qualified(DIGEST_A, DIGEST_B))
        loser, _ = self.coordinator(
            loser_selector, runs=race_runs, artifacts=race_artifacts)
        response = loser.handle(start_command(), "loser")
        self.assertEqual(response["result"]["type"], "Allow")
        self.assertEqual(race_runs.data[key].value["spike_backend"],
                         winner_read.value["spike_backend"])
        self.assertEqual(loser_selector.calls, 1)

    def test_falsey_selector_identity_survives_service_composition(self):
        selector = CountingSelector(_qualified(DIGEST_A, DIGEST_B))
        workspace, harness, runs, artifacts = Workspace(), Harness(), Runs(), Artifacts()
        service = v2.compose(
            workspace, harness, runs, artifacts, None, PROFILE, {}, None,
            spike_authority_selector=selector)
        result = service._dispatch_validated({"request_id": "start", "command": start_command()})
        self.assertEqual(result["result"]["type"], "Allow")
        self.assertIs(service._coordinator.spike_authority_selector, selector)
        self.assertEqual(selector.calls, 1)
        self.assertEqual(next(iter(runs.data.values())).value["spike_backend"]["kind"],
                         "qualified")

    def test_base_exception_from_injected_selector_propagates(self):
        coordinator, _ = self.coordinator(CountingSelector(KeyboardInterrupt()))
        with self.assertRaises(KeyboardInterrupt):
            coordinator.handle(start_command(), "start")

    def test_explicit_start_after_latest_run2_creates_fresh_run5_without_mutating_old(self):
        selector = CountingSelector()
        coordinator, (_workspace, _harness, runs, artifacts) = self.coordinator(selector)
        historical_state, historical_artifacts = chain(initial())
        old_key = RunKey(storage_id("p"), storage_id("s"), 1)
        old_document = legacy_document(historical_state, "empirica.run/2")
        runs.create(old_key, old_document)
        for item in historical_artifacts:
            artifacts.append(old_key, item)
        before_read = copy.deepcopy(runs.data[old_key])
        before_artifacts = copy.deepcopy(artifacts.values[old_key])
        result = coordinator.handle(start_command(), "start")
        new_key = RunKey(old_key.project_id, old_key.run_id, 2)
        self.assertEqual(result["result"]["type"], "Allow")
        self.assertIn(new_key, runs.data)
        self.assertEqual(selector.calls, 1)
        self.assertEqual(runs.data[old_key], before_read)
        self.assertEqual(artifacts.values[old_key], before_artifacts)
        self.assertEqual(runs.data[new_key].value["state_schema"], "empirica.run/5")

    def test_unavailable_run5_spike_is_guarded_before_workspace_artifact_and_harness(self):
        coordinator, (workspace, harness, runs, artifacts) = self.coordinator(
            CountingSelector(SpikesUnavailable()))
        started = coordinator.handle(start_command(), "start")
        before = (len(workspace.calls), artifacts.append_calls, runs.seq, harness.calls)
        result = coordinator.handle({
            "type": "ObserveAction", "run_id": started["result"]["run"]["id"],
            "action": {"kind": "spike_request", "claim_id": "C0",
                       "command": "true", "dependent_files": ["x"]},
        }, "spike")
        self.assertEqual(result["result"], {
            "type": "Fault", "code": "unavailable", "fail_direction": "closed",
            "reason": "spikes-unavailable"})
        self.assertEqual((len(workspace.calls), artifacts.append_calls, runs.seq, harness.calls),
                         before)

    def test_unqualified_run5_spike_is_guarded_before_workspace_artifact_and_harness(self):
        # run/5 admits an unqualified binding (spec §4.2): every guarded spike path must keep
        # refusing it exactly like unavailable — no artifact, no harness call, no state rewrite.
        workspace, harness, runs, artifacts = Workspace(), Harness(), Runs(), Artifacts()
        state, values = chain(replace(initial(), spike_backend={"kind": "unqualified"}))
        key = RunKey(storage_id("p"), storage_id("s"), 1)
        runs.create(key, run_state.encode_state(state))
        for item in values:
            artifacts.append(key, item)
        coordinator = Coordinator(workspace, harness, runs, artifacts, PROFILE, {}, CountingSelector())
        before = (len(workspace.calls), artifacts.append_calls, runs.seq, harness.calls)
        result = coordinator.handle({
            "type": "ObserveAction", "run_id": encode_handle(key),
            "action": {**spike_action("probe.py")},
        }, "unqualified-spike")
        self.assertEqual(result["result"], {
            "type": "Fault", "code": "unavailable", "fail_direction": "closed",
            "reason": "spikes-unavailable"})
        self.assertEqual((len(workspace.calls), artifacts.append_calls, runs.seq, harness.calls),
                         before)
    def test_terminal_run5_spike_is_inert_before_every_side_effect(self):
        bindings = (
            {"kind": "spikes_unavailable"},
            {"kind": "unqualified"},
            _persisted(_qualified(DIGEST_A, DIGEST_B)),
        )
        for binding in bindings:
            with self.subTest(kind=binding["kind"]):
                workspace, harness, runs, artifacts = Workspace(), Harness(), Runs(), Artifacts()
                state = replace(initial(), status="stopped_residual", spike_backend=binding)
                state, values = chain(state)
                key = RunKey(storage_id("p"), storage_id("s"), 1)
                runs.create(key, run_state.encode_state(state))
                for item in values:
                    artifacts.append(key, item)
                coordinator = Coordinator(
                    workspace, harness, runs, artifacts, PROFILE, {}, CountingSelector())
                before = (copy.deepcopy(runs.data[key]), copy.deepcopy(artifacts.values[key]),
                          len(workspace.calls), harness.calls, artifacts.append_calls)
                result = coordinator.handle({
                    "type": "ObserveAction", "run_id": encode_handle(key),
                    "action": {"kind": "spike_request", "claim_id": "C0",
                               "command": "true", "dependent_files": ["x"]},
                }, "terminal-spike")
                self.assertEqual(result["result"]["type"], "Inert")
                self.assertEqual(
                    (runs.data[key], artifacts.values[key], len(workspace.calls), harness.calls,
                     artifacts.append_calls), before)


def legacy_document(state, discriminator):
    """A persisted document of a previous state family: the current state, encoded, re-labelled
    with the legacy discriminator, so only its identity differs (the major run/2 shape predates
    the spike backend binding, so it does not carry one)."""
    current = replace(state, spike_backend={"kind": "spikes_unavailable"})
    document = run_state.encode_state(current)
    document["state_schema"] = discriminator
    if discriminator == "empirica.run/2":
        document.pop("spike_backend")
    return document


class LegacyRejectionTests(unittest.TestCase):
    def test_only_the_one_legacy_id_classifies_as_read_only_legacy_and_is_never_decoded(self):
        state = replace(initial(), spike_backend={"kind": "spikes_unavailable"})
        for discriminator, kind in ((run_state.LEGACY_STATE_SCHEMA_ID, "legacy"),
                                   ("empirica.run/3", "current_corrupt"),
                                   ("empirica.run/6", "current_corrupt")):
            with self.subTest(state_schema=discriminator):
                classified = run_state.classify_and_decode(
                    legacy_document(state, discriminator))
                self.assertEqual(classified.kind, kind)
                self.assertIsNone(classified.state, "neither legacy nor corrupt state is decoded")
        wrong_protocol = legacy_document(state, run_state.LEGACY_STATE_SCHEMA_ID)
        wrong_protocol["protocol"] = "empirica/v3"
        self.assertEqual(run_state.classify_and_decode(wrong_protocol).kind, "current_corrupt")
        missing = legacy_document(state, run_state.LEGACY_STATE_SCHEMA_ID)
        del missing["state_schema"]
        self.assertEqual(run_state.classify_and_decode(missing).kind, "current_corrupt")

    def test_a_legacy_run5_family_run_is_never_resumed_or_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            runs = FilesystemRunRepository(Path(temporary))
            artifacts = Artifacts()
            state, values = chain(initial())
            key = RunKey(storage_id("p"), storage_id("s"), 1)
            runs.create(key, legacy_document(state, run_state.LEGACY_STATE_SCHEMA_ID))
            for item in values:
                artifacts.append(key, item)
            workspace, harness = Workspace(), Harness()
            service = v2.compose(workspace, harness, runs, artifacts, None, PROFILE, {}, None)
            run_id = encode_handle(key)
            path = runs._doc_path(key)
            before_bytes = path.read_bytes()
            before_read = runs.read(key)
            before_artifacts = copy.deepcopy(artifacts.values[key])
            for command in (
                {"type": "ResolveRun", "selector": {"project": "p", "session": "s"}},
                {"type": "GetRun", "run_id": run_id},
                {"type": "RestoreRun", "run_id": run_id},
                {"type": "GetArgument", "run_id": run_id},
                {"type": "EvaluateRun", "run_id": run_id, "intent": "continue"},
                {"type": "ObserveAction", "run_id": run_id,
                 "action": {"kind": "route", "reason": "must not write"}},
                {"type": "ObserveAction", "run_id": run_id,
                 "action": {"kind": "spike_request", "claim_id": "C0",
                            "command": "true", "dependent_files": ["x"]}},
            ):
                with self.subTest(command=command["type"]):
                    result = service._dispatch_validated(
                        {"request_id": command["type"], "command": command})
                    self.assertEqual(result["result"]["type"], "Block")
                    self.assertEqual(result["result"]["reasons"][0]["code"], "run.legacy")
                    protocol.schema_validator("response").validate(result)
            self.assertIsNone(service.trusted_host_runtime(run_id=run_id))
            trusted = service.trusted_evidence_leaf(run_id=run_id, payload={})
            self.assertEqual(trusted["result"]["type"], "Inert")
            after_read = runs.read(key)
            self.assertEqual(path.read_bytes(), before_bytes)
            self.assertEqual(after_read.revision, before_read.revision)
            self.assertEqual(artifacts.values[key], before_artifacts)
            self.assertEqual(workspace.calls, [])
            self.assertEqual(harness.calls, 0)

    def test_older_and_major_families_stay_corrupt_and_cannot_mutate(self):
        for discriminator in ("empirica.run/3", "empirica.run/2"):
            with self.subTest(state_schema=discriminator), tempfile.TemporaryDirectory() as temporary:
                runs = FilesystemRunRepository(Path(temporary))
                artifacts = Artifacts()
                state, values = chain(initial())
                key = RunKey(storage_id("p"), storage_id("s"), 1)
                runs.create(key, legacy_document(state, discriminator))
                for item in values:
                    artifacts.append(key, item)
                workspace, harness = Workspace(), Harness()
                service = v2.compose(workspace, harness, runs, artifacts, None, PROFILE, {}, None)
                run_id = encode_handle(key)
                path = runs._doc_path(key)
                before_bytes = path.read_bytes()
                before_read = runs.read(key)
                before_artifacts = copy.deepcopy(artifacts.values[key])
                for command in (
                    {"type": "ResolveRun", "selector": {"project": "p", "session": "s"}},
                    {"type": "GetRun", "run_id": run_id},
                    {"type": "RestoreRun", "run_id": run_id},
                    {"type": "GetArgument", "run_id": run_id},
                    {"type": "EvaluateRun", "run_id": run_id, "intent": "continue"},
                    {"type": "ObserveAction", "run_id": run_id,
                     "action": {"kind": "route", "reason": "must not write"}},
                    {"type": "ObserveAction", "run_id": run_id,
                     "action": {"kind": "spike_request", "claim_id": "C0",
                                "command": "true", "dependent_files": ["x"]}},
                ):
                    with self.subTest(command=command["type"]):
                        result = service._dispatch_validated(
                            {"request_id": command["type"], "command": command})
                        self.assertEqual(result["result"]["type"], "Block")
                        self.assertEqual(result["result"]["reasons"][0]["code"], "run.corrupt")
                self.assertIsNone(service.trusted_host_runtime(run_id=run_id))
                trusted = service.trusted_evidence_leaf(run_id=run_id, payload={})
                self.assertEqual(trusted["result"]["type"], "Inert")
                after_read = runs.read(key)
                self.assertEqual(path.read_bytes(), before_bytes)
                self.assertEqual(after_read.revision, before_read.revision)
                self.assertEqual(artifacts.values[key], before_artifacts)
                self.assertEqual(workspace.calls, [])
                self.assertEqual(harness.calls, 0)

    def test_start_run_after_a_legacy_latest_starts_a_fresh_generation_without_touching_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            runs = FilesystemRunRepository(Path(temporary))
            artifacts = Artifacts()
            state, values = chain(initial())
            key = RunKey(storage_id("p"), storage_id("s"), 1)
            runs.create(key, legacy_document(state, run_state.LEGACY_STATE_SCHEMA_ID))
            for item in values:
                artifacts.append(key, item)
            before_bytes = runs._doc_path(key).read_bytes()
            before_artifacts = copy.deepcopy(artifacts.values[key])
            workspace, harness = Workspace(), Harness()
            service = v2.compose(workspace, harness, runs, artifacts, None, PROFILE, {}, None)
            started = service._dispatch_validated({"request_id": "start", "command": {
                "type": "StartRun", "control_mode": "deliberative",
                "invocation": dict(TEST_INVOCATION), "selector": {"project": "p", "session": "s"},
                "goal": "g"}})
            self.assertEqual(started["result"]["type"], "Allow")
            fresh = started["result"]["run"]["id"]
            self.assertNotEqual(fresh, encode_handle(key), "a fresh generation, not a resume")
            read = service._dispatch_validated({"request_id": "read", "command": {
                "type": "ResolveRun", "selector": {"project": "p", "session": "s"}}})
            self.assertEqual(read["result"]["type"], "Allow")
            self.assertEqual(read["result"]["run"]["id"], fresh)
            self.assertEqual(runs._doc_path(key).read_bytes(), before_bytes,
                            "the legacy file is never rewritten")
            self.assertEqual(artifacts.values[key], before_artifacts)
            legacy_read = service._dispatch_validated({"request_id": "legacy", "command": {
                "type": "GetRun", "run_id": encode_handle(key)}})
            self.assertEqual(legacy_read["result"]["type"], "Block")
            self.assertEqual(legacy_read["result"]["reasons"][0]["code"], "run.legacy")


if __name__ == "__main__":
    unittest.main()
