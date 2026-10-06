"""``observe serve``: the observer read model and the UI over HTTP, for this machine only.

    GET /api/view?source=store|demo          the store summary; ETag = its token, 304 when unchanged
    GET /api/run?key=KEY&source=store|demo   one run's detail
    GET /api/about                           which store this server observes (a digest), so a second
                                             ``serve`` of the same store on the same port reuses it
    GET /app/, /gallery/, ...                the UI's files (an allowlist under plugins/empirica/ui)

Security: bound to 127.0.0.1 only. Only GET and HEAD are served. A request whose ``Host`` is not this
server's loopback address is refused, which defeats DNS rebinding. No CORS headers are sent, so
another origin cannot read a response. A Content-Security-Policy allows only this origin. Nothing is
written: the observer reads, and the server only reads files from the allowlist.

``source=demo`` serves the contract fixtures vendored into the plugin
(``vendor/contracts/empirica-ui-view/v1/fixtures``): real runs the plugin produced, for trying the
UI and for its checks.
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from application.observer import Observer

#: What ``/api/about`` names this server, so a client knows it is an Empirica observer.
ABOUT = "empirica-observer"
PLUGIN = Path(__file__).resolve().parents[2]
UI = PLUGIN / "ui"
DEMO = PLUGIN / "vendor/contracts/empirica-ui-view/v1/fixtures"
HOST = "127.0.0.1"
#: What the UI may load: top-level folders and files under ``UI``, by extension.
SERVED_DIRS = ("app", "gallery", "elements", "dist", "vendor", "copy")
SERVED_FILES = ("states.json", "catalogue.json", "icons.json")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml", ".woff2": "font/woff2"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; font-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class Demo:
    """The vendored fixtures, read once: a fixed view and its details."""

    def __init__(self, folder: Path = DEMO) -> None:
        self._view = json.loads((folder / "view.json").read_text(encoding="utf-8"))
        self._details = json.loads((folder / "runs.json").read_text(encoding="utf-8"))

    def view(self) -> dict[str, Any]:
        return self._view

    def detail(self, key: str) -> dict[str, Any] | None:
        return self._details.get(key)


class Sources:
    """The two data sources, each observed under one lock (the observer's cache is not shared
    across threads)."""

    def __init__(self, store: Observer | None, demo: Demo | None = None, identity: str = "demo") -> None:
        self._sources = {"store": store, "demo": demo or Demo()}
        self.identity = identity  # which store is observed (cli.identity): what /api/about answers
        self._lock = threading.Lock()
        self._reported: set[str] = set()

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, source in self._sources.items() if source is not None)

    def has(self, name: str) -> bool:
        return self._sources.get(name) is not None

    def view(self, name: str) -> dict[str, Any]:
        with self._lock:
            return self._sources[name].view()

    def detail(self, name: str, key: str) -> dict[str, Any] | None:
        """One run's detail, projected alone (the store's other runs are not)."""
        with self._lock:
            return self._sources[name].detail(key)

    def report(self, exc: Exception) -> None:
        """Log an observer failure: its traceback the first time, then one line per repeat, so a
        failure that every poll meets is diagnosable without flooding the terminal."""
        line = f"observer: {type(exc).__name__}: {exc}"
        with self._lock:
            first = line not in self._reported
            self._reported.add(line)
        if first:
            traceback.print_exception(exc, file=sys.stderr)
        print(line, file=sys.stderr, flush=True)


def static_path(root: Path, url_path: str) -> Path | None:
    """The served file for ``url_path``, or ``None``: allowlisted folders and files only, no dot
    segments or dotfiles, no symlinks out of ``root``, known extensions only."""
    parts = [p for p in url_path.split("/") if p]
    if url_path.endswith("/"):
        parts.append("index.html")
    if not parts or any(p.startswith(".") or "\\" in p for p in parts):
        return None
    if not ((len(parts) == 1 and parts[0] in SERVED_FILES) or (len(parts) > 1 and parts[0] in SERVED_DIRS)):
        return None
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    base = root.resolve()
    if not resolved.is_relative_to(base) or not resolved.is_file() or resolved.suffix not in TYPES:
        return None
    if any(p.startswith(".") for p in resolved.relative_to(base).parts):  # a link to a dotfile inside root
        return None
    return resolved


def handler_for(sources: Sources, root: Path = UI):
    class Handler(BaseHTTPRequestHandler):
        server_version = "empirica-observer"
        sys_version = ""

        def log_message(self, format, *args):  # quiet: the terminal shows only the URL
            pass

        def _send(self, status: int, body: bytes = b"", kind: str | None = None,
                  headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            if kind:
                self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, value: Any, headers: dict[str, str] | None = None) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._send(200, body, TYPES[".json"], headers)

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host") in {f"{HOST}:{port}", f"localhost:{port}"}

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden host\n", "text/plain; charset=utf-8")
            url = urlsplit(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                landing = "/app/" if "store" in sources.names() else "/app/?data=demo"
                return self._send(302, headers={"Location": landing})
            if url.path in ("/api/view", "/api/run"):
                return self._api(url.path, query)
            if url.path == "/api/about":
                return self._json({"observer": ABOUT, "identity": sources.identity})
            path = static_path(root, url.path)
            if path is None:
                return self._send(404, b"not found\n", "text/plain; charset=utf-8")
            self._send(200, path.read_bytes(), TYPES[path.suffix])

        def _api(self, route: str, query: dict[str, str]) -> None:
            name = query.get("source", "store")
            if not sources.has(name):
                return self._send(404, b"no such source\n", "text/plain; charset=utf-8")
            try:
                answer = sources.view(name) if route == "/api/view" else sources.detail(name, query.get("key", ""))
            except Exception as exc:  # the UI says it has no answer; the terminal says why
                sources.report(exc)
                return self._send(500, b"the observer failed\n", "text/plain; charset=utf-8")
            if route == "/api/view":
                etag = f'"{answer["token"]}"'
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304, headers={"ETag": etag})
                return self._json(answer, {"ETag": etag})
            detail = answer
            if detail is None:
                return self._send(404, b"no such run\n", "text/plain; charset=utf-8")
            self._json(detail)

    return Handler


def serve(sources: Sources, port: int = 0, root: Path = UI) -> ThreadingHTTPServer:
    """A bound, not yet running, server on 127.0.0.1 (``port`` 0 picks a free one)."""
    server = ThreadingHTTPServer((HOST, port), handler_for(sources, root))
    server.daemon_threads = True
    return server
