#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from adapters.sandbox import generic_admission as admission
from adapters.sandbox import planner
from application.generic_spike import GenericQualifiedBackend
from core import spike_plan
from core.spike_plan_draft import canonicalize_spike_plan_draft, seal_spike_plan

D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64
C0 = "a" * 64
N0 = "b" * 64
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "docker" /
                      "generic-linux-v27.json").read_text(encoding="utf-8"))


def backend(profile="docker-isolated-v1"):
    return GenericQualifiedBackend("docker", profile, D0, D1)


def request(profile="docker-isolated-v1", *, argv=None, environment=None):
    draft = canonicalize_spike_plan_draft({
        "schema": "empirica.spike-plan-draft/2", "image": "python:3.12",
        "argv": argv or ["python", "/inputs/a.py", "--exact"],
        "inputs": ["a.py"], "environment": environment or {"MODE": "test"},
    })
    image = spike_plan.ResolvedImage(
        draft.image, D1, "linux", "linux", "x86_64", "amd64", ())
    captured = (spike_plan.CapturedFile("a.py", D0, 1),)
    plan = seal_spike_plan(draft, image, captured, engine_ncpu=8, network_profile=profile)
    return spike_plan.SealedSpikeRequest(plan, spike_plan.FRAMEWORK_SPIKE_POLICY)


def operation(profile="docker-isolated-v1"):
    return planner.compile_docker_operation(
        request(profile), backend(profile), "run-1", "o-" + "1" * 32,
        "stg-" + "2" * 32)


def static_document(op, *, network_id=None):
    """Hydrate only operation identities into an independently pinned Docker inspect document."""
    document = copy.deepcopy(FIXTURE["static_container_inspect"])
    document["Config"].update(
        Image=op.plan.image.image_id, Entrypoint=[op.plan.argv[0]],
        Cmd=list(op.plan.argv[1:]),
        Env=["PATH=/usr/local/bin:/usr/bin:/bin",
             *[f"{key}={value}" for key, value in op.container.environment]])
    document["Config"]["Labels"].update(dict(op.container.labels))
    if network_id is not None:
        document["HostConfig"]["NetworkMode"] = network_id
        document["Networks"] = {op.network.name: {"NetworkID": network_id, "EndpointID": ""}}
    return document


