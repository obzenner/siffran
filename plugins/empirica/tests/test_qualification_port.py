#!/usr/bin/env python3
"""Empirica 7.2 E9: the ``qualify`` transaction through the bridge's real qualification port and
the real qualification owner, with only the Docker runtime stubbed (fake daemon, fake image
inspections, fake effects — the owner suite's own harness).

The seam under test is the one a fake port cannot witness: the identity the transaction hands the
owner on a normally started run (the bounded storage run identifier beside the separate project
and generation fields, in generation one and in a later generation), and the owner's recovery of a
dead attempt under the shared runtime lock — taken without waiting, before anything is reserved and held to the outcome — before its row is closed.
Behaviour only; every contract value is read from its owner.
"""
import json
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters import bridge
from adapters.sandbox import common
from adapters.sandbox import run_qualification as run_owner
from application import generic_spike, protocol
from core import run as core_run
from application.transaction import Coordinator as ProductionCoordinator
from test_d7_transactions import Harness, QualifiedSelector, Workspace
from test_environment_actions import ENVIRONMENT_ACTION, QUALIFY_ACTION
from test_qualification_transaction import PROFILE, ProcessDied, QualificationEnv
from test_run_qualification import D0, OwnerHarness, spike_document

IN_PROGRESS = generic_spike.GenericUnavailableReason.QUALIFICATION_IN_PROGRESS.value
INTERRUPTED = generic_spike.GenericUnavailableReason.QUALIFICATION_INTERRUPTED.value
RESERVED, SUCCEEDED, FAILED, SUPERSEDED = core_run.ATTEMPT_OUTCOMES
PROPOSED_IMAGE = ENVIRONMENT_ACTION["payload"]["spike_images"][0]
#: The hard bound on every wait of a test that spawns a process or a thread.
PROCESS_BOUND_SECONDS = 20
#: A second process that takes the runtime lease, says so, and holds it until told to release.
HOLD_THE_RUNTIME_LOCK = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from adapters.sandbox import run_qualification
with run_qualification.hold_runtime_lock(sandbox_root=Path(sys.argv[2])):
    print("held", flush=True)
    sys.stdin.readline()
