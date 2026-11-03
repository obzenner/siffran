"""Whether an Empirica observer serves this machine's run store now, and where (PR 16, A10/A11).

The forms a host shows a human (a configuration, an amended configuration, an evidence batch, a
terminal consent) point at the observer UI beside the bound document, so the human decides where
to read the details: in the form, in the UI, or in the file. The link is **presentation only** —
the document's digest stays what every presentation and decision binds; the core never sees a
URL, and nothing here writes inside the run store's ``projects/`` tree.

``observe serve`` publishes one file — ``<home>/observer/serve.json`` — once it is listening, and
 removes it on a clean exit, so a host form never has to guess which port names the UI:

* the file is written atomically (a fresh temp file, renamed into place) with mode ``0600``, inside
  ``<home>/observer/`` — created ``0700``, its parent never a symlink — and its publication and
  cleanup are serialized with a lock file in that directory, so two servers cannot interleave;
* the fields are ``{schema, port, pid, instance, home_identity, capabilities}`` — ``instance`` a
  random token per server *process*, ``home_identity`` the identity of the resolved home alone
  (independent of ``--repo``, the same value ``/api/about`` answers), ``capabilities`` what the
  server's contract supports (``"hitl/1"``);
* the file carries **no URL**: :func:`observer_url` builds ``http://127.0.0.1:<port>`` itself and
  never trusts an address out of any file;
* before binding a new port, ``observe serve`` checks the advertised live server and reuses it
  when one is live for the same home, instead of falling back to an ephemeral port.

:func:`observer_url` is the hardened reader the hosts call — no file name trusted, one bounded
probe, and **``None``, never an exception, for any failure**: the file is opened no-follow and
non-blocking, must be a regular file owned by the current user with mode ``0600`` (its directory
``0700``, owned likewise), is read at most 4 KiB, and its schema, positive ``pid`` and ``port``
(1024–65535) are validated before anything on the network is tried. The probe is one ``GET
/api/about`` on 127.0.0.1 with proxies disabled, no redirects, at most 16 KiB read and **0.5 s for
the whole probe**; the answer must name this store's ``home_identity``, the process's ``instance``
and the ``"hitl/1"`` capability. Anything else — no file, a stale pid, no listener, a different
store's observer, an observer too old to answer ``capabilities`` (ADR-0072's amendment: a running
old observer must be restarted after upgrade) — is ``None``, and the form keeps its honest
fallback lines (A11) with the document's exact path.

The helpers here never start a process, never write inside the run store (the one write is the
observer's own advertise file, under ``<home>/observer/``, never under ``projects/``), and are
safe for the talk-down wire both sides of the forms share: one probe per form presentation, not
per item.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from core.evaluation import digest

#: The schema marker of the advertise file and of nothing else.
SERVE_SCHEMA = "empirica.observer-serve/1"
#: What a server that serves each decision's binding (PR 15) understands.
CAPABILITIES = ("hitl/1",)
#: The advertise file and its directory, and the lock that serializes publication and cleanup.
SERVE_DIR = "observer"
SERVE_FILE = "serve.json"
SERVE_LOCK = "serve.lock"
#: The file is read capped (A10: at most 4 KiB), like the probe's answer (16 KiB) — both bounds
#: are for a hostile writer: a huge file can neither slow a read nor widen memory.
SERVE_CAP = 4096
ABOUT_CAP = 16 * 1024
#: The whole ``/api/about`` probe — connect, answer and read — has this one budget (A10: 0.5 s),
#: worst case: a listener that accepts but does not answer, or answers too slowly, is ``None``.
PROBE_SECONDS = 0.5
#: Ports an advertised observer may name on the loopback interface (the OS-reserved range is
#: not a place a user server publishes).
_PORT_RANGE = (1024, 65535)
#: The advertise record's exact member set: a file with any other member (a url, a path, a name
#: some future writer adds) is a different file than the reader's contract, and is refused —
#: the URL is built from the port, never read out of a file.
_SERVE_MEMBERS = frozenset({"schema", "port", "pid", "instance", "home_identity", "capabilities"})
_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_PID = re.compile(r"\A[0-9]{1,10}\Z")
#: The exact digest prefix and short form length the repository's identifiers already use.
_SHA = "sha256:"
_SHORT_BODY = 12


def home_identity(home: Path | str) -> str:
    """The identity of a resolved home alone — independent of ``--repo`` (A10), the same value
    ``/api/about`` answers as ``home_identity`` and that names this store in the advertise file.
    The path is made absolute the sidecar store's way — expanduser, never symlink-resolved, so
    both sides (the writer behind ``observe serve``, the reader in a host form process) compute
    one value from one ``EMPIRICA_HOME`` however the host spelled it."""
    base = Path(home).expanduser().absolute()
    return digest({"home": str(base)})


def instance_token() -> str:
    """A random per-process token (A10): the ``instance`` of one server process, quoted in its
    advertise file and in every ``/api/about`` it answers, so a probe tells one live process
    from the file a previous one left, and two processes can never answer for each other."""
    return secrets.token_hex(16)


def compact_run_id(run_id: object) -> str | None:
    """A run's compact id — the observer UI's ``?run=`` value — computed next to the bridge from
    the ``er2:`` handle alone and one shared rule (A13: never by importing the observer's whole
    index into a host runtime). The id is ``application.observer_index.short_id`` over the run's
    storage key ``<project_id>/<run_id>/<generation>`` — a function of the key alone, so it never
    changes when other runs are added, and it never names or derives the opaque handle itself.
    ``None`` when the handle is not a run's."""
    from application.location import decode_handle  # lazy: only a form presentation pays the codec
    key = decode_handle(run_id)
    if key is None:
        return None
    return digest_material(f"{key.project_id}/{key.run_id}/{key.generation}")


