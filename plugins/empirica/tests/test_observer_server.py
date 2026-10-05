#!/usr/bin/env python3
"""The observer server (``adapters/observer/server.py``): what it serves, and what it refuses.

Loopback only, GET and HEAD only, a ``Host`` that is not its own refused (DNS rebinding), no CORS,
an allowlist of UI files with no way out of it, the store summary behind an ETag, a run's detail by
key, and the vendored demo data as a second source. The store is ``observer_store.build_store``.

Run: python3 plugins/empirica/tests/test_observer_server.py
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

_HERE = Path(__file__).resolve().parent
_PLUGIN = _HERE.parent
for _path in (_HERE / "v2", _HERE, _PLUGIN):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.observer import server as server_module  # noqa: E402
from adapters.observer.server import DEMO, Demo, Sources, serve, static_path  # noqa: E402
from application.observer import Observer  # noqa: E402
from observer_store import DriverSource, build_store  # noqa: E402

_STORE = build_store()


class _Served:
    """A running server on a free loopback port, and a client for it."""

    def __init__(self, sources: Sources, root: Path = server_module.UI) -> None:
        self.server = serve(sources, 0, root)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def get(self, path: str, method: str = "GET", host: str | None = None, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{self.port}")
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observer = Observer(DriverSource(_STORE))
        cls.view, cls.details = cls.observer.observe()
        cls.served = _Served(Sources(cls.observer))

    @classmethod
    def tearDownClass(cls):
        cls.served.close()

    def test_it_listens_on_loopback_only(self):
        self.assertEqual(self.served.server.server_address[0], "127.0.0.1")

    def test_a_request_for_another_host_is_refused(self):
        for host in ("evil.example", f"evil.example:{self.served.port}", "127.0.0.1", ""):
            with self.subTest(host=host):
                response, _ = self.served.get("/api/view", host=host)
                self.assertEqual(response.status, 403)
        response, _ = self.served.get("/api/view", host=f"localhost:{self.served.port}")
        self.assertEqual(response.status, 200)

    def test_the_summary_is_the_observer_s_with_its_token_as_etag(self):
        response, body = self.served.get("/api/view")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body), self.view)
        self.assertEqual(response.getheader("ETag"), f'"{self.view["token"]}"')
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_an_unchanged_store_answers_304_with_no_body(self):
        response, body = self.served.get("/api/view", headers={"If-None-Match": f'"{self.view["token"]}"'})
        self.assertEqual((response.status, body), (304, b""))
        response, _ = self.served.get("/api/view", headers={"If-None-Match": '"sha256:other"'})
        self.assertEqual(response.status, 200)

    def test_a_run_s_detail_is_served_by_key_and_an_unknown_key_is_404(self):
        key = next(iter(self.details))
        response, body = self.served.get(f"/api/run?key={quote(key, safe='')}")
        self.assertEqual((response.status, json.loads(body)), (200, self.details[key]))
        for path in ("/api/run?key=nope", "/api/run"):
            with self.subTest(path=path):
                self.assertEqual(self.served.get(path)[0].status, 404)

    def test_the_demo_source_is_the_vendored_fixtures(self):
        response, body = self.served.get("/api/view?source=demo")
        self.assertEqual(json.loads(body), json.loads((DEMO / "view.json").read_text(encoding="utf-8")))
        self.assertEqual(self.served.get("/api/view?source=other")[0].status, 404)

    def test_without_a_store_only_the_demo_is_served(self):
        served = _Served(Sources(None))
        try:
            self.assertEqual(served.get("/api/view")[0].status, 404)
            self.assertEqual(served.get("/api/view?source=demo")[0].status, 200)
        finally:
            served.close()

    def test_without_a_store_the_landing_page_is_the_demo(self):
        served = _Served(Sources(None))
        try:
            self.assertEqual(served.get("/")[0].getheader("Location"), "/app/?data=demo")
        finally:
            served.close()
        self.assertEqual(self.served.get("/")[0].getheader("Location"), "/app/")

    def test_an_observer_that_fails_answers_500_and_the_server_goes_on(self):
        class Broken:
            def observe(self):
                raise RuntimeError("boom")
        served = _Served(Sources(Broken()))
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual([served.get("/api/view")[0].status for _ in range(2)], [500, 500])
            lines = err.getvalue().splitlines()
            self.assertEqual((lines[0], lines[-2:]), ("Traceback (most recent call last):",
                                                      ["observer: RuntimeError: boom"] * 2),
                             "the traceback once, then one line per failure")
            self.assertEqual(err.getvalue().count("Traceback"), 1)
            self.assertEqual(served.get("/api/view?source=demo")[0].status, 200)
        finally:
            served.close()

    def test_only_get_and_head_are_served(self):
        self.assertEqual(self.served.get("/api/view", method="HEAD")[1], b"")
        for method in ("POST", "PUT", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                self.assertNotIn(self.served.get("/api/view", method=method)[0].status, range(200, 400))

    def test_no_response_allows_another_origin(self):
        for path in ("/api/view", "/app/", "/nope"):
            response, _ = self.served.get(path, headers={"Origin": "https://evil.example"})
            self.assertFalse([h for h, _ in response.getheaders() if h.lower().startswith("access-control-")], path)

    def test_the_ui_is_served_with_a_same_origin_policy(self):
        response, body = self.served.get("/app/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"<ev-split", body)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.served.get("/")[0].getheader("Location"), "/app/")
        response, _ = self.served.get("/elements/core/element.js")
        self.assertTrue(response.getheader("Content-Type").startswith("text/javascript"))

    def test_nothing_outside_the_allowlist_is_served(self):
        for path in ("/package.json", "/package-lock.json", "/node_modules/tailwindcss/package.json",
                     "/tests/browser.check.mjs", "/scripts/audit.mjs", "/vendor/VERSIONS",
                     "/app/../package.json", "/app/%2e%2e/package.json", "/app/..%2fpackage.json",
                     "//etc/passwd", "/app/.hidden.js", "/../../core/observer.py"):
            with self.subTest(path=path):
                self.assertEqual(self.served.get(path)[0].status, 404)


class StaticPathTests(unittest.TestCase):
    def test_a_symlink_out_of_the_root_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, outside = Path(tmp) / "ui", Path(tmp) / "secret.json"
            (root / "app").mkdir(parents=True)
            outside.write_text("{}", encoding="utf-8")
            (root / "app" / "index.html").write_text("<p>", encoding="utf-8")
            os.symlink(outside, root / "app" / "leak.json")
            self.assertEqual(static_path(root, "/app/"), (root / "app" / "index.html").resolve())
            self.assertIsNone(static_path(root, "/app/leak.json"))
            (root / ".secret.json").write_text("{}", encoding="utf-8")
            os.symlink(root / ".secret.json", root / "app" / "inner.json")
            self.assertIsNone(static_path(root, "/app/inner.json"), "a link to a dotfile inside the root")

    def test_about_names_the_observer_and_which_store_it_observes(self):
        served = _Served(Sources(None, identity="sha256:abc"))
        try:
            reply, body = served.get("/api/about")
            self.assertEqual((reply.status, json.loads(body)),
                             (200, {"observer": "empirica-observer", "identity": "sha256:abc"}))
        finally:
            served.close()

    def test_a_second_serve_of_the_same_store_reuses_the_first_and_another_store_does_not(self):
        from adapters.observer import cli
        served = _Served(Sources(None, identity="demo"))
        try:
            self.assertTrue(cli.running(served.port, "demo"))
            self.assertFalse(cli.running(served.port, "sha256:another-store"))
            cmd = [sys.executable, str(_PLUGIN / "adapters" / "observer" / "cli.py"), "serve", "--demo",
                   "--port", str(served.port)]
            again = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            self.assertEqual((again.returncode, again.stdout.strip()),
                             (0, f"Empirica observer (already running): http://127.0.0.1:{served.port}/app/?data=demo"))
        finally:
            served.close()
        self.assertFalse(cli.running(served.port, "demo"), "nothing answers once it stopped")

    def test_with_exit_with_stdin_the_server_ends_when_its_parent_s_pipe_closes(self):
        cli = _PLUGIN / "adapters" / "observer" / "cli.py"
        with subprocess.Popen([sys.executable, str(cli), "serve", "--demo", "--port", "0", "--exit-with-stdin",
                               "--repo", "/definitely/not/a/repo"],  # the demo opens no store, so this is unread
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            try:
                self.assertIn(b"http://127.0.0.1:", proc.stdout.readline())
                proc.stdin.close()
                self.assertEqual(proc.wait(timeout=10), 0)
            finally:
                if proc.poll() is None:
                    proc.kill()

    def test_the_demo_data_is_vendored_with_the_plugin(self):
        view, details = Demo().observe()
        self.assertEqual(set(details), {r["key"] for p in view["projects"] for r in p["runs"]})


if __name__ == "__main__":
    unittest.main(verbosity=1)
