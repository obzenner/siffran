#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from adapters.sandbox import common, generic_admission as admission
from adapters.sandbox import generic_authority, generic_effects, generic_staging
from adapters.sandbox import generic_execution as execution
from adapters.sandbox import generic_journal as journal
from adapters.sandbox import planner, process
from application import generic_spike
from core import spike_plan
from core.canonical import canonical_digest
from test_docker_planner import C0, D0, N0, FIXTURE, backend, request, static_document
from test_generic_authority import runtime
from test_generic_staging import snapshot

AUTHORITY = generic_authority.authority_facts(runtime())
FOREIGN = "c" * 64


class Crash(BaseException):
    """The controller was SIGKILLed: no further effect, cleanup, or journal write happens."""


class FakeDocker:
    def __init__(self, operation, *, exit_code=0, timeout=False, overflow=None, oom=False,
                 failed_start=False, malformed=False, create_loss=False, network_loss=False,
                 candidates=None, foreign=False, cleanup_failure=None, warning="warning",
                 no_cid=False, create_failure=False, kill_create=None, config=None,
                 host_config=None, crash_after=None):
        self.operation = operation
        self.exit_code = exit_code
        self.timeout = timeout
        self.overflow = overflow
        self.oom = oom
        self.failed_start = failed_start
        self.malformed = malformed
        self.create_loss = create_loss
        self.network_loss = network_loss
        self.candidates = candidates
        self.foreign = foreign
        self.cleanup_failure = cleanup_failure
        self.warning = warning
        self.no_cid = no_cid
        self.create_failure = create_failure
        # docker/cli killed after newCIDFile (empty file) with the daemon create True/False done.
        self.kill_create = kill_create
        self.config = config or {}
        self.host_config = host_config or {}
        self.crash_after = crash_after
        self.effects_seen = 0
        self.container_present = False
        self.network_present = False
        self.attached = False
        self.running = False
        self.calls = []
        self.destructive = []
        self.staging_override = None
        self.container_candidates = None
        self.foreign_container = None

    def _tick(self):
        self.effects_seen += 1
        if self.crash_after is not None and self.effects_seen > self.crash_after:
            raise Crash("controller killed")

    @staticmethod
    def raw(argv, code=0, stdout=b"", stderr=b"", *, timeout=False, limited=False):
        if isinstance(stdout, str):
            stdout = stdout.encode()
        if isinstance(stderr, str):
            stderr = stderr.encode()
        return admission.RawProcessObservation(
            tuple(argv), True, code, timeout, limited, False,
            admission.stream(stdout), admission.stream(stderr))

    def network_doc(self):
        containers = {C0: {}} if self.running else {}
        if self.foreign:
            containers[FOREIGN] = {}
        return {"Id": N0, "Driver": "bridge", "Internal": False,
                "Labels": dict(self.operation.network.labels), "Containers": containers}

    def container_doc(self, staging):
        document = static_document(self.operation, network_id=N0 if self.operation.network else None)
        document["Id"] = C0
        document["Mounts"][0]["Source"] = str(staging)
        document["Config"].update(copy.deepcopy(self.config))
        document["HostConfig"].update(copy.deepcopy(self.host_config))
        if self.running:
            document["State"] = {"Status": "running", "Running": True,
                                 "ExitCode": 0, "OOMKilled": False, "Error": ""}
            if self.operation.network:
                document["Networks"] = {self.operation.network.name: {
                    "NetworkID": N0, "EndpointID": "3" * 64}}
        elif self.attached:
            if self.failed_start:
                document["State"] = {"Status": "created", "Running": False,
                                     "ExitCode": 127, "OOMKilled": False,
                                     "Error": "OCI runtime create failed"}
            else:
                document["State"] = {"Status": "exited", "Running": False,
                                     "ExitCode": self.exit_code, "OOMKilled": self.oom,
                                     "Error": ""}
            if self.operation.network:
                document["Networks"] = {self.operation.network.name: {
                    "NetworkID": N0, "EndpointID": ""}}
        return document

    def _staging(self):
        staging = next(Path(item.split("source=", 1)[1].split(",", 1)[0])
                       for item in self.operation.command("container_create").argv
                       if "source=staging-token:" in item)
        if self.staging_override is not None:
            staging = self.staging_override
        # The executed create argv is the authoritative concrete staging path.
        for call in self.calls:
            if call[:2] == ("container", "create"):
                mount = next(item for item in call if item.startswith("type=bind"))
                staging = Path(mount.split("source=", 1)[1].split(",", 1)[0])
        return staging

    def _absent(self, argv):
        """docker/cli: a bare inspect flushes `[]\\n`, a `--format` one a bare newline; rc 1."""
        resource, full_id = argv[0], argv[-1]
        key = "container_no_such" if resource == "container" else "network_not_found"
        sample = C0 if resource == "container" else N0
        stdout = "\n" if "--format" in argv else FIXTURE["absence_stdout"]
        return self.raw(argv, FIXTURE["absence_returncode"], stdout=stdout,
                        stderr=FIXTURE["absence_stderr"][key].replace(sample, full_id))

    def _present(self, argv):
        return self.raw(argv, stdout=json.dumps([{"Id": argv[-1]}], indent=4) + "\n")

    def run(self, argv, *, timeout_seconds, output_limit_bytes=256 * 1024):
        argv = tuple(argv)
        self._tick()
        self.calls.append(argv)
        if argv[:2] == ("network", "create"):
            self.network_present = True
            if self.network_loss:
                raise OSError("lost after create")
            return self.raw(argv, stdout=N0 + "\n", stderr=self.warning)
        if argv[:2] == ("container", "ls"):
            values = self.container_candidates
            if values is None:
                values = [C0] if self.container_present else []
            return self.raw(argv, stdout="".join(item + "\n" for item in values))
        if argv[:2] == ("network", "ls"):
            values = self.candidates
            if values is None:
                values = [N0] if self.network_present else []
            return self.raw(argv, stdout="".join(item + "\n" for item in values))
        if argv[:2] == ("container", "create"):
            cidfile = Path(argv[argv.index("--cidfile") + 1])
            if cidfile.exists():
                return self.raw(
                    argv, 125,
                    stderr=FIXTURE["docker_cli_create_contract"]["cidfile_preexisting_error"])
            if self.kill_create is not None:
                fd = os.open(cidfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                self.container_present = self.kill_create
                return self.raw(argv, -9, timeout=True)
            self.container_present = True
            if self.create_failure:
                cidfile.write_text(C0)
                cidfile.chmod(0o600)
                cidfile.unlink()
                self.container_present = False
                return self.raw(argv, 125, stderr="daemon rejected create")
            if not self.no_cid:
                fd = os.open(cidfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(C0)
            if self.create_loss:
                raise OSError("lost after cidfile")
            return self.raw(argv, stdout=C0 + "\n", stderr=self.warning)
        if argv[:2] == ("container", "inspect") and self.foreign_container is not None \
                and argv[-1] == self.foreign_container["Id"]:
            if "--format" in argv:
                return self.raw(argv, stdout=json.dumps(self.foreign_container))
            return self._present(argv)
        if argv[:2] == ("container", "inspect") and "--format" in argv:
            if not self.container_present or argv[-1] != C0:
                return self._absent(argv)
            if self.malformed and self.attached:
                return self.raw(argv, stdout="{")
            return self.raw(argv, stdout=json.dumps(self.container_doc(self._staging())),
                            stderr=self.warning)
        if argv[:2] == ("network", "inspect") and "--format" in argv:
            if not self.network_present or argv[-1] != N0:
                return self._absent(argv)
            return self.raw(argv, stdout=json.dumps(self.network_doc()), stderr=self.warning)
        if argv[:2] == ("container", "rm"):
            self.destructive.append(argv)
            if self.cleanup_failure == "container":
                return self.raw(argv, 1, stderr="daemon error")
            if argv[-1] == C0:
                self.container_present = False
            return self.raw(argv, stdout=argv[-1])
        if argv[:2] == ("network", "rm"):
            self.destructive.append(argv)
            if self.cleanup_failure == "network":
                return self.raw(argv, 1, stderr="daemon error")
            if argv[-1] == N0:
                self.network_present = False
            return self.raw(argv, stdout=argv[-1])
        if argv[:2] == ("container", "inspect"):
            if (self.container_present and argv[-1] == C0) or self.cleanup_failure == "container":
                return self._present(argv)
            return self._absent(argv)
        if argv[:2] == ("network", "inspect"):
            if (self.network_present and argv[-1] == N0) or self.cleanup_failure == "network":
                return self._present(argv)
            return self._absent(argv)
        raise AssertionError(argv)

    def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
        argv = tuple(argv)
        self._tick()
        self.calls.append(argv)
        self.attached = True
        self.running = True
        if self.failed_start:
            self.running = False
            return admission.RawAttachObservation(
                self.raw(argv, 125, stderr="start failed"), True, False, 125)
        stdout = b"x" * (stdout_limit + 1) if self.overflow == "stdout" else b"out"
        stderr = b"x" * (stderr_limit + 1) if self.overflow == "stderr" else b"err"
        raw = self.raw(argv, -9 if self.timeout or self.overflow else self.exit_code,
                       stdout, stderr, timeout=self.timeout, limited=bool(self.overflow))
        natural = not self.timeout and not self.overflow
        self.running = False
        return admission.RawAttachObservation(raw, natural, not natural,
                                              self.exit_code if natural else None)

    def close(self):
        pass


class StubRunner:
    """A process runner over FakeDocker, so the real `ConcreteGenericEffects` vocabulary is on path."""
    def __init__(self, fake):
        self.fake = fake
        self.commands = []

    @staticmethod
    def _result(raw):
        return process.CommandResult(
            raw.returncode, raw.timed_out, raw.output_limited,
            process._capture_bytes(raw.stdout.data), process._capture_bytes(raw.stderr.data), 0)

    def run(self, argv, *, timeout_seconds, output_limit_bytes=None, stdout_limit_bytes=None,
            stderr_limit_bytes=None, env=None):
        self.commands.append(tuple(argv))
        docker = tuple(argv[1:])
        if docker[:2] == ("container", "start"):
            return self._result(self.fake.attach(
                docker, timeout_seconds=timeout_seconds, stdout_limit=stdout_limit_bytes,
                stderr_limit=stderr_limit_bytes).process)
        return self._result(self.fake.run(docker, timeout_seconds=timeout_seconds))


def concrete(fake, root):
    cli = Path(sys.executable).resolve()
    config = root / "docker-config"
    config.mkdir(mode=0o700, exist_ok=True)
    runner = StubRunner(fake)
    effects = generic_effects.ConcreteGenericEffects(
        cli, {"LANG": "C", "LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock",
              "DOCKER_CONFIG": str(config)}, root, runner=runner)
    return effects, runner


def fixture(profile="docker-isolated-v1", argv=None):
    capture = snapshot((("a.py", b"x"),))
    captured = (spike_plan.CapturedFile(
        "a.py", "sha256:" + hashlib.sha256(b"x").hexdigest(), 1),)
    plan = replace(request(profile, argv=argv).plan, captured_files=captured,
                   capture_digest=canonical_digest([item.to_data() for item in captured]))
    sealed = spike_plan.SealedSpikeRequest(plan, spike_plan.FRAMEWORK_SPIKE_POLICY)
    op = planner.compile_docker_operation(
        sealed, backend(profile), "run-1", "o-" + "1" * 32, "stg-" + "2" * 32)
    temporary = tempfile.TemporaryDirectory()
    store = common.ExecutionStore(Path(temporary.name).resolve() / "sandbox")
    store.prepare_execution_scaffolding()
    journal.GenericJournalStore(store).prepare()
    return temporary, store, op, capture


def execute(store, op, capture, effects, *, authority=None, postflight=None):
    return execution.execute_generic_operation(
        store, op, capture, effects, backend_deadline=time.monotonic() + 60,
        postflight=postflight or (lambda deadline: op.backend.execution_binding_digest),
        authority=AUTHORITY if authority is None else authority)


def recover(store, effects, *, authority=None):
    return execution.recover_generic_operations(
        store, effects, authority=AUTHORITY if authority is None else authority,
        deadline=time.monotonic() + 60)


class ExecutionCase(unittest.TestCase):
    def fixture(self, profile="docker-isolated-v1", argv=None):
        temporary, store, op, capture = fixture(profile, argv)
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name).resolve(), store, op, capture

    def assertNoResidue(self, store, op, fake):
        self.assertEqual(journal.GenericJournalStore(store).scan(), ())
        self.assertFalse((store.staging / op.operation_id).exists())
        self.assertFalse(fake.container_present)
        self.assertFalse(fake.network_present)
        for argv in fake.destructive:
            self.assertIn(argv[-1], {C0, N0})
            self.assertNotIn(op.container.name, argv)

    def assertRetained(self, store, op, fake):
        rows = journal.GenericJournalStore(store).scan()
        self.assertEqual([row.operation.operation_id for row in rows], [op.operation_id])
        self.assertFalse(fake.destructive)
        return rows[0]


class ExecutionLifecycleTests(ExecutionCase):
    def run_case(self, profile="docker-isolated-v1", **options):
        _, store, op, capture = self.fixture(profile)
        fake = FakeDocker(op, **options)
        result = execute(store, op, capture, fake)
        return store, op, fake, result

    def test_isolated_and_networked_success_zero(self):
        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            with self.subTest(profile=profile):
                store, op, fake, result = self.run_case(profile)
                self.assertIs(result.verdict, generic_spike.GenericVerdict.PASS)
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(result.realization_digest, D0)
                self.assertNoResidue(store, op, fake)

    def test_main_path_performs_exactly_one_static_inspection(self):
        _, _, fake, _ = self.run_case()
        formatted = [call for call in fake.calls
                     if call[:2] == ("container", "inspect") and "--format" in call]
        self.assertEqual(len(formatted), 2)  # one static, one terminal
        self.assertFalse(any(call[:2] == ("container", "ls") for call in fake.calls))

    def test_static_realization_digest_is_journaled_privately(self):
        _, store, op, capture = self.fixture()
        fake = FakeDocker(op)
        seen = []
        original = journal.GenericJournalStore.transition

        def spy(self, operation_id, state, kind, diagnostic=None):
            if diagnostic is not None:
                seen.append(dict(diagnostic))
            return original(self, operation_id, state, kind, diagnostic)
        journal.GenericJournalStore.transition = spy
        self.addCleanup(setattr, journal.GenericJournalStore, "transition", original)
        execute(store, op, capture, fake)
        static = [row for row in seen if row["kind"] == "container_static_inspect"]
        self.assertEqual([row["realization_digest"] for row in static], [D0])

    def test_docker_owned_cidfile_is_absent_before_create_and_removed_after_finish(self):
        _, _, fake, result = self.run_case()
        self.assertEqual(result.exit_code, 0)
        create = next(call for call in fake.calls if call[:2] == ("container", "create"))
        cidfile = Path(create[create.index("--cidfile") + 1])
        self.assertFalse(cidfile.exists())

    def test_every_create_without_a_cidfile_id_defers_the_name_lookup_to_recovery(self):
        class SignalKilled(FakeDocker):
            def run(self, argv, **kw):
                raw = super().run(argv, **kw)
                if tuple(argv[:2]) == ("container", "create"):
                    return self.raw(argv, -9)
                return raw

        forms = (  # (fake type, options, daemon created a container)
            (FakeDocker, {"create_failure": True}, False),  # natural 125, cidfile removed
            (FakeDocker, {"no_cid": True}, True),  # natural 0 without a written ID
            (FakeDocker, {"kill_create": True}, True),  # killed, empty cidfile
            (FakeDocker, {"kill_create": False}, False),
            (SignalKilled, {"kill_create": False}, False),  # signal death, no timeout
        )
        for fake_type, options, created in forms:
            with self.subTest(fake=fake_type.__name__, **options):
                _, store, op, capture = self.fixture()
                fake = fake_type(op, **options)
                with self.assertRaises(execution.GenericExecutionUnavailable) as caught:
                    execute(store, op, capture, fake)
                self.assertIn("unresolved without a cidfile ID", str(caught.exception))
                self.assertFalse(any(call[:2] == ("container", "ls") for call in fake.calls))
                row = self.assertRetained(store, op, fake)
                self.assertIsNone(row.container_id)
                self.assertEqual(fake.container_present, created)
                recover(store, fake)
                self.assertNoResidue(store, op, fake)
                self.assertEqual(bool(fake.destructive), created)

    def test_malformed_cidfile_falls_through_to_the_exact_name_candidate(self):
        for content in (C0 + "\n", C0[:40], "not hex at all", ""):
            with self.subTest(content=content):
                _, store, op, capture = self.fixture()
                fake = FakeDocker(op, kill_create=True)
                with self.assertRaises(execution.GenericExecutionUnavailable):
                    execute(store, op, capture, fake)
                row = self.assertRetained(store, op, fake)
                row.cidfile.write_text(content)
                recover(store, fake)
                self.assertNoResidue(store, op, fake)
                self.assertEqual(fake.destructive,
                                 [("container", "rm", "--force", "--volumes", C0)])

    def test_foreign_labelled_same_name_candidate_is_refused_without_destruction(self):
        _, store, op, capture = self.fixture()
        fake = FakeDocker(op, create_failure=True)
        foreign = static_document(op)
        foreign.update(Id=FOREIGN)
        foreign["Config"]["Labels"]["io.siffran.empirica.operation"] = "o-" + "f" * 32
        fake.foreign_container = foreign
        fake.container_candidates = [FOREIGN]
        with self.assertRaises(execution.GenericExecutionUnavailable):
            execute(store, op, capture, fake)
        row = self.assertRetained(store, op, fake)
        self.assertIsNone(row.container_id)
        with self.assertRaises(execution.GenericExecutionUnavailable) as caught:
            recover(store, fake)
        self.assertIn("ownership was refused", str(caught.exception))
        self.assertRetained(store, op, fake)

    def test_natural_125_is_fail_and_warnings_do_not_invalidate(self):
        _, _, _, result = self.run_case(exit_code=125, warning="harmless warning")
        self.assertIs(result.verdict, generic_spike.GenericVerdict.FAIL)
        self.assertEqual(result.exit_code, 125)

    def test_unavailable_outcomes_cleanup_without_evidence(self):
        cases = [
            {"exit_code": 137}, {"timeout": True}, {"overflow": "stdout"},
            {"overflow": "stderr"}, {"oom": True}, {"failed_start": True},
            {"malformed": True},
        ]
        for options in cases:
            _, store, op, capture = self.fixture()
            fake = FakeDocker(op, **options)
            with self.subTest(options=options), self.assertRaises(execution.GenericExecutionUnavailable):
                execute(store, op, capture, fake)
            self.assertNoResidue(store, op, fake)

    def test_create_cli_loss_recovers_only_from_secure_cidfile(self):
        _, _, fake, result = self.run_case(create_loss=True)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(any(argv[:2] == ("container", "inspect") for argv in fake.calls))

    def test_network_create_loss_adopts_one_exact_owned_candidate(self):
        _, _, fake, result = self.run_case("docker-networked-v1", network_loss=True)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(any(argv[:2] == ("network", "ls") for argv in fake.calls))

    def test_ambiguous_or_foreign_network_candidate_refuses_without_network_rm(self):
        for options in ({"network_loss": True, "candidates": [N0, "d" * 64]},
                        {"network_loss": True, "foreign": True}):
            _, store, op, capture = self.fixture("docker-networked-v1")
            fake = FakeDocker(op, **options)
            with self.subTest(options=options), self.assertRaises(execution.GenericExecutionUnavailable):
                execute(store, op, capture, fake)
            self.assertFalse(any(argv[:2] == ("network", "rm") for argv in fake.destructive))

    def test_cleanup_and_postflight_failures_admit_no_result(self):
        for options, postflight in (({"cleanup_failure": "container"}, lambda d: D0),
                                    ({}, lambda d: D0)):
            _, store, op, capture = self.fixture()
            fake = FakeDocker(op, **options)
            with self.assertRaises(execution.GenericExecutionUnavailable):
                execute(store, op, capture, fake, postflight=postflight)


class EffectsSeamTests(ExecutionCase):
    """N-B1: the real `ConcreteGenericEffects` vocabulary sits between the lifecycle and Docker."""

    GUEST_ARGV = (
        ["cargo", "build"], ["npm", "run", "build"], ["pytest", "-k", "load"],
        ["ps", "--pid", "1"], ["unshare", "--uts", "true"], ["go", "test", "./..."],
        ["sh", "-c", "docker system prune -af; docker context use x; docker image pull y"],
        ["make", "login", "daemon", "context", "system", "pull", "load"],
    )

    def test_toolchain_argv_with_docker_words_runs_through_real_effects(self):
        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            for argv in self.GUEST_ARGV:
                root, store, op, capture = self.fixture(profile, argv)
                fake = FakeDocker(op)
                effects, runner = concrete(fake, root)
                with self.subTest(profile=profile, argv=argv):
                    result = execute(store, op, capture, effects)
                    self.assertIs(result.verdict, generic_spike.GenericVerdict.PASS)
                    create = next(item for item in runner.commands
                                  if item[1:3] == ("container", "create"))
                    self.assertEqual(create[-len(argv) + 1:] if len(argv) > 1 else (),
                                     tuple(argv[1:]))
                    self.assertNoResidue(store, op, fake)

    def test_compiler_admits_guest_namespace_words_after_the_image(self):
        for argv in (["ps", "--pid", "1"], ["unshare", "--uts", "true"], ["x", "--privileged"]):
            op = self.fixture(argv=argv)[2]
            create = op.command("container_create").argv
            self.assertEqual(create[create.index(op.plan.image.image_id) + 1:], tuple(argv[1:]))

    def test_control_plane_subcommands_stay_refused_before_any_launch(self):
        root, _, op, _ = self.fixture()
        effects, runner = concrete(FakeDocker(op), root)
        for argv in (("image", "pull", "x"), ("context", "use", "x"), ("system", "prune", "-af"),
                     ("container", "run", "x"), ("container", "exec", "x", "sh"),
                     ("buildx", "build", "."), ("image", "load"), ("login", "x"),
                     ("image", "build", "."), ("network", "connect", "a", "b"), ("container",)):
            with self.subTest(argv=argv):
                with self.assertRaises(generic_effects.GenericEffectsRefused) as caught:
                    effects.run(argv, timeout_seconds=1)
                self.assertIs(caught.exception.before_launch, True)
        self.assertEqual(runner.commands, [])

    def test_create_refused_before_launch_records_absence_and_finishes(self):
        class RefusingEffects(generic_effects.ConcreteGenericEffects):
            def _argv(self, argv):
                if argv[:2] == ("container", "create"):
                    raise generic_effects.GenericEffectsRefused("refused before launch")
                return super()._argv(argv)

        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            root, store, op, capture = self.fixture(profile)
            fake = FakeDocker(op)
            _, runner = concrete(fake, root)
            effects = RefusingEffects(
                Path(sys.executable).resolve(),
                {"LANG": "C", "LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock",
                 "DOCKER_CONFIG": str(root / "docker-config")}, root, runner=runner)
            with self.subTest(profile=profile):
                with self.assertRaises(execution.GenericExecutionUnavailable):
                    execute(store, op, capture, effects)
                self.assertNoResidue(store, op, fake)
                self.assertFalse(any(item[1:3] == ("container", "create")
                                     for item in runner.commands))


class StaticPolicyTests(ExecutionCase):
    """N-B2: ownership records the ID; a policy refusal is a clean unavailable plus exact removal."""

    POLICY_MUTATIONS = (
        ("Config", "Image", D0), ("Config", "Entrypoint", ["sh"]), ("Config", "Cmd", ["wrong"]),
        ("Config", "WorkingDir", "/tmp"), ("Config", "User", "0:0"),
        ("Config", "Env", ["HOME=/bad", "TMPDIR=/tmp", "MODE=test"]),
        ("Config", "Volumes", {"/data": {}}),
        ("HostConfig", "Privileged", True), ("HostConfig", "PidMode", "host"),
        ("HostConfig", "UTSMode", "host"), ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "PortBindings", {"80/tcp": [{"HostPort": "80"}]}),
        ("HostConfig", "PublishAllPorts", True),
        ("HostConfig", "Devices", [{"PathOnHost": "/dev/null"}]),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]), ("HostConfig", "CgroupnsMode", "host"),
        ("HostConfig", "ReadonlyRootfs", False), ("HostConfig", "SecurityOpt", []),
        ("HostConfig", "CapDrop", []), ("HostConfig", "Memory", 1),
        ("HostConfig", "MemorySwap", -1), ("HostConfig", "NanoCpus", 1),
        ("HostConfig", "PidsLimit", 1), ("HostConfig", "ShmSize", 1),
        ("HostConfig", "Ulimits", []), ("HostConfig", "Tmpfs", {}),
        ("HostConfig", "NetworkMode", "host"),
    )

    def test_every_policy_refusal_removes_the_recorded_container_and_finishes(self):
        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            for section, key, value in self.POLICY_MUTATIONS:
                _, store, op, capture = self.fixture(profile)
                options = {"config": {key: value}} if section == "Config" else {
                    "host_config": {key: value}}
                fake = FakeDocker(op, **options)
                with self.subTest(profile=profile, field=f"{section}.{key}"):
                    with self.assertRaises(execution.GenericExecutionUnavailable) as caught:
                        execute(store, op, capture, fake)
                    self.assertIn("static admission was refused", str(caught.exception))
                    self.assertIn(("container", "rm", "--force", "--volumes", C0), fake.destructive)
                    self.assertFalse(any(call[:2] == ("container", "start") for call in fake.calls))
                    self.assertNoResidue(store, op, fake)

    def test_published_ports_stay_refused(self):
        for host_config in ({"PortBindings": {"6379/tcp": [{"HostIp": "", "HostPort": "6379"}]}},
                            {"PublishAllPorts": True}):
            _, store, op, capture = self.fixture()
            fake = FakeDocker(op, config={"ExposedPorts": {"6379/tcp": {}}},
                              host_config=host_config)
            with self.subTest(host_config=host_config):
                with self.assertRaises(execution.GenericExecutionUnavailable):
                    execute(store, op, capture, fake)
                self.assertNoResidue(store, op, fake)

    def test_image_merged_metadata_is_admitted(self):
        for form in FIXTURE["image_merged_config_forms"]:
            argv = ["python"] if form["name"] == "cmd-null-no-arguments" else None
            _, store, op, capture = self.fixture(argv=argv)
            config = copy.deepcopy(form["Config"])
            if "Env" in config:
                config["Env"] += [f"{key}={value}" for key, value in op.container.environment]
            if "Labels" in config:
                config["Labels"].update(dict(op.container.labels))
            fake = FakeDocker(op, config=config)
            with self.subTest(form=form["name"]):
                result = execute(store, op, capture, fake)
                self.assertIs(result.verdict, generic_spike.GenericVerdict.PASS)
                self.assertNoResidue(store, op, fake)

    def test_ownership_alone_decides_adoption(self):
        op = self.fixture()[2]
        document = static_document(op)
        document["Id"] = C0
        document["Config"]["ExposedPorts"] = {"80/tcp": {}}
        document["HostConfig"]["Privileged"] = True
        admission.admit_owned_container(document, container_full_id=C0, name=op.container.name,
                                        labels=op.container.labels)
        for mutate in (lambda d: d.__setitem__("Id", FOREIGN),
                       lambda d: d.__setitem__("Name", "/other"),
                       lambda d: d["Config"]["Labels"].__setitem__(
                           "io.siffran.empirica.run", "other"),
                       lambda d: d["Config"].__setitem__("Labels", None)):
            changed = copy.deepcopy(document)
            mutate(changed)
            with self.assertRaises(admission.DockerObservationError):
                admission.admit_owned_container(
                    changed, container_full_id=C0, name=op.container.name,
                    labels=op.container.labels)