def digest_material(text: str) -> str:
    """The compact id of a run key's materialization — the first hex digits of the digest of the
    key, exactly ``application.observer_index.short_id`` (which stays the observer's own owner of
    the rule; this is the host-runtime twin pinned to it by test)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_SHORT_BODY]


def observer_url(home: Path | str | None) -> str | None:
    """Where this machine's observer is serving ``home`` right now — ``http://127.0.0.1:<port>``
    — or ``None`` (never an exception, never a started process). One hardened read of the
    advertise file and one bounded probe (see the module docstring); the URL is built here from
    the file's port, never read out of the file. The caller links to a run with the query the
    observer UI's route owns (``/app/?run=<compact id>&decision=<12 hex>``)."""
    if home is None:
        return None
    try:
        return _observer_url(home)
    except Exception:  # noqa: BLE001 - a form presentation must never die on discovery
        return None


def document_details(home: Path | str | None, run_id: object,
                     document: Mapping[str, Any]) -> dict[str, Any]:
    """One form presentation's Details (A10/A11): what a host form lines beside the bound
    document — a live observer's full link to the run's decisions, the run's compact id, and
    the digest's 12-hex short form — computed **once per presentation, not per item**. The
    members are presentation only: the core never sees them (the decision envelope keeps nothing
    but the digest), and a host that does not find a live observer keeps its honest fallback
    lines with the document's exact absolute path. Never raises; any failure is the fallback.

    ``{"url": "<http://127.0.0.1:PORT/app/?run=…&decision=…>" | None,
       "run12": "<12 hex>" | None}``
    """
    full = document.get("digest") if isinstance(document, Mapping) else None
    try:
        compact = compact_run_id(run_id)
        base = observer_url(home)
        if (compact is None or base is None or not isinstance(full, str)
                or not full.startswith(_SHA)):
            return {"url": None, "run12": compact}
        short = full[len(_SHA):len(_SHA) + _SHORT_BODY]
        return {"url": f"{base}/app/?run={compact}&decision={short}", "run12": compact}
    except Exception:  # noqa: BLE001 - as ``observer_url``'s contract, extended to the fields
        return {"url": None, "run12": None}