class PlannerTests(unittest.TestCase):
    def test_isolated_compiles_exact_single_container_policy(self):
        op = operation()
        self.assertIsNone(op.network)
        self.assertEqual([item.kind for item in op.commands], [
            "container_create", "container_static_inspect", "container_start_attach",
            "container_terminal_inspect", "container_remove", "container_absence"])
        argv = op.command("container_create").argv
        self.assertEqual(argv[:2], ("container", "create"))
        for pair in (
            ("--platform", "linux/amd64"), ("--pull", "never"),
            ("--network", "none"), ("--user", "65534:65534"),
            ("--workdir", "/work"), ("--cpus", "2"),
            ("--memory", "2147483648b"), ("--memory-swap", "2147483648b"),
            ("--pids-limit", "256"), ("--shm-size", "67108864b"),
            ("--entrypoint", "python"),
        ):
            index = argv.index(pair[0])
            self.assertEqual(argv[index:index + 2], pair)
        self.assertIn("type=bind,source=staging-token:stg-" + "2" * 32
                      + ",destination=/inputs,readonly,bind-propagation=rprivate", argv)
        self.assertIn("/work:rw,nodev,nosuid,exec,size=536870912,mode=1777", argv)
        self.assertIn("/tmp:rw,nodev,nosuid,noexec,size=67108864,mode=1777", argv)
        self.assertNotIn("--pid", argv)
        self.assertNotIn("--uts", argv)
        self.assertEqual(
            {item for item in argv[:argv.index(D1)] if item.startswith("--")},
            set(planner.CONTAINER_CREATE_FLAGS))
        self.assertEqual(argv[-3:], (D1, "/inputs/a.py", "--exact"))
        self.assertEqual(op.request.operation_timeout_seconds, 210)
        self.assertEqual(op.policy.workload_timeout_seconds, 120)

    def test_networked_compiles_one_noninternal_private_bridge(self):
        op = operation("docker-networked-v1")
        self.assertIsNotNone(op.network)
        self.assertEqual(op.command("network_create").argv[:4],
                         ("network", "create", "--driver", "bridge"))
        self.assertNotIn("--internal", op.command("network_create").argv)
        kinds = [item.kind for item in op.commands]
        self.assertIn("network_prestart_inspect", kinds)
        self.assertLess(kinds.index("network_prestart_inspect"),
                        kinds.index("container_start_attach"))
        create = op.command("container_create").argv
        self.assertEqual(create[create.index("--network") + 1], op.network.full_id_token)
        self.assertNotIn("--publish", create)
        self.assertNotIn("--network-alias", create)

    def test_independent_cli_contract_forbids_invalid_default_namespace_flags(self):
        contract = FIXTURE["docker_cli_create_contract"]
        create = operation().command("container_create").argv
        for flag in contract["forbidden_default_namespace_flags"]:
            self.assertNotIn(flag, create)
        create_flags = {item for item in create[:create.index(D1)] if item.startswith("--")}
        self.assertTrue(create_flags.issubset(planner.CONTAINER_CREATE_FLAGS))
        self.assertEqual(create_flags, {
            "--cap-drop", "--cgroupns", "--cidfile", "--cpus", "--entrypoint", "--env",
            "--ipc", "--label", "--memory", "--memory-swap", "--mount", "--name",
            "--network", "--pids-limit", "--platform", "--pull", "--read-only",
            "--security-opt", "--shm-size", "--tmpfs", "--ulimit", "--user", "--workdir",
        })
        for invalid in ("--pid", "--uts", "--privileged", "--unknown-g12"):
            changed = list(create)
            changed.insert(changed.index("--read-only"), invalid)
            if invalid in {"--pid", "--uts", "--unknown-g12"}:
                changed.insert(changed.index("--read-only"), "private")
            with self.subTest(invalid=invalid), self.assertRaises(planner.DockerPlanCompileError):
                planner._admit_compiled_create_flags(tuple(changed), D1)

    def test_labels_bind_run_request_plan_policy_and_backend(self):
        op = operation()
        labels = dict(op.container.labels)
        self.assertEqual(labels["io.siffran.empirica.run"], "run-1")
        self.assertEqual(labels["io.siffran.empirica.request"], op.request.digest)
        self.assertEqual(labels["io.siffran.empirica.plan"], op.plan.digest)
        self.assertEqual(labels["io.siffran.empirica.policy"], op.policy.digest)
        self.assertEqual(labels["io.siffran.empirica.backend"], D1)
        self.assertEqual(op.to_data()["destructive_authority"],
                         "durably-recorded-full-ids-only")

    def test_compiler_rejects_nonexact_or_mismatched_authority(self):
        with self.assertRaises(planner.DockerPlanCompileError):
            planner.compile_docker_operation(request(), backend("docker-networked-v1"),
                "run-1", "o-" + "1" * 32, "stg-" + "2" * 32)
        for run, opid, token in (("bad run", "o-" + "1" * 32, "stg-" + "2" * 32),
                                  ("run-1", "bad", "stg-" + "2" * 32),
                                  ("run-1", "o-" + "1" * 32, "bad")):
            with self.assertRaises(planner.DockerPlanCompileError):
                planner.compile_docker_operation(request(), backend(), run, opid, token)

    def test_inspect_templates_are_bounded_and_missing_key_safe(self):
        self.assertNotIn("{{json .}}", planner.CONTAINER_INSPECT_FORMAT)
        self.assertNotIn("RepoDigests", planner.IMAGE_INSPECT_FORMAT)
        self.assertNotIn('"Env"', planner.IMAGE_INSPECT_FORMAT)
        self.assertNotIn('"Labels"', planner.IMAGE_INSPECT_FORMAT)
        for key in ("Volumes", "Entrypoint", "Cmd", "Env", "Labels", "Mounts",
                    "Networks", "Error"):
            self.assertIn("index", planner.IMAGE_INSPECT_FORMAT
                          if key == "Volumes" else planner.CONTAINER_INSPECT_FORMAT)


