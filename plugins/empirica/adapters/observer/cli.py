"""``observe``: the run store as the UI read model (``contracts/empirica-ui-view/v1``). Read-only.

    python3 plugins/empirica/adapters/observer/cli.py [--home DIR] [--repo DIR ...] [--run KEY]
    python3 plugins/empirica/adapters/observer/cli.py serve [--home DIR] [--repo DIR ...] [--port N] [--open] [--demo]

Without a command it prints the store summary (or one run's sheet, with ``--run``) as JSON. With
``serve`` it serves that and the UI on 127.0.0.1 and prints the address; ``--demo`` serves only the
plugin's demo data and reads no store. When an observer of the same store already answers on the
port, ``serve`` says so (``already running``), opens that one with ``--open``, and exits.

``--home`` defaults to the plugin's store (``$EMPIRICA_HOME``, else ``~/.empirica-plugin``). Each
project's history is found in the repository recorded for it when its runs were created
(``adapters.state.locations``). ``--repo`` adds repositories for runs recorded before that, matched
by the plugin's identity; without one, the current directory's repository is used when there is one.

``serve`` (PR 16, A10, ``adapters.observer.discovery``) also publishes one small advertise file —
``<home>/observer/serve.json``, ``0600`` inside a ``0700`` directory — naming its port, pid, a
per-process instance token, the home's identity and its capabilities, and removes it on a clean
exit. That is the observer's **one write**, and it lives outside the run store's ``projects/``
tree: everything under ``projects/`` stays exactly as it was (the observer itself stays read-only).
A host form finds the UI through that file (``adapters.observer.discovery.observer_url``), probes
``/api/about`` once per presentation, and falls back to honest lines without a link when no live
server of this home answers. A second ``serve`` of the same home — on the preferred port or on any
port an advertise file names — says ``already running`` and reuses that server instead of binding
a new one; the demo (``--demo``, which reads no store) publishes nothing, so no form ever links to
fixture data.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# plugins/empirica/adapters/observer/cli.py -> plugins/empirica (so core/application/adapters import).
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from adapters.git.artifact_repo import toplevel  # noqa: E402
from adapters.observer import civil  # noqa: E402
from adapters.observer import discovery  # noqa: E402
from adapters.observer.source import StoreSource  # noqa: E402
from adapters.state.repository import empirica_home  # noqa: E402
from application.observer import Observer  # noqa: E402
from core.evaluation import digest  # noqa: E402

#: The address ``serve`` tries first, so a bookmark keeps working; a free one when it is taken.
DEFAULT_PORT = 8765


def source_from(home: str | None, repos: list[str]) -> StoreSource:
    if not repos:
        here = toplevel(Path.cwd())
        repos = [str(here)] if here is not None else []
    return StoreSource(Path(home).expanduser() if home else empirica_home(), [Path(r) for r in repos])


def _store_options(p: argparse.ArgumentParser, defaults: bool) -> None:
    # The subcommand's copies default to SUPPRESS so ``--home X serve`` keeps X.
    keep = {} if defaults else {"default": argparse.SUPPRESS}
    p.add_argument("--home", help="the run store (default: $EMPIRICA_HOME or ~/.empirica-plugin)", **keep)
    p.add_argument("--repo", action="append", help="a repository holding run history",
                   **({"default": []} if defaults else keep))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="observe", description=__doc__.splitlines()[0])
    _store_options(p, defaults=True)
    p.add_argument("--run", help="print this run's sheet (its key, or its short id)")
    sub = p.add_subparsers(dest="command")
    s = sub.add_parser("serve", help="serve the read model and the UI on 127.0.0.1")
    _store_options(s, defaults=False)
    s.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to try first (default {DEFAULT_PORT})")
    s.add_argument("--open", action="store_true", help="open the UI in the default browser")
    s.add_argument("--demo", action="store_true", help="serve only the demo data (no store is read)")
    s.add_argument("--exit-with-stdin", action="store_true",
                   help="stop when standard input closes (a launcher's pipe: the server ends with its parent)")
    return p


ABOUT_LIMIT = 4096  # bytes: an /api/about answer is a few short strings
ABOUT_SECONDS = 1.0


def identity(source: StoreSource | None, repos: list[str]) -> str:
    """Which store a server observes, as a digest: its home and the repositories asked for explicitly
    (``--repo``), or ``demo``. The current directory's repository, the default, is not part of it:
    the recorded locations already find every project's history, so two sessions in different
    repositories share one observer of the same store.

    It identifies, it does not authenticate: the inputs are predictable (by default the home alone),
    so on a machine shared with another user, that user could answer on the port first. The plugin
    assumes a single-user machine, as its store does."""
    if source is None:
        return "demo"
    tops = sorted(str(toplevel(Path(r)) or Path(r).resolve()) for r in repos)
    return digest({"home": str(source.home.resolve()), "repos": tops})


def running(port: int, want: str) -> bool:
    """Whether an Empirica observer for the same store already answers on ``port``: asked once, with
    the whole answer bounded in size (``ABOUT_LIMIT``) and time (``ABOUT_SECONDS``)."""
    from adapters.observer.server import ABOUT
    deadline = time.monotonic() + ABOUT_SECONDS
    body = b""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/about", timeout=ABOUT_SECONDS) as reply:
            while len(body) <= ABOUT_LIMIT and time.monotonic() < deadline:
                chunk = reply.read1(ABOUT_LIMIT)
                if not chunk:
                    break
                body += chunk
            else:
                return False  # too long, or too slow
        about = json.loads(body)
    except (OSError, ValueError):
        return False
    return isinstance(about, dict) and about.get("observer") == ABOUT and about.get("identity") == want


def announce(label: str, port: int, page: str, suffix: str = "") -> str:
    """Print where the observer is and return the address to open: ``empirica.localhost`` (a name of its own, which a
    browser resolves to this machine), then the loopback address for a browser that does not resolve ``*.localhost``.
    The first line is the one a launcher reads."""
    from adapters.observer.server import NAME
    url = f"http://{NAME}:{port}{page}"
    print(f"{label}: {url}{suffix}", flush=True)
    print(f"  If your browser does not resolve *.localhost: http://127.0.0.1:{port}{page}", flush=True)
    return url


def _advertised(home: Path | None) -> int | None:
    """The port a live, advertised observer of this very home serves on (A10) — the discovery
    reader probes the advertise file with the 0.5 s budget — or ``None``. ``observe serve`` asks
    this *before* it binds a port of its own, so a live server for the same home is reused (the
    ``already running`` path) instead of shadowed."""
    if home is None:
        return None
    url = discovery.observer_url(home)
    if url is None:
        return None
    return int(url.rsplit(":", 1)[1])


def _serve(args, source: StoreSource | None) -> int:
    from adapters.observer.indexer import Indexer
    from adapters.observer.server import Sources, serve
    store = identity(source, args.repo or [])
    home = source.home if source is not None else None
    page = "/app/" + ("?data=demo" if args.demo else "")
    reuse = None
    if running(args.port, store):  # the same store is already served on the preferred port: use that one
        reuse = args.port
    else:
        use = _advertised(home)  # A10: a live advertised server of this home, whatever port it took
        if use is not None:
            reuse = use
    if reuse is not None:
        url = announce("Empirica observer (already running)", reuse, page)
        if args.open:
            webbrowser.open(url)
        return 0
    # The store is indexed in the background (its clock bounds each slice), so a request never pays for indexing and a
    # cold start answers at once with what has been read so far.
    observer = None if source is None else Observer(source, clock=time.monotonic, zones=civil.zone)
    instance = discovery.instance_token()
    sources = Sources(observer, identity=store,
                      home_identity=discovery.home_identity(home) if home is not None else None,
                      instance=instance)
    if observer is not None:
        Indexer(observer, after=sources.prepare).start()
    try:
        server = serve(sources, args.port)
    except OSError:
        server = serve(sources, 0)
    url = announce("Empirica observer", server.server_address[1], page, "  (Ctrl-C to stop)")
    if args.open:
        webbrowser.open(url)
    # PR 16 (A10): advertise once listening — the observer's one write, outside ``projects/``
    # (a publication that fails never stops the server: a form then falls back to no link).
    if source is not None and discovery.publish_serve(
            home, port=server.server_address[1], pid=os.getpid(), instance=instance) is None:
        print("observe: this run store's observer could not advertise its UI link", file=sys.stderr)
    if args.exit_with_stdin:
        def watch() -> None:
            while sys.stdin.buffer.read(4096):
                pass
            server.shutdown()
        threading.Thread(target=watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if source is not None:  # the clean exit withdraws its own advertise (A10: only its own pid's)
            discovery.withdraw_serve(home, os.getpid())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # A closed pipe (``| head``) ends a one-shot JSON command quietly. A server must outlive a client that drops its
    # connection mid-write (a closed tab), so there the write fails with an error the handler already absorbs.
    if argv is None:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN if args.command == "serve" else signal.SIG_DFL)
    if args.command == "serve" and args.demo:
        return _serve(args, None)  # the demo reads no store, so none is opened
    try:
        source = source_from(args.home, args.repo)
    except ValueError as exc:
        print(f"observe: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        return _serve(args, source)
    observer = Observer(source, zones=civil.zone)
    observer.index()
    if args.run is not None:
        sheet = observer.sheet(args.run)
        if sheet is None:
            print(f"observe: no run {args.run}", file=sys.stderr)
            return 1
        json.dump(sheet, sys.stdout, ensure_ascii=False, indent=1)
    else:
        json.dump(observer.summary(), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