class RecoveryTests(ExecutionCase):
    """N-B3/NEW-3: recovery is total from persisted data, under the same endpoint only."""

    def crash_and_recover(self, profile, crash_after, options, *, operator_removed=False):
        _, store, op, capture = self.fixture(profile)
        fake = FakeDocker(op, crash_after=crash_after, **options)
        try:
            execute(store, op, capture, fake)
        except (Crash, execution.GenericExecutionUnavailable):
            pass
        fake.crash_after = None
        if operator_removed:
            fake.container_present = False
            fake.network_present = False
        recover(store, fake)
        self.assertNoResidue(store, op, fake)
        return fake

    def test_crash_at_every_effect_recovers_in_every_cidfile_and_create_form(self):
        forms = {"cidfile-written-natural": {}, "cidfile-missing-natural": {"no_cid": True},
                 "cidfile-empty-killed-created": {"kill_create": True},
                 "cidfile-empty-killed-absent": {"kill_create": False},
                 "cidfile-missing-natural-failed": {"create_failure": True},
                 "cidfile-written-response-lost": {"create_loss": True},
                 "network-response-lost": {"network_loss": True}}
        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            for name, options in forms.items():
                _, store, op, capture = self.fixture(profile)
                total = FakeDocker(op, **options)
                try:
                    execute(store, op, capture, total)
                except execution.GenericExecutionUnavailable:
                    pass
                for crash_after in range(total.effects_seen + 1):
                    for operator_removed in (False, True):
                        with self.subTest(profile=profile, form=name, crash_after=crash_after,
                                          operator_removed=operator_removed):
                            self.crash_and_recover(profile, crash_after, options,
                                                   operator_removed=operator_removed)

    def test_each_journal_state_with_recorded_ids_present_or_already_absent(self):
        transitions = ("staged", "network_intent", "network_created", "container_intent",
                       "container_created", "static_verified", "attaching",
                       "terminal_observed", "cleanup", "removed")
        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            states = [item for item in transitions
                      if profile == "docker-networked-v1" or not item.startswith("network")]
            for stop in range(len(states) + 1):
                for container_present in (True, False):
                    for network_present in (True, False):
                        for cidfile in ("written", "empty", "missing"):
                            self.recover_built_state(profile, states[:stop], container_present,
                                                     network_present, cidfile)

    def recover_built_state(self, profile, states, container_present, network_present, cidfile):
        _, store, op, capture = self.fixture(profile)
        journals = journal.GenericJournalStore(store)
        row = journals.begin(op, authority=AUTHORITY)
        fake = FakeDocker(op)
        for state in states:
            if state == "staged":
                manifest = generic_staging.materialize_generic_staging(
                    store, op.operation_id, op.plan, capture, op.staging_token)
                fake.staging_override = manifest.root
            if state == "removed":
                fake.container_present = fake.network_present = False
            row = journals.transition(op.operation_id, state, state)
            if state == "network_intent":
                fake.network_present = network_present
                row = journals.record_full_id(op.operation_id, "network", N0)
            if state == "container_intent":
                fake.container_present = container_present
                if cidfile != "missing":
                    row.cidfile.write_text(C0 if cidfile == "written" else "")
                    row.cidfile.chmod(0o600)
            if state == "container_created":
                row = journals.record_full_id(op.operation_id, "container", C0)
            if state in {"attaching", "terminal_observed"}:
                fake.attached = True
        with self.subTest(profile=profile, states=states, container=container_present,
                          network=network_present, cidfile=cidfile):
            recover(store, fake)
            self.assertNoResidue(store, op, fake)

    def test_recorded_owned_container_failing_policy_is_removed_not_refused(self):
        _, store, op, capture = self.fixture()
        journals = journal.GenericJournalStore(store)
        journals.begin(op, authority=AUTHORITY)
        journals.transition(op.operation_id, "container_intent", "container_intent")
        journals.record_full_id(op.operation_id, "container", C0)
        journals.transition(op.operation_id, "container_created", "container_created")
        fake = FakeDocker(op, config={"ExposedPorts": {"80/tcp": {}}},
                          host_config={"Privileged": True})
        fake.container_present = True
        recover(store, fake)
        self.assertEqual(fake.destructive, [("container", "rm", "--force", "--volumes", C0)])
        self.assertNoResidue(store, op, fake)

    def test_different_endpoint_journal_fails_closed_and_keeps_its_operation_id(self):
        _, store, op, _ = self.fixture()
        journals = journal.GenericJournalStore(store)
        journals.begin(op, authority=AUTHORITY)
        journals.transition(op.operation_id, "container_intent", "container_intent")
        journals.record_full_id(op.operation_id, "container", C0)
        fake = FakeDocker(op)
        fake.container_present = True
        other = generic_authority.authority_facts(
            {**runtime(), "endpoint": "unix:///run/user/1000/docker.sock"})
        with self.assertRaises(execution.GenericExecutionUnavailable) as caught:
            recover(store, fake, authority=other)
        self.assertIn(f"stuck operation {op.operation_id}", str(caught.exception))
        self.assertIn("different Docker endpoint or platform", str(caught.exception))
        self.assertEqual(fake.calls, [])
        self.assertRetained(store, op, fake)
        recover(store, fake)
        self.assertNoResidue(store, op, fake)

    def test_same_daemon_with_changed_capability_facts_recovers(self):
        _, store, op, _ = self.fixture()
        journals = journal.GenericJournalStore(store)
        journals.begin(op, authority=AUTHORITY)
        self.assertEqual(set(journals.load(op.operation_id).authority),
                         {"endpoint", "server_os", "server_architecture"})
        journals.transition(op.operation_id, "container_intent", "container_intent")
        journals.record_full_id(op.operation_id, "container", C0)
        fake = FakeDocker(op)
        fake.container_present = True
        upgraded = generic_authority.authority_facts(
            runtime(seccomp="name=seccomp,profile=builtin"))
        self.assertNotEqual(upgraded, AUTHORITY)
        recover(store, fake, authority={**upgraded, "memory_limit": False})
        self.assertNoResidue(store, op, fake)
        self.assertEqual(fake.destructive, [("container", "rm", "--force", "--volumes", C0)])

    def test_unfinished_journal_survives_a_planner_and_implementation_change(self):
        _, store, op, _ = self.fixture()
        journals = journal.GenericJournalStore(store)
        row = journals.begin(op, authority=AUTHORITY)
        journals.transition(op.operation_id, "container_intent", "container_intent")
        journals.record_full_id(op.operation_id, "container", C0)
        path = row.directory / "journal.json"
        document = json.loads(path.read_text())
        # What an older planner wrote: different commands, schema, formats and digests.
        document["operation"]["schema"] = "empirica.docker-operation-plan/older"
        document["operation"]["commands"] = [{"kind": "retired", "argv": ["container", "x"]}]
        document["operation"]["timing"] = {"workload_seconds": 1}
        document["operation_digest"] = "sha256:" + "f" * 64
        path.write_text(json.dumps(document))
        path.chmod(0o600)
        fake = FakeDocker(op)
        fake.container_present = True
        recover(store, fake)
        self.assertNoResidue(store, op, fake)

    def test_malformed_journal_failure_keeps_the_operation_id(self):
        _, store, op, _ = self.fixture()
        row = journal.GenericJournalStore(store).begin(op, authority=AUTHORITY)
        path = row.directory / "journal.json"
        path.write_text("{")
        path.chmod(0o600)
        with self.assertRaises(execution.GenericExecutionUnavailable) as caught:
            recover(store, FakeDocker(op))
        self.assertIn(f"stuck operation {op.operation_id}", str(caught.exception))

    def test_transient_ambiguous_answer_is_resolved_by_a_later_well_formed_one(self):
        _, store, op, _ = self.fixture()
        journals = journal.GenericJournalStore(store)
        journals.begin(op, authority=AUTHORITY)
        journals.transition(op.operation_id, "container_intent", "container_intent")
        fake = FakeDocker(op)
        fake.container_present = True
        fake.container_candidates = [C0, "d" * 64]
        with self.assertRaises(execution.GenericExecutionUnavailable) as caught:
            recover(store, fake)
        self.assertIn("ambiguous exact-name candidates", str(caught.exception))
        self.assertRetained(store, op, fake)
        fake.container_candidates = None
        recover(store, fake)
        self.assertNoResidue(store, op, fake)


if __name__ == "__main__":
    unittest.main()
