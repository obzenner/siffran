"""PR 16 (A10/A11): the observer's advertise file, the hardened ``observer_url`` reader, and the
form link helpers — ``observe serve`` publishing one file a host form finds, the bounded probe
that decides it is live for *this* store, and the presentation-only URL/compact-id helpers the
forms line beside a bound document.

Everything here uses a temporary machine home (never the developer's real ``$EMPIRICA_HOME``)
and, where a live server is the point, a real ``observe serve`` subprocess on an ephemeral port.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.observer import discovery
from application.observer_index import short_id
from core.records import RunKey

CLI = Path(__file__).resolve().parents[1] / "adapters" / "observer" / "cli.py"
_DEADLINE = 20.0


class Home:
    """One temporary machine home, and the answers a live observer for it gives."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="r16-observer-home-"))

    def serve_record(self) -> dict:
        return json.loads((self.dir / discovery.SERVE_DIR / discovery.SERVE_FILE).read_text())

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class Serve:
    """A real ``observe serve`` subprocess on an ephemeral port, with a stdin pipe so its clean
    exit (and the file's removal) is ours to command (``--exit-with-stdin``)."""

    def __init__(self, home: Path, *extra: str) -> None:
        self.url: str | None = None
        self.port: int | None = None
        self.process = subprocess.Popen(
            [sys.executable, str(CLI), "serve", "--home", str(home), "--port", "0",
             "--exit-with-stdin", *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)

    def wait_listening(self, home: Path) -> None:
        """Wait until the advertise file says this process serves this home (the deadline makes a
        hang a failure, never a wait)."""
        until = time.monotonic() + _DEADLINE
        while time.monotonic() < until:
            url = discovery.observer_url(home)
            if url is not None:
                self.url, self.port = url, int(url.rsplit(":", 1)[1])
                return
            self.assert_alive()
            time.sleep(0.05)
        raise AssertionError("observe serve did not advertise within the deadline")

    def assert_alive(self) -> None:
        if self.process.poll() is not None:
            raise AssertionError(f"observe serve exited: {self.process.poll()}, "
                                 f"{self.process.stdout.read() if self.process.stdout else ''}")

    def stop(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=_DEADLINE)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=_DEADLINE)
        finally:
            self.process.stdout.close()


