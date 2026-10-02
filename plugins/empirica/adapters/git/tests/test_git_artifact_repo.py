#!/usr/bin/env python3
"""Real-temp-git integration + concurrency suite for the Git artifact adapter (ADR-31).

Run: make empirica-git-check (stdlib + Git).
Exit 0 = all pass; 1 = at least one failed.

Tests use throwaway Git repositories under ``tempfile``. Integrity, concurrency and isolation
checks drive real Git; malformed batch frames are injected at the byte-transport seam to verify
refusal. The suite covers the confirmations ADR-31 requires: concurrent CAS, append commutativity/idempotency,
generation isolation, no-remote operation, distinct absent-vs-corrupt reads, id/body collision, and
a byte-identical user HEAD/index/worktree across an artifact write.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
PLUGIN = HERE.parents[3]  # plugins/empirica — makes `core` and `adapters` importable
sys.path.insert(0, str(PLUGIN))

from adapters.git.artifact_repo import (  # noqa: E402
    ArtifactCollision,
    GitArtifactRepository,
    GitError,
    _CorruptTree,
)
from core.records import ABSENT, Artifact, Corrupt, Present, RunKey  # noqa: E402


def _run(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args} failed: {proc.stderr}")
    return proc


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _run(root, "init", "-q", "-b", "main", ".")
    _run(root, "config", "user.email", "user@example.com")
    _run(root, "config", "user.name", "User")
    (root / "tracked.txt").write_text("hello\n")
    _run(root, "add", "tracked.txt")
    _run(root, "commit", "-qm", "initial")
    return root


def _cid(body: str) -> str:
    """A stand-in producer content address (the adapter treats artifact_id as opaque)."""
    return hashlib.sha256(body.encode()).hexdigest()


def _art(body: str) -> Artifact:
    return Artifact(_cid(body), body)


class GitArtifactRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _init_repo(Path(self._tmp.name) / "repo")
        self.repo = GitArtifactRepository(self.root)
        self.key = RunKey(project_id="proj/one", run_id="run-1", generation=1)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- basic roundtrip ------------------------------------------------------

    def test_absent_before_any_append(self):
        self.assertIs(self.repo.read(self.key), ABSENT)

    def test_append_then_read_roundtrip(self):
        a, b = _art("claim-A"), _art("evidence-B")
        self.repo.append(self.key, a)
        self.repo.append(self.key, b)
        result = self.repo.read(self.key)
        self.assertIsInstance(result, Present)
        self.assertEqual(result.value, frozenset({a, b}))

    def test_unicode_and_multiline_body_roundtrips(self):
        a = _art("π ≈ 3.14\nlíne two\t\U0001f9ea")  # non-ASCII + newline + tab + emoji
        self.repo.append(self.key, a)
        self.assertEqual(self.repo.read(self.key).value, frozenset({a}))

    def test_nonempty_read_uses_one_batch_process_independent_of_artifact_count(self):
        class CountingRepository(GitArtifactRepository):
            def __init__(self, root):
                super().__init__(root)
                self.commands = []

            def _git(self, *args, **kwargs):
                self.commands.append(args)
                return super()._git(*args, **kwargs)

            def _git_bytes(self, *args, **kwargs):
                self.commands.append(args)
                return super()._git_bytes(*args, **kwargs)

        counted = CountingRepository(self.root)
        artifacts = [_art(f"batch-{index}") for index in range(6)]
        for value in artifacts:
            counted.append(self.key, value)
        counted.commands.clear()
        result = counted.read(self.key)
        self.assertEqual(result.value, frozenset(artifacts))
        self.assertEqual(len(counted.commands), 5)
        self.assertEqual(
            [command for command in counted.commands if command[0] == "cat-file"],
            [("cat-file", "--batch")],
        )

    def test_empty_tree_does_not_start_batch_process(self):
        class CountingRepository(GitArtifactRepository):
            def __init__(self, root):
                super().__init__(root)
                self.batch_calls = 0

            def _git_bytes(self, *args, **kwargs):
                self.batch_calls += 1
                return super()._git_bytes(*args, **kwargs)

        counted = CountingRepository(self.root)
        tree = _run(self.root, "mktree", stdin="").stdout.strip()
        commit = _run(self.root, "commit-tree", tree, stdin="empty").stdout.strip()
        _run(self.root, "update-ref", counted.ref_for(self.key), commit)
        result = counted.read(self.key)
        self.assertEqual(result.value, frozenset())
        self.assertEqual(counted.batch_calls, 0)

    def test_batch_parser_preserves_raw_crlf_utf8_and_multiple_blobs(self):
        artifacts = [Artifact("utf8-id", "π line"), Artifact("second-id", "second")]
        encoded = [
            json.dumps(
                {"id": artifact.artifact_id, "body": artifact.body},
                sort_keys=True, ensure_ascii=False, indent=2,
            ).replace("\n", "\r\n")
            for artifact in artifacts
        ]
        self.assertTrue(all("\r\n" in body for body in encoded))
        oids = [
            _run(self.root, "hash-object", "-w", "--stdin", stdin=body).stdout.strip()
            for body in encoded
        ]
        descriptors = [(artifact.artifact_id, oid)
                       for artifact, oid in zip(artifacts, oids, strict=True)]

        entries = self.repo._batch_read_entries(descriptors)
        self.assertEqual([entries[value.artifact_id].body for value in artifacts],
                         [value.body for value in artifacts])

    def test_batch_parser_rejects_oversized_decimal_size_header(self):
        oid = "a" * 40
        output = f"{oid} blob {'9' * 4301}\n".encode("ascii")

        class FramedRepository(GitArtifactRepository):
            def _git_bytes(self, *args, **kwargs):
                return subprocess.CompletedProcess(args, 0, output, b"")

        with self.assertRaises(_CorruptTree):
            FramedRepository(self.root)._batch_read_entries([("a", oid)])

    def test_batch_parser_rejects_failure_after_valid_first_frame(self):
        first_oid, second_oid = "a" * 40, "b" * 40
        first_body = b'{"body":"first","id":"first"}'
        second_body = b'{"body":"second","id":"second"}'
        first = f"{first_oid} blob {len(first_body)}\n".encode("ascii") + first_body + b"\n"
        second = (f"{second_oid} blob {len(second_body)}\n".encode("ascii")
                  + second_body + b"\n")
        failures = {
            "reordered second oid": first + second.replace(second_oid.encode(), b"c" * 40, 1),
            "missing second frame": first,
            "malformed second header": first + b"malformed\n",
            "truncated second body": first + second[:-5],
        }
        descriptors = [("first", first_oid), ("second", second_oid)]
        for label, output in failures.items():
            with self.subTest(label=label):
                class FramedRepository(GitArtifactRepository):
                    def _git_bytes(self, *args, **kwargs):
                        return subprocess.CompletedProcess(args, 0, output, b"")

                with self.assertRaises(_CorruptTree):
                    FramedRepository(self.root)._batch_read_entries(descriptors)

    def test_batch_parser_rejects_malformed_or_incomplete_output(self):
        oid = "a" * 40
        body = b'{"body":"x","id":"a"}'
        valid = f"{oid} blob {len(body)}\n".encode("ascii") + body + b"\n"
        failures = {
            "empty output": b"",
            "wrong oid": valid.replace(oid.encode("ascii"), b"b" * 40, 1),
            "wrong type": valid.replace(b" blob ", b" tree ", 1),
            "bad size": valid.replace(str(len(body)).encode("ascii"), b"nope", 1),
            "huge size": valid.replace(str(len(body)).encode("ascii"), b"999999999999", 1),
            "missing header newline": valid.replace(b"\n", b"", 1),
            "truncated body": valid[:-2] + b"\n",
            "missing delimiter": valid[:-1],
            "wrong delimiter": valid[:-1] + b"x",
            "trailing data": valid + b"extra",
            "missing object": f"{oid} missing\n".encode("ascii"),
        }

        for label, output in failures.items():
            with self.subTest(label=label):
                class FramedRepository(GitArtifactRepository):
                    def _git_bytes(self, *args, **kwargs):
                        return subprocess.CompletedProcess(args, 0, output, b"")

                with self.assertRaisesRegex(_CorruptTree, ".+"):
                    FramedRepository(self.root)._batch_read_entries([("a", oid)])

    def test_batch_process_failure_is_corrupt(self):
        oid = "a" * 40

        class FailedRepository(GitArtifactRepository):
            def _git_bytes(self, *args, **kwargs):
                return subprocess.CompletedProcess(args, 1, b"", b"failed")

        with self.assertRaisesRegex(_CorruptTree, "batch-read"):
            FailedRepository(self.root)._batch_read_entries([("a", oid)])

    def test_ref_lives_under_refs_empirica(self):
        self.repo.append(self.key, _art("x"))
        ref = self.repo.ref_for(self.key)
        self.assertTrue(ref.startswith("refs/empirica/artifacts/"))
        _run(self.root, "show-ref", "--verify", "--quiet", ref)  # raises if missing

    def test_ref_components_with_double_dots_are_valid(self):
        key = RunKey("project..name", "run..name", 1)
        self.repo.append(key, _art("x"))
        ref = self.repo.ref_for(key)
        self.assertNotIn("..", ref)
        _run(self.root, "check-ref-format", ref)

    # --- idempotency / commutativity -----------------------------------------

    def test_append_is_idempotent_and_leaves_ref_unmoved(self):
        a = _art("dup")
        self.repo.append(self.key, a)
        ref = self.repo.ref_for(self.key)
        head1 = _run(self.root, "rev-parse", ref).stdout.strip()
        self.repo.append(self.key, a)  # identical artifact again
        head2 = _run(self.root, "rev-parse", ref).stdout.strip()
        self.assertEqual(head1, head2, "idempotent append must not move the ref")
        self.assertEqual(len(self.repo.read(self.key).value), 1)

    def test_commutative_same_set_regardless_of_order(self):
        arts = [_art(f"c{i}") for i in range(5)]
        self.repo.append(self.key, arts[0])
        self.repo.append(self.key, arts[1])
        self.repo.append(self.key, arts[2])

        other = GitArtifactRepository(self.root)
        key2 = RunKey("proj/one", "run-2", 1)
        for a in reversed(arts[:3]):
            other.append(key2, a)

        self.assertEqual(self.repo.read(self.key).value, other.read(key2).value)

    def test_identical_set_yields_identical_tree_revision(self):
        arts = [_art(f"t{i}") for i in range(3)]
        for a in arts:
            self.repo.append(self.key, a)
        rev_forward = self.repo.read(self.key).revision

        key2 = RunKey("proj/one", "run-rev", 1)
        for a in reversed(arts):
            self.repo.append(key2, a)
        rev_reverse = self.repo.read(key2).revision
        self.assertEqual(rev_forward, rev_reverse, "revision is a digest of the set, order-free")

    # --- collision = corruption ----------------------------------------------

    def test_same_id_different_body_is_collision(self):
        good = Artifact("shared-id", "body-one")
        self.repo.append(self.key, good)
        with self.assertRaises(ArtifactCollision):
            self.repo.append(self.key, Artifact("shared-id", "body-two"))
        # The original survives, untouched.
        self.assertEqual(self.repo.read(self.key).value, frozenset({good}))

    def test_append_refuses_existing_corrupt_tree_without_moving_ref(self):
        self._plant_tree({"aaa": "not json at all"})
        ref = self.repo.ref_for(self.key)
        before = _run(self.root, "rev-parse", ref).stdout.strip()
        with self.assertRaisesRegex(GitError, "cannot append onto corrupt store"):
            self.repo.append(self.key, _art("must-not-append"))
        self.assertEqual(_run(self.root, "rev-parse", ref).stdout.strip(), before)

    # --- absent vs corrupt ----------------------------------------------------

    def test_generation_isolation(self):
        self.repo.append(self.key, _art("g1"))
        other_gen = RunKey(self.key.project_id, self.key.run_id, 2)
        self.assertIs(self.repo.read(other_gen), ABSENT)

    def test_ref_pointing_at_non_commit_reads_corrupt(self):
        ref = self.repo.ref_for(self.key)
        blob = _run(self.root, "hash-object", "-w", "--stdin", stdin="junk").stdout.strip()
        _run(self.root, "update-ref", ref, blob)
        self.assertIsInstance(self.repo.read(self.key), Corrupt)

    def test_non_json_blob_reads_corrupt(self):
        self._plant_tree({"aaa": "not json at all"})
        self.assertIsInstance(self.repo.read(self.key), Corrupt)

    def test_path_id_mismatch_reads_corrupt(self):
        # Path says "aaa" but the blob records a different id — tampering / collision on disk.
        import json
        self._plant_tree({"aaa": json.dumps({"id": "bbb", "body": "x"})})
        self.assertIsInstance(self.repo.read(self.key), Corrupt)

    def test_non_utf8_blob_reads_corrupt_not_raises(self):
        # A tampered blob that is not valid UTF-8 must read back as Corrupt, never raise (ADR-31:
        # read must not raise for corruption). Write raw bytes directly as the tree's only blob.
        oid = subprocess.run(
            ["git", "-C", str(self.root), "hash-object", "-w", "--stdin"],
            input=b"\xff\xfe\x00 not utf-8", capture_output=True,
        ).stdout.decode().strip()
        tree = _run(self.root, "mktree", stdin=f"100644 blob {oid}\taaa\n").stdout.strip()
        commit = _run(self.root, "commit-tree", tree, stdin="planted").stdout.strip()
        _run(self.root, "update-ref", self.repo.ref_for(self.key), commit)
        self.assertIsInstance(self.repo.read(self.key), Corrupt)

    def _plant_tree(self, path_to_content: dict[str, str]) -> None:
        """Force the run's ref to a commit over a hand-built (possibly corrupt) tree."""
        spec = ""
        for path, content in path_to_content.items():
            oid = _run(self.root, "hash-object", "-w", "--stdin", stdin=content).stdout.strip()
            spec += f"100644 blob {oid}\t{path}\n"
        tree = _run(self.root, "mktree", stdin=spec).stdout.strip()
        commit = _run(self.root, "commit-tree", tree, stdin="planted").stdout.strip()
        _run(self.root, "update-ref", self.repo.ref_for(self.key), commit)

    # --- isolation guarantees -------------------------------------------------

    def test_user_head_index_worktree_byte_identical(self):
        # Dirty the index and worktree so we prove even uncommitted user state is preserved.
        (self.root / "staged.txt").write_text("staged content\n")
        _run(self.root, "add", "staged.txt")
        (self.root / "dirty.txt").write_text("unstaged\n")

        before = self._user_state()
        for i in range(4):
            self.repo.append(self.key, _art(f"iso{i}"))
        after = self._user_state()
        self.assertEqual(before, after, "artifact writes must not touch HEAD/index/worktree")

    def _user_state(self):
        head = _run(self.root, "rev-parse", "HEAD").stdout.strip()
        index = (self.root / ".git" / "index").read_bytes()
        status = _run(self.root, "status", "--porcelain").stdout
        tracked = (self.root / "tracked.txt").read_bytes()
        return (head, index, status, tracked)

    def test_no_remote_operation(self):
        # A throwaway repo has no remotes; the adapter must work anyway (local-only, ADR-31).
        remotes = _run(self.root, "remote").stdout.strip()
        self.assertEqual(remotes, "", "precondition: no remotes configured")
        self.repo.append(self.key, _art("local-only"))
        self.assertEqual(len(self.repo.read(self.key).value), 1)

    def test_linked_worktree_shares_namespace(self):
        wt = Path(self._tmp.name) / "linked-wt"
        _run(self.root, "worktree", "add", "-q", str(wt), "-b", "feature")
        # Append via the linked worktree; read via the main checkout. Same run must be visible.
        linked = GitArtifactRepository(wt)
        linked.append(self.key, _art("from-linked"))
        result = self.repo.read(self.key)
        self.assertIsInstance(result, Present)
        self.assertEqual(result.value, frozenset({_art("from-linked")}))

    def test_sha256_repository_roundtrip(self):
        # The CAS create sentinel must be object-format agnostic: a SHA-256 repo rejects a
        # 40-hex zero oid, so the adapter uses an empty old-value instead. Prove first-append works.
        root = Path(self._tmp.name) / "sha256"
        root.mkdir()
        made = subprocess.run(
            ["git", "-C", str(root), "init", "-q", "--object-format=sha256", "-b", "main", "."],
            capture_output=True, text=True,
        )
        if made.returncode != 0:
            self.skipTest("git build does not support --object-format=sha256")
        _run(root, "config", "user.email", "u@e")
        _run(root, "config", "user.name", "U")
        (root / "f").write_text("x\n")
        _run(root, "add", "f")
        _run(root, "commit", "-qm", "i")

        repo = GitArtifactRepository(root)
        a, b = _art("s256-A"), _art("s256-B")
        repo.append(self.key, a)  # first append -> exercises the create sentinel
        repo.append(self.key, b)
        self.assertEqual(repo.read(self.key).value, frozenset({a, b}))

    # --- concurrency ----------------------------------------------------------

    def test_concurrent_distinct_appends_all_survive(self):
        n = 12
        arts = [_art(f"concurrent-{i}") for i in range(n)]
        barrier = threading.Barrier(n)
        errors: list[BaseException] = []

        def worker(a: Artifact):
            try:
                barrier.wait()  # maximise real overlap / CAS contention
                GitArtifactRepository(self.root).append(self.key, a)
            except BaseException as exc:  # noqa: BLE001 - surface any thread failure
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(a,)) for a in arts]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"no append should fail under contention: {errors}")
        result = self.repo.read(self.key)
        self.assertIsInstance(result, Present)
        self.assertEqual(result.value, frozenset(arts), "every distinct artifact must survive")

    def test_concurrent_identical_appends_are_idempotent(self):
        n = 8
        a = _art("same-under-race")
        barrier = threading.Barrier(n)
        errors: list[BaseException] = []

        def worker():
            try:
                barrier.wait()
                GitArtifactRepository(self.root).append(self.key, a)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(self.repo.read(self.key).value, frozenset({a}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