"""


class RealPortTests(unittest.TestCase):
    def setUp(self):
        trap = mock.patch.object(common, "sandbox_home", side_effect=AssertionError(
            "a test resolved the real sandbox home"))
        trap.start()
        self.addCleanup(trap.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = (Path(temporary.name) / "sandbox").resolve()
        self.root.mkdir(mode=0o700, parents=True)
        self.root.chmod(0o700)
        # the loaded release latches per process; this suite resets it at its only seam
        common._loaded_release = None
        common._loaded_release_failed = None
        self.harness = OwnerHarness(self.root)
        self.harness.spike_images = [PROPOSED_IMAGE]
        self.harness.images.spike_documents[PROPOSED_IMAGE] = spike_document()
        self.harness.manifests = [{"implementation_digest": D0}] * 256
        self.port = bridge._RunQualificationPort(**self.harness.options())
        self.env = QualificationEnv(self.port)
        self.store = run_owner.RunQualificationStore(self.root)

    def _loaded_record(self):
        installed = self.env.binding()
        self.assertEqual(installed["kind"], "qualified", installed)
        key = self.env.key
        return self.store.load_record(
            installed["qualification_record_digest"], project_id=key.project_id,
            run_id=key.run_id, generation=key.generation)

    def test_a_normally_started_run_qualifies_through_the_owner_in_generation_one(self):
        result = self.env.qualify()
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Allow", result)
        key = self.env.key
        self.assertEqual(key.generation, 1)
        record = self._loaded_record()
        self.assertEqual(
            {name: record["run_binding"][name] for name in ("project_id", "run_id", "generation")},
            {"project_id": key.project_id, "run_id": key.run_id, "generation": 1})
        self.assertEqual(record["run_binding"]["attempt_id"], self.env.rows()[0]["attempt_id"])
        self.assertEqual([row["reference"] for row in record["spike_images"]], [PROPOSED_IMAGE])

    def test_a_later_generation_qualifies_through_the_owner_under_its_own_binding(self):
        self.env.restart()
        key = self.env.key
        self.assertEqual(key.generation, 2)
        result = self.env.qualify()
        self.assertEqual(result["type"], "Allow", result)
        record = self._loaded_record()
        self.assertEqual(record["run_binding"]["generation"], 2)
        self.assertEqual(record["run_binding"]["run_id"], key.run_id)

    def _die_after(self, publishing: bool):
        """One attempt of the real owner that dies mid-attempt (before or right after its record
        is durably published): the host process is gone, nothing was recorded."""
        original = run_owner.RunQualificationStore.write_record

        def publish_then_die(store, record):
            original(store, record)
            raise ProcessDied()

        patches = [mock.patch.object(run_owner.RunQualificationStore, "write_record",
                                     publish_then_die)] if publishing else [
            mock.patch.object(run_owner, "build_run_record", side_effect=ProcessDied())]
        with patches[0]:
            with self.assertRaises(ProcessDied):
                self.env.qualify()
        self.assertEqual([row["outcome"] for row in self.env.rows()], [RESERVED])

    def test_a_dead_attempt_before_publication_is_recovered_closed_and_asked_for_afresh(self):
        self._die_after(publishing=False)
        dead = self.env.rows()[0]["attempt_id"]
        self.assertFalse(self.store.records.exists() and any(self.store.records.iterdir()))
        result = self.env.qualify()
        self.assertEqual(result["type"], "Allow", result)
        rows = self.env.rows()
        self.assertEqual([(row["outcome"], row["reason"]) for row in rows],
                         [(FAILED, INTERRUPTED), (SUCCEEDED, None)])
        self.assertEqual(rows[0]["attempt_id"], dead)
        self.assertNotEqual(rows[1]["attempt_id"], dead)
        self.assertEqual(self._loaded_record()["run_binding"]["attempt_id"], rows[1]["attempt_id"])

    def test_a_dead_attempt_after_publication_leaves_its_record_an_orphan(self):
        self._die_after(publishing=True)
        orphans = sorted(self.store.records.iterdir())
        self.assertEqual(len(orphans), 1, "the dead attempt's record was durably published")
        result = self.env.qualify()
        self.assertEqual(result["type"], "Allow", result)
        rows = self.env.rows()
        self.assertEqual([(row["outcome"], row["reason"]) for row in rows],
                         [(FAILED, INTERRUPTED), (SUCCEEDED, None)])
        self.assertIsNone(rows[0]["record_digest"])
        installed = self.env.binding()["qualification_record_digest"]
        self.assertEqual(len(sorted(self.store.records.iterdir())), 2)
        self.assertNotIn(installed.removeprefix("sha256:") + ".json",
                         [orphan.name for orphan in orphans],
                         "the orphan names no row and binds nothing")

    def test_a_held_runtime_lock_refuses_in_progress_until_it_is_released(self):
        self._die_after(publishing=False)
        store = common.ExecutionStore(self.root)
        with common.runtime_lock(store, time.monotonic() + 30.0):
            result = self.env.qualify()
            self.assertEqual(result["type"], "Fault", result)
            self.assertEqual(result["reason"], IN_PROGRESS, result)
            self.assertEqual([row["outcome"] for row in self.env.rows()], [RESERVED])
        self.assertEqual(self.env.qualify()["type"], "Allow")
        self.assertEqual([row["outcome"] for row in self.env.rows()], [FAILED, SUCCEEDED])

    def test_another_process_holding_the_runtime_lock_refuses_before_anything_is_reserved(self):
        """The window the lock-first order closes, across two real processes: a process that
        holds the shared lock (an attempt between its reservation and its outcome) is never
        observed by a qualify that reserves first — the refusal reserves, charges and attempts
        nothing, and the same request proceeds once the other process has released."""
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD_THE_RUNTIME_LOCK, str(Path(__file__).resolve().parents[1]),
             str(self.root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([holder.stdout], [], [], PROCESS_BOUND_SECONDS)
            self.assertTrue(ready, "the holder process took the lock within its bound")
            self.assertEqual(holder.stdout.readline().strip(), "held")
            result = self.env.qualify()
            self.assertEqual((result["type"], result["reason"]), ("Fault", IN_PROGRESS), result)
            self.assertEqual((self.env.rows(), self.harness.inspector_calls), ([], 0),
                             "nothing reserved, charged or attempted while the other process holds it")
        finally:
            try:
                holder.communicate("release\n", timeout=PROCESS_BOUND_SECONDS)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.communicate()
        self.assertEqual(holder.returncode, 0)
        self.assertEqual(self.env.qualify()["type"], "Allow")
        self.assertEqual([row["outcome"] for row in self.env.rows()], [SUCCEEDED])

    def test_a_live_attempt_is_never_closed_as_interrupted_by_a_concurrent_qualify(self):
        """The earliest instant after the reservation commit: the attempt already holds the real
        lock, so a second coordinator's qualify (its own port, its own descriptor on the same
        lock file) refuses in progress and leaves the open row alone; the attempt then binds."""
        reserved, release = threading.Event(), threading.Event()
        commit = self.env.coordinator._commit
        commits = []

        def pausing_commit(*args, **kwargs):
            outcome = commit(*args, **kwargs)
            commits.append(outcome)
            if len(commits) == 1:
                reserved.set()
                self.assertTrue(release.wait(PROCESS_BOUND_SECONDS), "the test released the paused attempt")
            return outcome

        rival = ProductionCoordinator(
            Workspace(), Harness(), self.env.runs, self.env.artifacts_repo, PROFILE, {},
            QualifiedSelector(), None, bridge._RunQualificationPort(**self.harness.options()))
        answers = {}
        with mock.patch.object(self.env.coordinator, "_commit", pausing_commit):
            attempt = threading.Thread(
                target=lambda: answers.update(first=self.env.qualify("first")), daemon=True)
            attempt.start()
            try:
                self.assertTrue(reserved.wait(PROCESS_BOUND_SECONDS), "the reservation committed")
                self.assertEqual([row["outcome"] for row in self.env.rows()], [RESERVED])
                answers["rival"] = rival.handle(
                    {"type": "ObserveAction", "run_id": self.env.run_id,
                     "action": json.loads(json.dumps(QUALIFY_ACTION))}, "rival")["result"]
                self.assertEqual([row["outcome"] for row in self.env.rows()], [RESERVED],
                                 "the live row was not closed by the rival")
            finally:
                release.set()
                attempt.join(PROCESS_BOUND_SECONDS)
                self.assertFalse(attempt.is_alive(), "the attempt finished within its bound")
        self.assertEqual((answers["rival"]["type"], answers["rival"]["reason"]),
                         ("Fault", IN_PROGRESS), answers["rival"])
        self.assertEqual(answers["first"]["type"], "Allow", answers["first"])
        self.assertEqual([row["outcome"] for row in self.env.rows()], [SUCCEEDED])
        self.assertEqual(self.env.binding()["kind"], "qualified")

if __name__ == "__main__":
    unittest.main()