def free_port() -> int:
    """A port nothing listens on (bound to zero, read its number, close it again)."""
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Sleeper:
    """A live process with a pid we own (stale-pid and foreign-pid tests)."""

    def __init__(self) -> None:
        self.process = subprocess.Popen([sys.executable, "-c",
                                          "import sys; sys.stdin.read()"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    @property
    def pid(self) -> int:
        return self.process.pid

    def stop(self) -> None:
        self.process.stdin.close()
        try:
            self.process.wait(timeout=_DEADLINE)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=_DEADLINE)
        finally:
            self.process.stdout.close()


class FakeAbout:
    """A loopback listener that answers ``/api/about`` exactly what its test supplies, so the
    reader's checks (identity, instance, capabilities, redirects, size) are driven per answer."""

    def __init__(self, mode: str, payload: str) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's own spelling
                if self.path == "/moved":
                    self.send_response(302)
                    self.send_header("Location", "/api/about")
                    self.end_headers()
                    return
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/honey")
                    self.end_headers()
                    return
                body = payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ServeJsonTests(unittest.TestCase):
    """``observe serve`` publishes its advertise file (A10): after it listens, removed on a clean
    exit, but a file that names another live pid is not withdrawn."""

    def setUp(self) -> None:
        self.home = Home()
        self.addCleanup(self.home.cleanup)

    def test_written_once_listening_with_the_declared_modes(self):
        server = Serve(self.home.dir)
        self.addCleanup(server.stop)
        server.wait_listening(self.home.dir)
        record = self.home.serve_record()
        self.assertEqual(record["schema"], discovery.SERVE_SCHEMA)
        self.assertEqual(record["port"], server.port)
        self.assertEqual(record["pid"], server.process.pid)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{32}", record["instance"]), record["instance"])
        self.assertEqual(record["home_identity"], discovery.home_identity(self.home.dir))
        self.assertIn("hitl/1", record["capabilities"])
        self.assertEqual({"schema", "port", "pid", "instance", "home_identity",
                          "capabilities"}, set(record), "the record's exact member set (A10)")
        self.assertNotIn("url", record, "the file carries no URL: the reader builds it")
        file_mode = stat.S_IMODE(os.stat(self.home.dir / discovery.SERVE_DIR
                                         / discovery.SERVE_FILE).st_mode)
        dir_mode = stat.S_IMODE(os.stat(self.home.dir / discovery.SERVE_DIR).st_mode)
        self.assertEqual(file_mode, 0o600)
        self.assertEqual(dir_mode, 0o700)

    def test_removed_on_a_clean_exit(self):
        server = Serve(self.home.dir)
        server.wait_listening(self.home.dir)
        record_pid = self.home.serve_record()["pid"]
        server.stop()
        self.assertFalse((self.home.dir / discovery.SERVE_DIR / discovery.SERVE_FILE).exists())
        self.assertEqual(record_pid, record_pid)  # the withdrawn file had named its own pid

    def test_not_removed_when_the_file_names_another_pid(self):
        server = Serve(self.home.dir)
        self.addCleanup(server.stop)
        server.wait_listening(self.home.dir)
        other = Sleeper()
        self.addCleanup(other.stop)
        discovery.withdraw_serve(self.home.dir, other.pid)  # a foreign live pid is not ours to withdraw
        record = self.home.serve_record()
        self.assertEqual(record["pid"], server.process.pid,
                         "a withdraw that names another pid touches nothing")
        discovery.withdraw_serve(self.home.dir, server.process.pid)  # the clean exit's own name
        self.assertFalse((self.home.dir / discovery.SERVE_DIR / discovery.SERVE_FILE).exists())


class ObserverUrlTests(unittest.TestCase):
    """The hardened reader (A10): every refusal is ``None``, never an exception; the one live
    case is a real ``observe serve`` of this very home."""

    def setUp(self) -> None:
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.directory = self.home.dir / discovery.SERVE_DIR

    def publish(self, home: Path, port: int, pid: int, **changes) -> None:
        instance = changes.pop("instance", discovery.instance_token())
        path = discovery.publish_serve(home, port=port, pid=pid, instance=instance)
        assert path is not None, "the temp home's publication must succeed"
        self.assertEqual(changes, {})

    def test_no_file_is_none(self):
        self.assertIsNone(discovery.observer_url(self.home.dir))

    def test_a_stale_pid_is_none(self):
        sleeper = Sleeper()
        self.addCleanup(sleeper.stop)
        self.publish(self.home.dir, free_port(), sleeper.pid)
        sleeper.stop()  # the process dies; its file stays (a crashed server cannot withdraw)
        self.assertIsNone(discovery.observer_url(self.home.dir))
        discovery.withdraw_serve(self.home.dir, sleeper.pid)  # the file still names it; removing is safe
        self.assertFalse((self.directory / discovery.SERVE_FILE).exists())

    def test_a_live_pid_with_no_listener_is_none(self):
        sleeper = Sleeper()
        self.addCleanup(sleeper.stop)
        self.publish(self.home.dir, free_port(), sleeper.pid)
        self.assertIsNone(discovery.observer_url(self.home.dir))

    def test_a_listener_of_another_home_is_none(self):
        server = Serve(self.home.dir)
        self.addCleanup(server.stop)
        server.wait_listening(self.home.dir)
        elsewhere = Home()
        self.addCleanup(elsewhere.cleanup)
        # a hand-published file that steals the live server's port, but names another home
        self.publish(elsewhere.dir, server.port, os.getpid())
        self.assertEqual(discovery.observer_url(self.home.dir), f"http://127.0.0.1:{server.port}")
        self.assertIsNone(discovery.observer_url(elsewhere.dir),
                          "the listener answers a different home identity: not a link")

    def test_an_instance_mismatch_is_none(self):
        server = Serve(self.home.dir)
        self.addCleanup(server.stop)
        server.wait_listening(self.home.dir)
        self.publish(self.home.dir, server.port, os.getpid())
        self.assertIsNone(discovery.observer_url(self.home.dir),
                         "the advertise names another process's instance token: refused")

    def test_garbage_modes_and_shapes_are_refused(self):
        elsewhere_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(elsewhere_dir, ignore_errors=True))
        record = {"schema": discovery.SERVE_SCHEMA, "port": 18770, "pid": os.getpid(),
                  "instance": discovery.instance_token(),
                  "home_identity": discovery.home_identity(self.home.dir),
                  "capabilities": ["hitl/1"]}
        file = self.directory / discovery.SERVE_FILE
        self.directory.mkdir(parents=True)
        for name, payload in (
            ("garbage bytes", b"(not json"),
            ("a wrong schema", json.dumps({**record, "schema": "other"}).encode()),
            ("a url member is never trusted", json.dumps({**record, "url":
                                    "http://evil.example/"}).encode()),
            ("a non-integer pid", json.dumps({**record, "pid": "1"}).encode()),
            ("a zero pid", json.dumps({**record, "pid": 0}).encode()),
            ("a port below the range", json.dumps({**record, "port": 1023}).encode()),
            ("a port above the range", json.dumps({**record, "port": 65536}).encode()),
            ("an instance that is not a token", json.dumps({**record, "instance": "bad token!"}).encode()),
            ("an empty capabilities list", json.dumps({**record, "capabilities": []}).encode()),
        ):
            with self.subTest(name):
                file.write_bytes(payload)
                os.chmod(file, 0o600)
                os.chmod(self.directory, 0o700)
                self.assertIsNone(discovery.observer_url(self.home.dir))
        # hostilities against the file itself: a symlink file, a symlinked directory, a widened mode
        file.write_bytes(b"{}")
        file.unlink()
        file.symlink_to("/tmp/target.json")
        self.assertIsNone(discovery.observer_url(self.home.dir))
        file.unlink()
        self.directory.rmdir()
        self.directory.symlink_to(elsewhere_dir)
        self.assertIsNone(discovery.observer_url(self.home.dir))

    def test_a_refused_mode_or_owner_is_ignored(self):
        self.directory.mkdir(parents=True)
        self.publish(self.home.dir, 18770, os.getpid())
        file = self.directory / discovery.SERVE_FILE
        os.chmod(file, 0o644)
        self.assertIsNone(discovery.observer_url(self.home.dir), "a widened file mode is refused")
        os.chmod(file, 0o600)
        os.chmod(self.directory, 0o755)
        self.assertIsNone(discovery.observer_url(self.home.dir), "a widened directory is refused")
        os.chmod(self.directory, 0o700)

    def test_an_answer_without_the_hitl_capability_is_none(self):
        """A real find, an old observer: ``/api/about`` answers nothing the reader can trust
        (ADR-0072's amendment — a running old observer must be restarted after upgrade)."""
        good = {"observer": "empirica-observer", "identity": "x", "home_identity":
                discovery.home_identity(self.home.dir)}
        for name, payload in (
            ("no capabilities", json.dumps(good)),
            ("no hitl/1", json.dumps({**good, "capabilities": ["other/1"]})),
            ("no home_identity", json.dumps({"observer": "empirica-observer",
                                             "capabilities": ["hitl/1"]})),
            ("no instance", json.dumps({**good, "capabilities": ["hitl/1"]})),
            ("not an observer's answer", json.dumps({"observer": "someone"})),
            ("not json", "(json-ish"),
        ):
            with self.subTest(name):
                fake = FakeAbout("answer", payload)
                self.addCleanup(fake.stop)
                self.publish(self.home.dir, fake.port, os.getpid())
                self.assertIsNone(discovery.observer_url(self.home.dir))
                discovery.withdraw_serve(self.home.dir, os.getpid())

    def test_a_redirected_probe_is_none(self):
        """No redirects (A10): an answer that moves the probe elsewhere is not an answer."""
        fake = FakeAbout("redirect", "")
        self.addCleanup(fake.stop)
        self.publish(self.home.dir, fake.port, os.getpid())
        self.assertIsNone(discovery.observer_url(self.home.dir))
        discovery.withdraw_serve(self.home.dir, os.getpid())

    def test_none_home_is_none_and_nothing_raises(self):
        self.assertIsNone(discovery.observer_url(None))
        self.assertIsNone(discovery.observer_url(Path("/definitely/not/a/home")))
        self.assertEqual(discovery.document_details(None, "er2:anything", {"digest": "sha256:" + "a" * 64}),
                         {"url": None, "run12": None})



