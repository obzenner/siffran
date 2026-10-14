"""``observe serve``: the observer read model and the UI over HTTP, for this machine only.

    GET /api/summary?since=EPOCH&tz=ZONE     contract v1: facets, aggregates (within ``since``), coverage, the runs
                                             that need a human, the universe marks and the calendar by civil day in
                                             ``tz`` (IANA, default UTC); ETag = its token, 304 when unchanged; gzip
                                             when the client accepts it
    GET /api/runs?FACET=VALUE&cursor=&limit= contract v1: one-line rows (at most 200), filtered by standing, ask,
                                             project, host, mode, audit or since, paged by ``cursor``
    GET /api/search?q=TEXT&cursor=&limit=    contract v1: the rows whose goal, claim text, project or session holds
                                             every word
    GET /api/run?key=KEY                     contract v1: one run's sheet: its detail, its row and every attempt's row
                                             (``key`` may be a run's short ``id``; one several runs share is a 409);
                                             ETag = its versions, 304 when unchanged
    GET /api/about                           which store this server observes (a digest), so a second
                                             ``serve`` of the same store on the same port reuses it, and what
                                             each source is (``sources``: store, generated or fixtures)
    GET /app/, /elements/, ...               the UI's files (an allowlist under plugins/empirica/ui)

Security: bound to 127.0.0.1 only. Only GET and HEAD are served. A request whose ``Host`` is not one of this
server's three names with its own port (``127.0.0.1``, ``localhost``, ``empirica.localhost``) is refused, which
defeats DNS rebinding: browsers resolve ``*.localhost`` to the loopback address without asking DNS, so the name the
CLI prints, ``empirica.localhost:<port>``, tells the tab apart from other local servers, and no other name is taken. No CORS headers are sent, so
another origin cannot read a response. A Content-Security-Policy allows only this origin. Nothing is
written: the observer reads, and the server only reads files from the allowlist.

``source=demo`` serves the contract fixtures vendored into the plugin
(``vendor/contracts/empirica-ui-view/v1/fixtures``): real runs the plugin produced, for trying the
UI and for its checks.
"""
from __future__ import annotations

import gzip
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from adapters.observer import civil
from application import observer_index as _index
from application.observer import CONTRACT, Observer

#: What ``/api/about`` names this server, so a client knows it is an Empirica observer.
ABOUT = "empirica-observer"
PLUGIN = Path(__file__).resolve().parents[2]
UI = PLUGIN / "ui"
DEMO = PLUGIN / "vendor/contracts/empirica-ui-view/v1/fixtures"
#: How long one request may spend indexing before it answers with what it has (coverage says what is pending).
INDEX_BUDGET = 0.25
#: The query parameters each contract endpoint takes besides ``source``.
PARAMS = {"/api/summary": {"since", "tz"}, "/api/runs": {"cursor", "limit", "since", *("standing", "ask", "project", "host",
                                                                                   "mode", "audit", "flag")},
             "/api/search": {"q", "cursor", "limit"}}