def _serve_path(home: Path) -> Path:
    return Path(home).expanduser().absolute() / SERVE_DIR


# ---- the one write: publication and cleanup -----------------------------------------------


@contextmanager
def _lock_in(directory: Path):
    """The context manager that serializes publication and cleanup with a lock file in the
    advertise directory (A10): one process holds the lock while its file changes, so a second
    server never reads a half-written advertise or interleaves its own. The lock file is a
    plain ``0600`` file created once; its old contents are nobody's. A directory that cannot be
    made or locked is a publication failure — the server still serves; a form then falls back."""
    lock = directory / SERVE_LOCK
    handle = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


def _prepare_dir(home: Path) -> Path | None:
    """The advertise directory, created 0700 and owned by this user, or ``None`` when it cannot
    be (a symlink there, a hostile parent, a filesystem error): nothing is published, and the
    reader refuses the same directory, so writer and reader agree on what a publication means."""
    directory = _serve_path(home)
    try:
        try:
            info = directory.lstat()
        except FileNotFoundError:
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)  # umask may have narrowed the create; the mode is the rule
            info = directory.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()):
            return None
        mode = stat.S_IMODE(info.st_mode)
        if mode != 0o700:
            os.chmod(directory, 0o700)  # an older, wider directory is ours to narrow
            if stat.S_IMODE(directory.lstat().st_mode) != 0o700:
                return None
    except OSError:
        return None
    return directory


def _owner_mode_ok(info: os.stat_result, path: Path, mode: int) -> bool:
    return (not stat.S_ISLNK(info.st_mode) and stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == mode)


def publish_serve(home: Path | str, *, port: int, pid: int, instance: str,
                  capabilities: tuple[str, ...] = CAPABILITIES) -> Path | None:
    """Write the advertise file for a listening server (A10). One atomic rename of mode-``0600``
    bytes; the lock serializes the write against every other publication or cleanup; the
    directory is created ``0700`` on demand. The answer is the file's path, or ``None`` when the
    publication failed — which never stops the server: a form falls back to the document's
    path. The file replaces an older advertise of a *dead* previous server (a crashed one
    cannot withdraw); a live one was already asked to be reused before this process bound its
    own port (``observe serve``'s reuse path)."""
    directory = _prepare_dir(home)
    if directory is None:
        return None
    record = {"schema": SERVE_SCHEMA, "port": int(port), "pid": int(pid),
              "instance": instance, "home_identity": home_identity(home),
              "capabilities": list(capabilities)}
    payload = json.dumps(record, ensure_ascii=False) + "\n"
    path = directory / SERVE_FILE
    try:
        handle, temp = tempfile.mkstemp(prefix=".serve.", dir=directory)
        try:
            os.write(handle, payload.encode("utf-8"))
            os.fchmod(handle, 0o600)
            os.fsync(handle)
        finally:
            os.close(handle)
        with _lock_in(directory):
            os.replace(temp, path)
        _fsync_dir(directory)
        return path
    except OSError:
        try:
            os.unlink(temp)  # noqa: F821 - a failed rename leaves the temp behind
        except OSError:
            pass
        return None


def _fsync_dir(directory: Path) -> None:
    try:
        handle = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
    except OSError:  # a directory that cannot be fsynced (some network filesystems) is not fatal
        pass


def withdraw_serve(home: Path | str, pid: int) -> None:
    """Remove the advertise file on a clean exit, but only when it still names this own pid
    (A10): a file another live process published is not ours to withdraw, and a crashed
    server's stale file is simply ignored by every reader (its pid is dead). Serialized with
    the same lock; never raises."""
    try:
        directory = _prepare_dir(home)
        if directory is None:
            return
        path = directory / SERVE_FILE
        with _lock_in(directory):
            record = _read_serve(home)
            if record is None or record.get("pid") != int(pid):
                return
            os.unlink(path)
        _fsync_dir(directory)
    except Exception:  # noqa: BLE001 - cleanup never fails a clean exit
        pass