class DocumentDetailsTests(unittest.TestCase):
    """The forms' one-probe-per-presentation helpers (A10/A11): the link, the compact id, the
    digest's short form, and their fallbacks; presentation only."""

    def setUp(self) -> None:
        self.home = Home()
        self.addCleanup(self.home.cleanup)
        self.server = Serve(self.home.dir)
        self.addCleanup(self.server.stop)
        self.server.wait_listening(self.home.dir)

    @staticmethod
    def run_key() -> RunKey:
        return RunKey("s256-" + "3f9c2a81b7d45e6c9f0a1b2c3d4e5f67" * 2,
                      "s256-" + "5e0a91c88f2b7d4c6e9a0b1d3f4e5278" * 2, 1)

    def test_the_compact_id_is_the_observers_short_id_of_the_key(self):
        from application.location import encode_handle
        key = self.run_key()
        handle = encode_handle(key)
        self.assertEqual(discovery.compact_run_id(handle), short_id("/".join(
            (key.project_id, key.run_id, str(key.generation)))))
        self.assertIsNone(discovery.compact_run_id("not a handle"))

    def test_a_live_observer_links_to_the_decision_row(self):
        from application.location import encode_handle
        handle = encode_handle(self.run_key())
        compact = discovery.compact_run_id(handle)
        document = {"digest": "sha256:" + "0" * 19 + "b" * 45}
        details = discovery.document_details(self.home.dir, handle, document)
        self.assertEqual(details, {"url": f"http://127.0.0.1:{self.server.port}"
                                          f"/app/?run={compact}&decision={'0' * 12}",
                                   "run12": compact})
        # the digest's own 12-hex short form is what the UI resolves (never the whole digest)
        self.assertIn("&decision=" + document["digest"][7:19], details["url"])

    def test_no_live_observer_leaves_only_the_fallback_fields(self):
        from application.location import encode_handle
        handle = encode_handle(self.run_key())
        elsewhere = Home()  # no observer serves this home
        self.addCleanup(elsewhere.cleanup)
        details = discovery.document_details(elsewhere.dir, handle,
                                              {"digest": "sha256:" + "0" * 64})
        self.assertIsNone(details["url"])
        self.assertEqual(details["run12"], discovery.compact_run_id(handle))

    def test_the_details_never_raise_on_hostile_input(self):
        for document in ({"digest": "sha256:short"}, None, "text", {"digest": 7}):
            self.assertEqual(discovery.document_details(self.home.dir, None, document)["url"], None)


if __name__ == "__main__":
    unittest.main()