HOST = "127.0.0.1"
#: The name the CLI prints and opens; the host check (`_host_ok`) also accepts the two loopback names, with the server's own port.
NAME = "empirica.localhost"
#: What the UI may load: top-level folders and files under ``UI``, by extension.
SERVED_DIRS = ("app", "elements", "dist", "vendor", "copy")
SERVED_FILES = ("states.json", "catalogue.json", "icons.json", "enums.json")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml", ".woff2": "font/woff2"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; font-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class Demo:
    """The vendored fixtures, read once: the summary, rows and sheets of a store the real service built, paged and searched
    by the same functions the live index uses (``application.observer_index``)."""

    def __init__(self, folder: Path = DEMO) -> None:
        self._summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        self._rows = json.loads((folder / "rows.json").read_text(encoding="utf-8"))
        self._sheets = json.loads((folder / "runs.json").read_text(encoding="utf-8"))
        self._labels = {p["id"]: p["label"] for p in self._summary["projects"]}
        self._facts = {key: _index.facts(sheet) for key, sheet in self._sheets.items()}

    def summary(self, since: float | None = None, tz: str = "UTC", **_: Any) -> dict[str, Any]:
        """The fixtures' summary (the demo is fixed: its window is the fixtures' own), its calendar in ``tz``."""
        if tz == self._summary["calendar"]["tz"]:
            return self._summary
        ids = [p["id"] for p in self._summary["projects"]]
        return {**self._summary, "calendar": _index.calendar(self._rows, ids, tz, civil.zone(tz)),
                "token": _index.token([self._summary["token"], tz])}

    def runs(self, filters: dict[str, str], cursor: str | None = None, limit: int = _index.PAGE, **_: Any) -> dict:
        return {"contract": CONTRACT, "pending": 0,
                **_index.page(self._rows, self._labels, lambda r: _index.matches(r, filters, self._facts.get(r["key"])),
                              cursor, limit, self._summary["token"])}

    def search(self, query: str, cursor: str | None = None, limit: int = _index.PAGE, **_: Any) -> dict:
        wanted = _index.words(query)
        keep = lambda r: all(w in _index.haystack(r, self._labels[r["project"]]) for w in wanted)  # noqa: E731
        return {"contract": CONTRACT, "pending": 0, "query": query,
                "unsearched": sum(1 for r in self._rows if r["claims"] is None and not keep(r)) if wanted else 0,
                **_index.page(self._rows, self._labels, keep, cursor, limit, self._summary["token"])}

    def sheet(self, key: str) -> dict[str, Any] | None:
        return _index.resolve(self._sheets.items(), key)