class AdmissionTests(unittest.TestCase):
    def test_static_admits_benign_unknowns_nulls_sets_and_inherited_environment(self):
        op = operation()
        document = static_document(op)
        document["State"].update(Status="exited", ExitCode=137, Running=False)
        admitted = admission.admit_static_container(
            op, document, container_full_id=C0,
            network_full_id=None, staging_path=Path("/stage/root"))
        self.assertEqual(admitted.full_id, C0)
        self.assertEqual(admitted.realization_digest, D0)
        self.assertEqual(admitted.network_mode, "none")

    def test_every_security_relevant_static_field_fails_closed(self):
        op = operation()
        mutations = [
            lambda d: d.__setitem__("Id", "c" * 64),
            lambda d: d.__setitem__("Image", "bad"),
            lambda d: d["Config"].__setitem__("Image", D0),
            lambda d: d["Config"].__setitem__("Entrypoint", ["sh"]),
            lambda d: d["Config"].__setitem__("Cmd", ["wrong"]),
            lambda d: d["Config"].__setitem__("WorkingDir", "/tmp"),
            lambda d: d["Config"].__setitem__("User", "0:0"),
            lambda d: d["Config"].__setitem__("Env", ["HOME=/bad", "TMPDIR=/tmp", "MODE=test"]),
            lambda d: d["Config"]["Labels"].__setitem__("io.siffran.empirica.run", "other"),
            lambda d: d["Config"].__setitem__("ExposedPorts", {"80/tcp": {}}),
            lambda d: d["Config"].__setitem__("Volumes", {"/data": {}}),
            lambda d: d["HostConfig"].__setitem__("Privileged", True),
            lambda d: d["HostConfig"].__setitem__("PidMode", "host"),
            lambda d: d["HostConfig"].__setitem__("UTSMode", "private"),
            lambda d: d["HostConfig"].__setitem__("PortBindings", {"80/tcp": [{"HostPort": "80"}]}),
            lambda d: d["HostConfig"].__setitem__("Devices", [{"PathOnHost": "/dev/null"}]),
            lambda d: d["HostConfig"].__setitem__("DeviceCgroupRules", ["c 1:3 rwm"]),
            lambda d: d["HostConfig"].__setitem__("CapAdd", ["SYS_ADMIN"]),
            lambda d: d["HostConfig"].__setitem__("CgroupnsMode", "host"),
            lambda d: d["HostConfig"].__setitem__("ReadonlyRootfs", False),
            lambda d: d["HostConfig"].__setitem__("SecurityOpt", []),
            lambda d: d["HostConfig"].__setitem__("CapDrop", []),
            lambda d: d["HostConfig"].__setitem__("Memory", 1),
            lambda d: d["HostConfig"].__setitem__("MemorySwap", -1),
            lambda d: d["HostConfig"].__setitem__("NanoCpus", 1),
            lambda d: d["HostConfig"].__setitem__("PidsLimit", 1),
            lambda d: d["HostConfig"]["Ulimits"][0].__setitem__("Hard", 1),
            lambda d: d["HostConfig"].__setitem__("ShmSize", 1),
            lambda d: d["HostConfig"]["Tmpfs"].__setitem__("/tmp", "rw,exec"),
            lambda d: d["Mounts"][0].__setitem__("Source", "/other"),
            lambda d: d["Mounts"].append({"Type": "volume", "Destination": "/data", "RW": True}),
            lambda d: d["HostConfig"].__setitem__("NetworkMode", "default"),
        ]
        for index, mutation in enumerate(mutations):
            document = static_document(op)
            mutation(document)
            with self.subTest(index=index), self.assertRaises(admission.DockerObservationError):
                admission.admit_static_container(
                    op, document, container_full_id=C0,
                    network_full_id=None, staging_path=Path("/stage/root"))

    def test_daemon_real_pid_and_uts_default_forms_are_narrow(self):
        op = operation()
        for pid in ("", "private"):
            document = static_document(op)
            document["HostConfig"]["PidMode"] = pid
            admission.admit_static_container(
                op, document, container_full_id=C0,
                network_full_id=None, staging_path=Path("/stage/root"))
        for uts in ("private", "host"):
            document = static_document(op)
            document["HostConfig"]["UTSMode"] = uts
            with self.assertRaises(admission.DockerObservationError):
                admission.admit_static_container(
                    op, document, container_full_id=C0,
                    network_full_id=None, staging_path=Path("/stage/root"))

    def test_networked_mode_accepts_full_id_or_name_but_not_default_bridge(self):
        op = operation("docker-networked-v1")
        for mode in (N0, op.network.name):
            document = static_document(op, network_id=N0)
            document["HostConfig"]["NetworkMode"] = mode
            admission.admit_static_container(
                op, document, container_full_id=C0,
                network_full_id=N0, staging_path=Path("/stage/root"))
        document = static_document(op, network_id=N0)
        document["HostConfig"]["NetworkMode"] = "bridge"
        with self.assertRaises(admission.DockerObservationError):
            admission.admit_static_container(
                op, document, container_full_id=C0,
                network_full_id=N0, staging_path=Path("/stage/root"))

    def test_exact_absence_accepts_pinned_docker_variants_only_for_recorded_id(self):
        empty = admission.stream(b"")
        for resource, key, full in (
            ("container", "container_no_such", C0),
            ("container", "container_not_found", C0),
            ("network", "network_no_such", N0),
            ("network", "network_not_found", N0),
        ):
            argv = (resource, "inspect", full)
            raw = admission.RawProcessObservation(
                argv, True, 1, False, False, False, empty,
                admission.stream(FIXTURE["absence_stderr"][key].encode()))
            admission.admit_exact_absence(raw, argv, resource)
            for bad in (
                FIXTURE["absence_stderr"][key].replace(full, "c" * 64),
                FIXTURE["absence_stderr"][key] + " ambiguous trailing text",
            ):
                changed = admission.RawProcessObservation(
                    argv, True, 1, False, False, False, empty,
                    admission.stream(bad.encode()))
                with self.assertRaises(admission.DockerObservationError):
                    admission.admit_exact_absence(changed, argv, resource)

    def test_prestart_network_requires_owned_bridge_with_no_live_endpoints(self):
        op = operation("docker-networked-v1")
        document = {"Id": N0, "Driver": "bridge", "Internal": False,
                    "Labels": dict(op.network.labels), "Containers": {}}
        admitted = admission.admit_prestart_network(op, document, network_full_id=N0)
        self.assertEqual(admitted.attached_container_ids, ())
        document["Containers"] = {C0: {}}
        with self.assertRaises(admission.DockerObservationError):
            admission.admit_prestart_network(op, document, network_full_id=N0)

    def test_running_network_requires_exact_single_verdict_membership(self):
        op = operation("docker-networked-v1")
        document = {"Id": N0, "Driver": "bridge", "Internal": False,
                    "Labels": dict(op.network.labels), "Containers": {C0: {"ignored": True}}}
        result = admission.admit_network_membership(
            op, document, network_full_id=N0, container_full_id=C0)
        self.assertEqual(result.attached_container_ids, (C0,))
        for mutation in (
            lambda d: d.__setitem__("Internal", True),
            lambda d: d.__setitem__("Driver", "host"),
            lambda d: d["Containers"].__setitem__("c" * 64, {}),
            lambda d: d.__setitem__("Containers", {}),
        ):
            changed = copy.deepcopy(document)
            mutation(changed)
            with self.assertRaises(admission.DockerObservationError):
                admission.admit_network_membership(
                    op, changed, network_full_id=N0, container_full_id=C0)

    def test_terminal_decoder_handles_natural_125_137_oom_and_failed_start(self):
        empty = admission.stream(b"")
        def attach(code):
            process = admission.RawProcessObservation(
                ("docker", "start"), True, code, False, False, False, empty, empty)
            return admission.RawAttachObservation(process, True, False, code)
        base = {"Id": C0,
                "State": {"Status": "exited", "Running": False, "ExitCode": 125,
                          "OOMKilled": False, "Error": ""},
                "Networks": {"none": {"NetworkID": N0, "EndpointID": ""}}}
        terminal = admission.admit_terminal_container(
            operation(), base, container_full_id=C0, network_full_id=None)
        self.assertIs(admission.admit_natural_verdict(terminal, attach(125)),
                      spike_plan.NaturalVerdict.FAIL)
        for code, oom in ((137, False), (1, True)):
            changed = copy.deepcopy(base)
            changed["State"].update(ExitCode=code, OOMKilled=oom)
            terminal = admission.admit_terminal_container(
                operation(), changed, container_full_id=C0, network_full_id=None)
            with self.assertRaises(admission.DockerObservationError):
                admission.admit_natural_verdict(terminal, attach(code))
        failed = admission.admit_terminal_container(operation(), {
            "Id": C0,
            "State": {"Status": "created", "Running": False, "ExitCode": 127,
                      "OOMKilled": False, "Error": "exec failed"},
            "Networks": {"none": {"NetworkID": "", "EndpointID": ""}}},
            container_full_id=C0, network_full_id=None)
        self.assertEqual(failed.exit_code, 127)
        with self.assertRaises(admission.DockerObservationError):
            admission.admit_natural_verdict(failed, attach(127))


if __name__ == "__main__":
    unittest.main()
