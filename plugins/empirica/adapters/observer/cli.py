"""``observe``: the run store as the UI read model (``contracts/empirica-ui-view/v1``). Read-only.

    python3 plugins/empirica/adapters/observer/cli.py [--home DIR] [--repo DIR ...] [--run KEY]
    python3 plugins/empirica/adapters/observer/cli.py serve [--home DIR] [--repo DIR ...] [--port N] [--open] [--demo]

Without a command it prints the store summary (or one run's detail, with ``--run``) as JSON. With
``serve`` it serves that and the UI on 127.0.0.1 and prints the address; ``--demo`` serves only the
plugin's demo data and reads no store. When an observer of the same store already answers on the
port, ``serve`` says so (``already running``), opens that one with ``--open``, and exits.

``--home`` defaults to the plugin's store (``$EMPIRICA_HOME``, else ``~/.empirica-plugin``). Each
project's history is found in the repository recorded for it when its runs were created
(``adapters.state.locations``). ``--repo`` adds repositories for runs recorded before that, matched
by the plugin's identity; without one, the current directory's repository is used when there is one.
"""
from __future__ import annotations

import argparse
import json
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
    p.add_argument("--run", help="print this run's detail (a key from the summary)")
    sub = p.add_subparsers(dest="command")
    s = sub.add_parser("serve", help="serve the read model and the UI on 127.0.0.1")
    _store_options(s, defaults=False)
    s.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to try first (default {DEFAULT_PORT})")
    s.add_argument("--open", action="store_true", help="open the UI in the default browser")
    s.add_argument("--demo", action="store_true", help="serve only the demo data (no store is read)")
    s.add_argument("--exit-with-stdin", action="store_true",
                   help="stop when standard input closes (a launcher's pipe: the server ends with its parent)")
    return p


ABOUT_LIMIT = 4096  # bytes: an /api/about answer is two short strings
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


def _serve(args, source: StoreSource | None) -> int:
    from adapters.observer.server import Sources, serve
    store = identity(source, args.repo or [])
    page = "/app/" + ("?data=demo" if args.demo else "")
    if running(args.port, store):  # the same store is already served: use that one
        url = f"http://127.0.0.1:{args.port}{page}"
        print(f"Empirica observer (already running): {url}", flush=True)
        if args.open:
            webbrowser.open(url)
        return 0
    sources = Sources(None if source is None else Observer(source), identity=store)
    try:
        server = serve(sources, args.port)
    except OSError:
        server = serve(sources, 0)
    url = f"http://127.0.0.1:{server.server_address[1]}{page}"
    print(f"Empirica observer: {url}  (Ctrl-C to stop)", flush=True)
    if args.open:
        webbrowser.open(url)
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
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:  # as a command: a closed pipe (``| head``) ends quietly
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    args = parser().parse_args(argv)
    if args.command == "serve" and args.demo:
        return _serve(args, None)  # the demo reads no store, so none is opened
    try:
        source = source_from(args.home, args.repo)
    except ValueError as exc:
        print(f"observe: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        return _serve(args, source)
    view, details = Observer(source).observe()
    if args.run is not None:
        if args.run not in details:
            print(f"observe: no run {args.run}", file=sys.stderr)
            return 1
        json.dump(details[args.run], sys.stdout, ensure_ascii=False, indent=1)
    else:
        json.dump(view, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
