#!/usr/bin/env python3
"""Fake-only tests for the Docker lifecycle substrate; no Docker daemon is used."""
from __future__ import annotations

import copy
import hashlib
import multiprocessing
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters.sandbox import docker, execution, record
from application.ports import ExecutionSnapshot
from core.freshness import FileBinding, PathContractError, canonical_digest

import test_sandbox_adapter as support


def operation_id(label: str) -> str:
    return "o-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


def _lock_attempt(home: str, deadline: float, queue) -> None:
    store = record.QualificationStore(Path(home), environment_inspector=lambda: None)
    try:
        with execution.runtime_lock(store, deadline):
            queue.put("acquired")
    except Exception as exc:
        queue.put(type(exc).__name__)


class SubstrateCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.environment = support.healthy_environment(self.root)
        result = support.RecordTests().result(self.root, environment_identity=self.environment)
        self.authority = record.build_record(result)
        self.policy = result["policy"]
        self.store = record.QualificationStore(
            self.root / "sandbox", environment_inspector=lambda: self.environment)
        self.store.publish(self.authority)
        identity = docker.DockerRuntimeIdentity(**{
            **result["preflight"],
            "engine_security_options": tuple(result["preflight"]["engine_security_options"]),
            "reasons": tuple(result["preflight"]["reasons"]),
        })
        self.runner = support.LifecycleRunner()
        self.client = docker.DockerClient(identity, self.policy, self.runner, env={})

    def snapshot(self, rows=(("src/main.rs", b"fn main() {}\n"),)):
        bindings = tuple(FileBinding(path, "sha256:" + __import__("hashlib").sha256(data).hexdigest())
                         for path, data in rows)
        return ExecutionSnapshot(
            bindings, tuple(data for _, data in rows),
            canonical_digest([(item.path, item.sha256) for item in bindings]))

    def intent(self, operation_id=operation_id("default"), *, name=None):
        name = name or "siffran-qual-" + operation_id
        labels = docker.qualification_labels(
            "r-" + "1" * 40, "spike", docker.policy_digest(self.policy))
        return {
            "schema": record.JOURNAL_SCHEMA,
            "operation_id": operation_id,
            "lifecycle_state": "intent",
            "execution_binding_digest": self.authority["execution_binding_digest"],
            "qualification_record_digest": self.authority["qualification_record_digest"],
            "policy_digest": docker.policy_digest(self.policy),
            "run_id": labels[docker.RUN_LABEL],
            "role": "spike",
            "name": name,
            "labels": labels,
            "expected_image": {
                "repo_digest": docker.IMAGE_REPO_DIGEST,
                "config_id": self.authority["runtime"]["image_config_id"],
            },
            "full_id": None,
            "cidfile_name": "container.cid",
        }

    def begin_and_stage(self, operation_id=operation_id("default")):
        intent = self.intent(operation_id)
        self.store.begin_operation(intent)
        path = execution.materialize_snapshot(
            self.store, operation_id, self.snapshot(), self.policy)
        return intent, path