class Sources:
    """The two data sources, each observed under one lock (the observer's cache is not shared
    across threads)."""

    def __init__(self, store: Observer | None, demo: Demo | None = None, identity: str = "demo",
                 store_kind: str = "store") -> None:
        self._sources = {"store": store, "demo": demo or Demo()}
        self.identity = identity  # which store is observed (cli.identity): what /api/about answers
        #: What each source serves, so the page names it truly: ``store`` an Empirica store, ``generated`` a store the
        #: real service built for a demo (tests/v2/serve_generated.py), ``fixtures`` the vendored contract fixtures.
        self._kinds = {"store": store_kind, "demo": "fixtures"}
        self._lock = threading.Lock()
        self._reported: set[str] = set()
        self._encoded: dict[tuple[str, bool], bytes] = {}

    def about(self) -> dict[str, Any]:
        """``/api/about``: that this is an Empirica observer, which store it observes, and what each source it serves is."""
        return {"observer": ABOUT, "identity": self.identity,
                "sources": {name: self._kinds[name] for name in self.names()}}

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, source in self._sources.items() if source is not None)

    def has(self, name: str) -> bool:
        return self._sources.get(name) is not None

    def call(self, name: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """A contract read (``summary``, ``runs``, ``search``, ``sheet``) of one source: under the lock, unless
        the source is indexed in the background (it then answers from its last published view, which no reader
        changes, so requests never wait on each other or on the indexer)."""
        source = self._sources[name]
        if getattr(source, "indexed", False):
            return getattr(source, method)(*args, **kwargs)
        with self._lock:
            return getattr(source, method)(*args, **kwargs)

    def prepare(self, name: str = "store") -> None:
        """Encode a background-indexed source's newest default summary both ways ahead of any request for it."""
        source = self._sources.get(name)
        if source is not None and getattr(source, "indexed", False):
            summary = source.summary()
            for compressed in (True, False):
                self.encoded(summary, compressed, name)

    def encoded(self, answer: dict[str, Any], compressed: bool, name: str = "store") -> bytes:
        """A summary's bytes on the wire, encoded once per source, token and encoding (two sources may give the same
        token: the demo's fixtures were taken from a store like the live one)."""
        key = (name, answer["token"], compressed)
        with self._lock:
            found = self._encoded.get(key)
        if found is None:
            found = json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if compressed:
                found = gzip.compress(found, compresslevel=6, mtime=0)
            with self._lock:
                if len(self._encoded) > 8:
                    self._encoded.clear()
                self._encoded[key] = found
        return found

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
            try:
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page went away (a navigation cancels its requests); nothing is owed to it

        def _json(self, value: Any, headers: dict[str, str] | None = None) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._send(200, body, TYPES[".json"], headers)

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host") in {f"{HOST}:{port}", f"localhost:{port}", f"{NAME}:{port}"}

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
            if url.path in PARAMS or url.path == "/api/run":
                return self._api(url.path, query)
            if url.path == "/api/about":
                return self._json(sources.about())
            path = static_path(root, url.path)
            if path is None:
                return self._send(404, b"not found\n", "text/plain; charset=utf-8")
            self._send(200, path.read_bytes(), TYPES[path.suffix])

        def _api(self, route: str, query: dict[str, str]) -> None:
            name = query.pop("source", "store")
            if not sources.has(name):
                return self._send(404, b"no such source\n", "text/plain; charset=utf-8")
            try:
                args = _call(route, query)
            except ValueError as exc:
                return self._send(400, f"{exc}\n".encode("utf-8"), "text/plain; charset=utf-8")
            try:
                method, positional, keywords = args
                answer = sources.call(name, method, *positional, **keywords)
            except _index.StaleCursor as stale:  # the view changed since the cursor was given: the walk restarts
                return self._send(409, json.dumps({"error": "stale_cursor", "token": stale.token}).encode("utf-8"),
                                  "application/json; charset=utf-8")
            except _index.AmbiguousId as clash:  # a short id several runs share: never one of them
                return self._send(409, json.dumps({"error": "ambiguous_id", "keys": list(clash.keys)}).encode("utf-8"),
                                  "application/json; charset=utf-8")
            except Exception as exc:  # the UI says it has no answer; the terminal says why
                sources.report(exc)
                return self._send(500, b"the observer failed\n", "text/plain; charset=utf-8")
            if answer is None:
                return self._send(404, b"no such run\n", "text/plain; charset=utf-8")
            headers = {}
            if route in ("/api/summary", "/api/run"):
                etag = f'"{answer["token"] if route == "/api/summary" else _index.sheet_tag(answer)}"'
                headers["ETag"] = etag
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304, headers=headers)
            compressed = "gzip" in (self.headers.get("Accept-Encoding") or "")
            if route == "/api/summary":
                body = sources.encoded(answer, compressed, name)
            else:
                body = json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if compressed:
                    body = gzip.compress(body, compresslevel=6, mtime=0)
            if compressed:
                headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
            self._send(200, body, TYPES[".json"], headers)

    return Handler


def _call(route: str, query: dict[str, str]) -> tuple:
    """The observer call a contract request names, its parameters checked (``ValueError`` names what is wrong)."""
    if route == "/api/run":
        if not query.get("key"):
            raise ValueError("key is required")
        return "sheet", (query["key"],), {}
    unknown = set(query) - PARAMS[route]
    if unknown:
        raise ValueError(f"unknown parameters: {', '.join(sorted(unknown))}")
    if route == "/api/summary":
        since = query.get("since")
        since = since if since else None
        return "summary", (_index.since_of(since), civil.tz_of(query.get("tz"))), {"budget": INDEX_BUDGET}
    limit = _index.limit_of(query.pop("limit", None))
    cursor = query.pop("cursor", None) or None
    _index.cursor_after(cursor)
    if route == "/api/search":
        return "search", (query.get("q", ""), cursor, limit), {"budget": INDEX_BUDGET}
    if "since" in query:
        _index.since_of(query["since"])
    for facet in ("standing", "ask", "flag"):
        if facet in query:
            _index.known([query[facet]], facet)
    return "runs", (query, cursor, limit), {"budget": INDEX_BUDGET}


def serve(sources: Sources, port: int = 0, root: Path = UI) -> ThreadingHTTPServer:
    """A bound, not yet running, server on 127.0.0.1 (``port`` 0 picks a free one)."""
    server = ThreadingHTTPServer((HOST, port), handler_for(sources, root))
    server.daemon_threads = True
    return server