# ---- the hardened read and the one probe --------------------------------------------------


def _read_serve(home: Path | str, *, probed: bool = True) -> dict[str, Any] | None:
    """The advertise file's record — a bounded, no-follow, non-blocking read of a regular file
    this user owns at mode ``0600`` (its directory ``0700``, owned likewise; A10), parsed and
    schema-validated, at most ``SERVE_CAP`` bytes. ``None`` for any refusal: a hostile writer
    gets no reader. The ``pid`` is checked alive when the reader is about to probe; the caller
    that only compares a pid reads it as data (a dead pid is still *this process's* record)."""
    directory = _serve_path(home)
    try:
        info = directory.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            return None
        path = directory / SERVE_FILE
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        info = os.fstat(handle)
        if not _owner_mode_ok(info, path, 0o600):
            return None
        if info.st_size > SERVE_CAP:
            return None
        data = b""
        while len(data) <= SERVE_CAP:
            try:
                chunk = os.read(handle, SERVE_CAP + 1)
            except BlockingIOError:
                return None
            if not chunk:
                break
            data += chunk
        if len(data) > SERVE_CAP or not data:
            return None
        record = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    finally:
        os.close(handle)
    if not isinstance(record, dict) or record.get("schema") != SERVE_SCHEMA:
        return None
    if set(record) != _SERVE_MEMBERS:
        return None
    pid, port = record.get("pid"), record.get("port")
    if type(pid) is not int or pid <= 0:
        return None
    if type(port) is not int or not _PORT_RANGE[0] <= port <= _PORT_RANGE[1]:
        return None
    if not (isinstance(record.get("instance"), str) and _TOKEN.fullmatch(record["instance"])):
        return None
    identity = record.get("home_identity")
    if (not isinstance(identity, str) or not identity
            or len(identity) > 256
            or any(character.isspace() or ord(character) < 32 for character in identity)):
        return None
    if (not isinstance(record.get("capabilities"), list)
            or not all(isinstance(value, str) for value in record["capabilities"])):
        return None
    return record


def _no_redirect():
    class _Refused(urllib.request.HTTPRedirectHandler):  # a redirect is refused, not followed
        def redirect_request(self, *args, **kwargs):  # noqa: ARG002 - always nothing
            return None
    return _Refused()


def _about(port: int, *, seconds: float = PROBE_SECONDS) -> dict[str, Any] | None:
    """One bounded ``GET /api/about`` on 127.0.0.1 (A10): proxies disabled (a machine-wide proxy
    cannot take the loopback), no redirects, at most ``ABOUT_CAP`` bytes and ``seconds`` for the
    whole probe. ``None`` for any failure — refused, slow, oversized, not an observer's answer."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _no_redirect())
    deadline = time.monotonic() + seconds
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/about", timeout=seconds) as reply:
            if reply.status != 200:
                return None
            data = b""
            while len(data) <= ABOUT_CAP:
                if time.monotonic() > deadline:
                    return None
                chunk = reply.read1(ABOUT_CAP)
                if not chunk:
                    break
                data += chunk
            if len(data) > ABOUT_CAP:
                return None
            answer = json.loads(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 - refused, reset, unspeakable: one answer, ``None``
        return None
    return answer if isinstance(answer, dict) else None


def _observer_url(home: Path | str) -> str | None:
    record = _read_serve(home)
    if record is None:
        return None
    if record.get("home_identity") != home_identity(home):
        return None
    pid = record["pid"]
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return None
    answer = _about(record["port"])
    if answer is None:
        return None
    if (answer.get("observer") != "empirica-observer"
            or answer.get("home_identity") != record["home_identity"]
            or answer.get("instance") != record["instance"]
            or "hitl/1" not in (answer.get("capabilities") or [])):
        return None
    return f"http://127.0.0.1:{record['port']}"