class ScaffoldingAndLockTests(SubstrateCase):
    def test_operator_prepare_creates_exact_scaffolding_and_runtime_does_not_repair(self):
        self.assertEqual(stat.S_IMODE(self.store.staging.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store.runtime_lock.stat().st_mode), 0o600)
        self.assertEqual(self.store.runtime_lock.read_bytes(), b"")
        self.store.runtime_lock.unlink()
        with self.assertRaises(record.QualificationRecordError):
            self.store.check_execution_scaffolding()
        self.assertFalse(self.store.runtime_lock.exists())

    def test_operation_id_grammar_rejects_dot_dot_and_malformed_without_mutation(self):
        before_runtime = sorted(path.name for path in self.store.runtime.iterdir())
        before_staging = sorted(path.name for path in self.store.staging.iterdir())
        for value in (".", "..", "o-short", "o-" + "a" * 33, "o-" + "g" * 32):
            with self.subTest(value=value):
                intent = self.intent(operation_id("valid"))
                intent["operation_id"] = value
                with self.assertRaises((record.QualificationRecordError,
                                        execution.ExecutionSubstrateError)):
                    self.store.begin_operation(intent)
                with self.assertRaises(execution.ExecutionSubstrateError):
                    execution.cleanup_staging(self.store, value, self.policy)
        self.assertEqual(sorted(path.name for path in self.store.runtime.iterdir()), before_runtime)
        self.assertEqual(sorted(path.name for path in self.store.staging.iterdir()), before_staging)
        generated = {execution.new_operation_id() for _ in range(32)}
        self.assertEqual(len(generated), 32)
        self.assertTrue(all(record.OPERATION_ID.fullmatch(value) for value in generated))

    def test_passive_authority_load_does_not_require_or_repair_execution_scaffolding(self):
        self.store.runtime_lock.unlink()
        self.store.staging.rmdir()
        loaded = self.store.load_current(expected_environment=self.environment)
        self.assertEqual(loaded["qualification_record_digest"],
                         self.authority["qualification_record_digest"])
        self.assertFalse(self.store.runtime_lock.exists())
        self.assertFalse(self.store.staging.exists())

    def test_runtime_lock_serializes_processes_and_times_out_at_supplied_deadline(self):
        queue = multiprocessing.Queue()
        with execution.runtime_lock(self.store, time.monotonic() + 1):
            child = multiprocessing.Process(
                target=_lock_attempt,
                args=(str(self.store.home), time.monotonic() + 0.15, queue))
            child.start()
            child.join(3)
        self.assertFalse(child.is_alive())
        self.assertEqual(queue.get(timeout=1), "ExecutionSubstrateError")
        with execution.runtime_lock(self.store, time.monotonic() + 1):
            pass
        for deadline in (float("nan"), float("inf"), True):
            with self.subTest(deadline=deadline), self.assertRaises(
                    execution.ExecutionSubstrateError):
                with execution.runtime_lock(self.store, deadline):
                    pass

    def test_lock_symlink_mode_and_hardlink_are_refused_without_repair(self):
        self.store.runtime_lock.chmod(0o644)
        with self.assertRaises((record.QualificationRecordError, execution.ExecutionSubstrateError)):
            with execution.runtime_lock(self.store, time.monotonic() + 0.1):
                pass
        self.store.runtime_lock.chmod(0o600)
        with patch.object(record.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(record.QualificationRecordError):
                self.store.check_execution_scaffolding()
        hardlink = self.root / "hard-lock"
        os.link(self.store.runtime_lock, hardlink)
        with self.assertRaises(record.QualificationRecordError):
            self.store.check_execution_scaffolding()
        hardlink.unlink()
        alias = self.root / "alias-lock"
        self.store.runtime_lock.rename(alias)
        self.store.runtime_lock.symlink_to(alias)
        with self.assertRaises((record.QualificationRecordError, execution.ExecutionSubstrateError)):
            with execution.runtime_lock(self.store, time.monotonic() + 0.1):
                pass


class StagingTests(SubstrateCase):
    def test_staging_contains_only_snapshot_bytes_with_sealed_modes_then_cleans_exactly(self):
        operation = operation_id("stage")
        self.store.begin_operation(self.intent(operation))
        snapshot = self.snapshot()
        plan = execution._plan_staging(snapshot, self.policy)
        self.assertEqual(plan.files[0][0], ("src", "main.rs"))
        self.assertEqual((plan.max_files, plan.max_bytes),
                         (self.policy["bounds"]["staging_max_files"],
                          self.policy["bounds"]["staging_max_bytes"]))
        root = execution.materialize_snapshot(self.store, operation, snapshot, self.policy)
        self.assertEqual((root / "src/main.rs").read_bytes(), b"fn main() {}\n")
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE((root / "src").stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE((root / "src/main.rs").stat().st_mode), 0o444)
        self.assertEqual(sorted(path.relative_to(root).as_posix() for path in root.rglob("*")),
                         ["src", "src/main.rs"])
        execution.cleanup_staging(self.store, operation, self.policy)
        self.store.finish_operation(operation, None)
        self.assertFalse(root.exists())

    def test_staging_rejects_noncanonical_paths_before_any_operation_tree(self):
        for path in ("../escape", "/absolute", "a//b", "a\\b"):
            with self.subTest(path=path):
                with self.assertRaises(PathContractError):
                    FileBinding(path, "sha256:" + "0" * 64)
        self.assertEqual(list(self.store.staging.iterdir()), [])

    def test_deep_snapshot_and_zero_progress_write_leave_no_staging_root(self):
        operation = operation_id("deep")
        deep_path = "/".join([f"d{index}" for index in range(65)] + ["file"])
        snapshot = self.snapshot(((deep_path, b"x"),))
        with self.assertRaises(execution.ExecutionSubstrateError):
            execution.materialize_snapshot(self.store, operation, snapshot, self.policy)
        self.assertFalse((self.store.staging / operation).exists())

        operation = operation_id("many-prefixes")
        files = tuple(("/".join(f"d{file_index}-{depth}" for depth in range(64)) + "/file", b"x")
                      for file_index in range(20))
        with self.assertRaises(execution.ExecutionSubstrateError):
            execution.materialize_snapshot(self.store, operation, self.snapshot(files), self.policy)
        self.assertFalse((self.store.staging / operation).exists())

        operation = operation_id("zero-write")
        with patch.object(execution.os, "write", return_value=0):
            with self.assertRaises(execution.ExecutionSubstrateError):
                execution.materialize_snapshot(
                    self.store, operation, self.snapshot((("a", b"x"),)), self.policy)
        self.assertFalse((self.store.staging / operation).exists())

    def test_staging_count_byte_symlink_mode_and_hardlink_bounds_fail_closed(self):
        operation = operation_id("bounds")
        self.store.begin_operation(self.intent(operation))
        narrow = copy.deepcopy(self.policy)
        narrow["bounds"]["staging_max_bytes"] = 1
        self.assertNotEqual(record.current_profile(self.policy)["profile_digest"],
                            record.current_profile(narrow)["profile_digest"])
        with self.assertRaises(execution.ExecutionSubstrateError):
            execution.materialize_snapshot(self.store, operation, self.snapshot(), narrow)
        # The failed materializer cleans its exact operation tree, never the journal.
        self.assertFalse((self.store.staging / operation).exists())

        operation = operation_id("count")
        self.store.begin_operation(self.intent(operation))
        narrow = copy.deepcopy(self.policy)
        narrow["bounds"]["staging_max_files"] = 1
        with self.assertRaises(execution.ExecutionSubstrateError):
            execution.materialize_snapshot(
                self.store, operation,
                self.snapshot((("a", b"a"), ("b", b"b"))), narrow)
        self.assertFalse((self.store.staging / operation).exists())

        operation = operation_id("hardlink")
        self.store.begin_operation(self.intent(operation))
        root = execution.materialize_snapshot(self.store, operation, self.snapshot(), self.policy)
        root.chmod(0o700)
        os.link(root / "src/main.rs", root / "copy")
        with self.assertRaises(execution.ExecutionSubstrateError):
            execution.cleanup_staging(self.store, operation, self.policy)
        self.assertTrue(root.exists())

        operation = operation_id("symlink")
        self.store.begin_operation(self.intent(operation))
        root = self.store.staging / operation
        root.mkdir(mode=0o700)
        (root / "bad").symlink_to(self.root / "outside")
        with self.assertRaises(execution.ExecutionSubstrateError):
            execution.cleanup_staging(self.store, operation, self.policy)
        self.assertTrue((root / "bad").is_symlink())

        operation = operation_id("mode")
        self.store.begin_operation(self.intent(operation))
        root = self.store.staging / operation
        root.mkdir(mode=0o700)
        root.chmod(0o755)
        with self.assertRaises(execution.ExecutionSubstrateError):
            execution.cleanup_staging(self.store, operation, self.policy)
        self.assertTrue(root.exists())


class JournalTransitionTests(SubstrateCase):
    def test_pure_transition_table_matches_persisted_effects(self):
        operation = operation_id("planner")
        intent = self.intent(operation)
        self.assertEqual(record.plan_journal_transition(intent, "begin"), intent)
        self.store.begin_operation(intent)
        row = self.store.read_journals()[0]
        self.assertEqual(record.plan_journal_transition(
            row, "write_cid", self.runner.ID), intent)
        self.store.write_cid(operation, self.runner.ID)
        row = self.store.read_journals()[0]
        planned = record.plan_journal_transition(row, "mark_created", self.runner.ID)
        self.store.mark_created(operation, self.runner.ID)
        persisted = self.store.read_journals()[0]
        self.assertEqual({key: persisted[key] for key in record.JOURNAL_KEYS}, planned)
        planned = record.plan_journal_transition(persisted, "mark_removing")
        self.store.mark_removing(operation)
        persisted = self.store.read_journals()[0]
        self.assertEqual({key: persisted[key] for key in record.JOURNAL_KEYS}, planned)
        tombstone = record.plan_journal_transition(persisted, "finish", self.runner.ID)
        self.assertEqual((tombstone["lifecycle_state"], tombstone["full_id"]),
                         ("intent", None))
        for event in ("begin", "write_cid", "mark_created"):
            with self.subTest(event=event), self.assertRaises(record.QualificationRecordError):
                record.plan_journal_transition(persisted, event, self.runner.ID)

    def test_exact_atomic_transition_sequence_and_journal_deleted_last(self):
        intent, staging = self.begin_and_stage()
        self.store.write_cid(intent["operation_id"], self.runner.ID)
        self.store.mark_created(intent["operation_id"], self.runner.ID)
        self.assertEqual(self.store.read_journals()[0]["lifecycle_state"], "created")
        self.store.mark_removing(intent["operation_id"])
        self.store.mark_removing(intent["operation_id"])
        with self.assertRaises(record.QualificationRecordError):
            self.store.finish_operation(intent["operation_id"], self.runner.ID)
        self.assertTrue((self.store.runtime / intent["operation_id"]).exists())
        execution.cleanup_staging(self.store, intent["operation_id"], self.policy)
        self.store.finish_operation(intent["operation_id"], self.runner.ID)
        self.assertFalse(staging.exists())
        self.assertEqual(self.store.read_journals(), [])

    def test_transitions_reject_wrong_id_existing_cid_and_out_of_order_state(self):
        operation = operation_id("order")
        intent = self.intent(operation)
        self.store.begin_operation(intent)
        with self.assertRaises(record.QualificationRecordError):
            self.store.mark_created(operation, self.runner.ID)
        with self.assertRaises(record.QualificationRecordError):
            self.store.mark_removing(operation)
        self.store.write_cid(operation, self.runner.ID)
        with self.assertRaises(record.QualificationRecordError):
            self.store.write_cid(operation, self.runner.ID)
        with self.assertRaises(record.QualificationRecordError):
            self.store.mark_created(operation, "b" * 64)


class RecoveryTests(SubstrateCase):
    def test_pure_recovery_plan_table_matches_effect_paths(self):
        row = self.intent(operation_id("recovery-planner"))
        table = (
            (False, True, True, "cleanup"),
            (False, True, False, "cleanup"),
            (False, False, False, "finish"),
            (True, True, True, "remove"),
            (True, False, False, "refuse"),
            (True, True, False, "refuse"),
        )
        for present, staged, safe, expected in table:
            with self.subTest(present=present, staged=staged, safe=safe):
                self.assertEqual(execution._plan_recovery(
                    row, container_present=present,
                    staging_present=staged, staging_safe=safe), expected)

    def _create(self, intent, staging):
        container_id, _doc = self.client.create_container_with_identity(
            name=intent["name"], labels=intent["labels"], input_dir=staging,
            command="true", network="none")
        return container_id

    def test_deadline_is_rechecked_after_authority_load_before_any_cli_call(self):
        with execution.runtime_lock(self.store, time.monotonic() + 1):
            for deadline in (float("nan"), float("inf"), True):
                with self.subTest(deadline=deadline), self.assertRaises(
                        execution.ExecutionSubstrateError):
                    execution.recover_operations(self.store, self.client, deadline)
            expired = self.store.runtime / operation_id("expired-empty")
            expired.mkdir(mode=0o700)
            with self.assertRaises(execution.ExecutionSubstrateError):
                execution.recover_operations(
                    self.store, self.client, time.monotonic() - 1)
            self.assertTrue(expired.exists())
            expired.rmdir()
        operation = operation_id("authority-deadline")
        self.store.begin_operation(self.intent(operation))
        original = self.store.load_record
        before = len(self.runner.calls)

        def slow_load(digest):
            time.sleep(0.03)
            return original(digest)

        with patch.object(self.store, "load_record", side_effect=slow_load):
            with execution.runtime_lock(self.store, time.monotonic() + 1):
                with self.assertRaises(execution.ExecutionSubstrateError):
                    execution.recover_operations(
                        self.store, self.client, time.monotonic() + 0.01)
        self.assertEqual(len(self.runner.calls), before)
        self.assertTrue((self.store.runtime / operation).exists())

    def test_recovery_covers_no_container_create_before_cid_and_every_durable_state(self):
        # intent + partial staging, no container
        operation = operation_id("none")
        intent, _staging = self.begin_and_stage(operation)
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            self.assertEqual(execution.recover_operations(
                self.store, self.client, time.monotonic() + 2), (operation,))
        self.assertEqual(self.store.read_journals(), [])

        # create-before-cidfile is found by all labels and exact inspect
        operation = operation_id("pre-cid")
        intent, staging = self.begin_and_stage(operation)
        self._create(intent, staging)
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            self.assertEqual(execution.recover_operations(
                self.store, self.client, time.monotonic() + 2), (operation,))
        self.assertTrue(self.runner.removed)
        self.assertEqual(self.store.read_journals(), [])

    def test_intent_with_cid_created_and_removing_absence_windows_finish(self):
        for state in ("intent-cid", "created", "removing"):
            with self.subTest(state=state):
                operation = operation_id(state)
                intent, _ = self.begin_and_stage(operation)
                self.store.write_cid(operation, self.runner.ID)
                if state != "intent-cid":
                    self.store.mark_created(operation, self.runner.ID)
                if state == "removing":
                    self.store.mark_removing(operation)
                with execution.runtime_lock(self.store, time.monotonic() + 2):
                    execution.recover_operations(self.store, self.client, time.monotonic() + 2)
                self.assertFalse((self.store.runtime / operation).exists())

    def test_live_container_recovery_covers_intent_created_and_removing(self):
        for state in ("intent", "created", "removing"):
            with self.subTest(state=state):
                operation = operation_id("live-" + state)
                intent, staging = self.begin_and_stage(operation)
                runner = support.LifecycleRunner()
                client = docker.DockerClient(self.client.identity, self.policy, runner, env={})
                container_id, _ = client.create_container_with_identity(
                    name=intent["name"], labels=intent["labels"], input_dir=staging,
                    command="true", network="none")
                if state != "intent":
                    self.store.write_cid(operation, container_id)
                    self.store.mark_created(operation, container_id)
                if state == "removing":
                    self.store.mark_removing(operation)
                with execution.runtime_lock(self.store, time.monotonic() + 2):
                    execution.recover_operations(self.store, client, time.monotonic() + 2)
                self.assertTrue(runner.removed)
                self.assertFalse((self.store.runtime / operation).exists())
                self.assertFalse(staging.exists())

    def test_partial_name_and_generic_inspect_failures_never_prove_identity_or_absence(self):
        operation = operation_id("name")
        intent, staging = self.begin_and_stage(operation)
        self._create(intent, staging)
        self.runner.create_doc["Name"] = "/" + intent["name"] + "-suffix"
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            with self.assertRaises(docker.DockerInfrastructureError):
                execution.recover_operations(self.store, self.client, time.monotonic() + 2)
        self.assertFalse(self.runner.removed)
        self.assertTrue((self.store.runtime / operation).exists())
        self.runner.create_doc["Name"] = "/" + intent["name"]
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            execution.recover_operations(self.store, self.client, time.monotonic() + 2)

        class InspectFailureRunner(support.LifecycleRunner):
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                args = tuple(argv[1:])
                if args[:2] == ("container", "inspect") and inner.created:
                    return docker.CommandResult.fake(1, stderr="daemon transport failed")
                return super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)

        runner = InspectFailureRunner()
        client = docker.DockerClient(self.client.identity, self.policy, runner, env={})
        operation = operation_id("inspect")
        intent, staging = self.begin_and_stage(operation)
        runner.created = True
        runner.create_doc = support.LifecycleRunner()._build_doc(docker.docker_create_argv(
            self.policy, input_dir=staging, name=intent["name"], labels=intent["labels"],
            command="true"))
        self.store.write_cid(operation, runner.ID)
        self.store.mark_created(operation, runner.ID)
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            with self.assertRaises((docker.DockerInfrastructureError,
                                    docker.DockerCleanupError)):
                execution.recover_operations(self.store, client, time.monotonic() + 2)
        self.assertFalse(runner.removed)
        self.assertTrue((self.store.runtime / operation).exists())

    def test_label_filtered_hint_hides_drift_but_known_full_id_retains_journal(self):
        operation = operation_id("label-drift")
        intent, staging = self.begin_and_stage(operation)
        self._create(intent, staging)
        self.store.write_cid(operation, self.runner.ID)
        self.store.mark_created(operation, self.runner.ID)
        self.runner.create_doc["Config"]["Labels"][docker.OWNER_LABEL] = "wrong"
        self.assertEqual(self.client.exact_candidates(
            name=intent["name"], labels=intent["labels"], input_dir=staging,
            network="none", timeout_seconds=1), ())
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            with self.assertRaises(docker.DockerInfrastructureError):
                execution.recover_operations(
                    self.store, self.client, time.monotonic() + 2)
        self.assertFalse(self.runner.removed)
        self.assertTrue((self.store.runtime / operation).exists())
        self.assertTrue(staging.exists())

    def test_create_builder_rejects_every_unsafe_preselected_label_value(self):
        labels = docker.qualification_labels(
            "run", "spike", docker.policy_digest(self.policy))
        for key in labels:
            with self.subTest(key=key):
                changed = dict(labels)
                changed[key] = "unsafe/value"
                with self.assertRaises(docker.DockerQualificationError):
                    docker.docker_create_argv(
                        self.policy, input_dir=self.store.staging,
                        name=docker.NAME_PREFIX + "safe", labels=changed, command="true")

    def test_every_inspected_identity_family_mismatch_retains_live_journal(self):
        operation = operation_id("mismatch")
        intent, staging = self.begin_and_stage(operation)
        self._create(intent, staging)
        self.store.write_cid(operation, self.runner.ID)
        self.store.mark_created(operation, self.runner.ID)
        baseline = copy.deepcopy(self.runner.create_doc)

        def label(doc):
            doc["Config"]["Labels"][docker.OWNER_LABEL] = "wrong"

        def image(doc):
            doc["Image"] = "sha256:" + "f" * 64

        def process(doc):
            doc["Config"]["User"] = "0:0"

        def network(doc):
            doc["HostConfig"]["NetworkMode"] = "bridge"

        def resources(doc):
            doc["HostConfig"]["Memory"] += 1

        def mount(doc):
            doc["Mounts"][0]["RW"] = True

        def tmpfs(doc):
            doc["HostConfig"]["Tmpfs"].pop("/tmp")

        for mutate in (label, image, process, network, resources, mount, tmpfs):
            with self.subTest(mutate=mutate.__name__):
                self.runner.create_doc = copy.deepcopy(baseline)
                mutate(self.runner.create_doc)
                with execution.runtime_lock(self.store, time.monotonic() + 2):
                    with self.assertRaises((docker.DockerInfrastructureError,
                                            docker.DockerCleanupError)):
                        execution.recover_operations(
                            self.store, self.client, time.monotonic() + 2)
                self.assertFalse(self.runner.removed)
                self.assertTrue((self.store.runtime / operation).exists())
        self.runner.create_doc = baseline

    def _removing_absent(self, label):
        operation = operation_id(label)
        intent, staging = self.begin_and_stage(operation)
        self._create(intent, staging)
        self.store.write_cid(operation, self.runner.ID)
        self.store.mark_created(operation, self.runner.ID)
        self.store.mark_removing(operation)
        self.runner.removed = True
        execution.cleanup_staging(self.store, operation, self.policy)
        return operation

    def test_recovery_closes_mkdir_atomic_temp_and_finish_crash_windows(self):
        mkdir_operation = operation_id("mkdir-fault")
        original_mkdir = Path.mkdir

        def fail_operation_mkdir(path, *args, **kwargs):
            if path.name == mkdir_operation:
                raise OSError("injected mkdir interruption")
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", fail_operation_mkdir):
            with self.assertRaises(record.QualificationRecordError):
                self.store.begin_operation(self.intent(mkdir_operation))
        self.assertFalse((self.store.runtime / mkdir_operation).exists())

        atomic_operation = operation_id("atomic-fault")
        original_atomic = record.fsio.atomic_write

        def fail_before_rename(path, data):
            if path.name == "intent.json" and path.parent.name == atomic_operation:
                residue = path.parent / ".tmp.uvwxyz.json"
                residue.write_bytes(data[:8])
                residue.chmod(0o600)
                raise OSError("injected write/rename interruption")
            return original_atomic(path, data)

        with patch.object(record.fsio, "atomic_write", side_effect=fail_before_rename):
            with self.assertRaises(OSError):
                self.store.begin_operation(self.intent(atomic_operation))
        self.assertTrue((self.store.runtime / atomic_operation / ".tmp.uvwxyz.json").exists())
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            execution.recover_operations(self.store, self.client, time.monotonic() + 2)
        self.assertFalse((self.store.runtime / atomic_operation).exists())

        transition = operation_id("transition-temp")
        self.store.begin_operation(self.intent(transition))
        transition_residue = self.store.runtime / transition / ".tmp.ghijkl.json"
        transition_residue.write_bytes(b"partial replacement")
        transition_residue.chmod(0o600)
        empty = self.store.runtime / operation_id("empty")
        empty.mkdir(mode=0o700)
        temporary = self.store.runtime / operation_id("temporary")
        temporary.mkdir(mode=0o700)
        residue = temporary / ".tmp.abcdef.json"
        residue.write_bytes(b"partial")
        residue.chmod(0o600)
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            execution.recover_operations(self.store, self.client, time.monotonic() + 2)
        self.assertFalse(empty.exists())
        self.assertFalse(temporary.exists())
        self.assertFalse((self.store.runtime / transition).exists())

        unsafe = self.store.runtime / operation_id("unsafe-temp")
        unsafe.mkdir(mode=0o700)
        unsafe_residue = unsafe / ".tmp.mnopqr.json"
        unsafe_residue.write_bytes(b"unsafe")
        unsafe_residue.chmod(0o644)
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            with self.assertRaises(execution.ExecutionSubstrateError):
                execution.recover_operations(
                    self.store, self.client, time.monotonic() + 2)
        self.assertTrue(unsafe_residue.exists())
        unsafe_residue.chmod(0o600)
        unsafe_residue.unlink()
        unsafe.rmdir()

        original_unlink = Path.unlink
        operation = self._removing_absent("cid-unlink")
        def fail_cid(path, *args, **kwargs):
            if path.name == "container.cid":
                raise OSError("injected cid unlink crash")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_cid):
            with self.assertRaises(OSError):
                self.store.finish_operation(operation, self.runner.ID)
        row = self.store.read_journals()[0]
        self.assertEqual((row["lifecycle_state"], row["cidfile_full_id"]),
                         ("intent", self.runner.ID))
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            execution.recover_operations(self.store, self.client, time.monotonic() + 2)
        self.assertFalse((self.store.runtime / operation).exists())

        self.runner = support.LifecycleRunner()
        self.client = docker.DockerClient(self.client.identity, self.policy, self.runner, env={})
        operation = self._removing_absent("intent-unlink")
        def fail_intent(path, *args, **kwargs):
            if path.name == "intent.json":
                raise OSError("injected intent unlink crash")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_intent):
            with self.assertRaises(OSError):
                self.store.finish_operation(operation, self.runner.ID)
        row = self.store.read_journals()[0]
        self.assertEqual((row["lifecycle_state"], row["cidfile_full_id"]), ("intent", None))
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            execution.recover_operations(self.store, self.client, time.monotonic() + 2)
        self.assertFalse((self.store.runtime / operation).exists())

        self.runner = support.LifecycleRunner()
        self.client = docker.DockerClient(self.client.identity, self.policy, self.runner, env={})
        operation = self._removing_absent("rmdir")
        original_rmdir = Path.rmdir
        def fail_rmdir(path, *args, **kwargs):
            if path.name == operation:
                raise OSError("injected rmdir crash")
            return original_rmdir(path, *args, **kwargs)
        with patch.object(Path, "rmdir", fail_rmdir):
            with self.assertRaises(OSError):
                self.store.finish_operation(operation, self.runner.ID)
        self.assertEqual(list((self.store.runtime / operation).iterdir()), [])
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            execution.recover_operations(self.store, self.client, time.monotonic() + 2)
        self.assertFalse((self.store.runtime / operation).exists())

    def test_recovery_uses_exact_closed_argv_environment_and_order(self):
        operation = operation_id("argv")
        intent, staging = self.begin_and_stage(operation)
        self._create(intent, staging)
        before = len(self.runner.calls)
        timeout_before = len(self.runner.timeouts)
        with execution.runtime_lock(self.store, time.monotonic() + 2):
            execution.recover_operations(self.store, self.client, time.monotonic() + 2)
        calls = self.runner.calls[before:]
        self.assertEqual([call[1:3] for call in calls], [
            ("container", "ls"), ("container", "inspect"),
            ("container", "inspect"), ("container", "rm"),
            ("container", "inspect"), ("container", "ls"), ("container", "ls")])
        list_call = calls[0]
        self.assertNotIn("name=", " ".join(list_call))
        for key, value in intent["labels"].items():
            self.assertIn(f"label={key}={value}", list_call)
        self.assertTrue(all(env == {
            "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": "unix:///tmp/docker.sock"}
                            for env in self.runner.environments))
        recovery_timeouts = self.runner.timeouts[timeout_before:]
        self.assertEqual(len(recovery_timeouts), len(calls))
        self.assertTrue(all(0 < value <= 2 for value in recovery_timeouts))
        self.assertTrue(all(value == docker.CONTROL_OUTPUT_LIMIT_BYTES
                            for value in self.runner.output_limits[timeout_before:]))


class ClientBaseExceptionTests(SubstrateCase):
    def test_client_attempts_exact_cleanup_and_preserves_original_base_exception(self):
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                class InterruptRunner(support.LifecycleRunner):
                    def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                        if tuple(argv[1:4]) == ("container", "start", "--attach"):
                            inner.calls.append(tuple(argv))
                            inner.environments.append(dict(env or {}))
                            inner.timeouts.append(timeout_seconds)
                            inner.output_limits.append(output_limit_bytes)
                            raise KeyboardInterrupt()
                        return super().run(
                            argv, timeout_seconds=timeout_seconds,
                            output_limit_bytes=output_limit_bytes, env=env)

                runner = InterruptRunner(remove_exit=1 if cleanup_fails else 0)
                client = docker.DockerClient(self.client.identity, self.policy, runner, env={})
                operation = operation_id("cancel-" + str(int(cleanup_fails)))
                intent, staging = self.begin_and_stage(operation)
                with self.assertRaises(KeyboardInterrupt):
                    client.execute(
                        run_id=intent["run_id"], role="spike", input_dir=staging,
                        command="true")
                self.assertTrue(any(call[1:4] == ("container", "rm", "--force")
                                    for call in runner.calls))


class ReachabilityTests(unittest.TestCase):
    def test_substrate_is_not_imported_by_application_or_bridge_paths(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "application/observation.py", "application/transaction.py", "application/v2.py",
            "adapters/bridge.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            self.assertNotIn("sandbox.execution", source)
            self.assertNotIn("recover_operations", source)
            self.assertNotIn("materialize_snapshot", source)


class BaseExceptionCleanupTests(unittest.TestCase):
    def test_runner_rejects_nonfinite_boolean_and_nonpositive_bounds(self):
        runner = docker.BoundedRunner()
        for timeout in (float("nan"), float("inf"), -1, 0, True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                runner.run(("/bin/true",), timeout_seconds=timeout,
                           output_limit_bytes=1, env={})
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                runner.run(("/bin/true",), timeout_seconds=1,
                           output_limit_bytes=limit, env={})

    def test_runner_kills_descendant_holding_pipes_after_leader_exits(self):
        script = (
            "import os,time\n"
            "pid=os.fork()\n"
            "if pid: os._exit(0)\n"
            "print(os.getpid(), flush=True)\n"
            "time.sleep(60)\n"
        )
        result = docker.BoundedRunner().run(
            (os.sys.executable, "-c", script), timeout_seconds=0.2,
            output_limit_bytes=4096, env={})
        self.assertTrue(result.timed_out)
        child = int(result.stdout.data.decode("ascii").strip())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail("descendant survived bounded-runner cleanup")

    def test_bounded_runner_kills_and_reaps_owned_group_before_reraising(self):
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "leader.pid"
            script = (
                "import os,pathlib,time; "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); time.sleep(60)")

            def interrupt(*_args, **_kwargs):
                deadline = time.monotonic() + 2
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                raise KeyboardInterrupt()

            with patch.object(docker.selectors.DefaultSelector, "select", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    docker.BoundedRunner().run(
                        (__import__("sys").executable, "-c", script),
                        timeout_seconds=5, output_limit_bytes=4096)
            pid = int(pid_path.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
