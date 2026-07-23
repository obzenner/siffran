#!/usr/bin/env python3
"""Atomic, locked JSON file-io — the ONE hardened writer shared by budget.py and
manifest.py (ADR-19).

Before this module each of those files carried its own copy of "take an exclusive OS
lock on a symlink-refusing 0600 path, write JSON to a fresh temp file, fsync, atomic
rename." That is a single piece of knowledge (it moves together under change — e.g. if
the locking strategy changes, both must change), so it lives in one place. The external
review explicitly asked the manifest to reuse budget's proven io rather than ship a
second implementation.

Loaded by sibling hooks via `spec_from_file_location`, so it uses no package imports and
sits next to its callers in the hooks dir.
"""
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX only
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    _HAVE_FCNTL = False

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


@contextmanager
def lock(path: Path):
    """Exclusive OS lock on a per-target `.lock` file, opened O_NOFOLLOW (no symlink),
    mode 0600. Best-effort where fcntl is absent (Windows) — a logged degradation, not a
    crash. Shared by every writer so read-modify-write cycles serialise against each other.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC, 0o600)
    try:
        if _HAVE_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if _HAVE_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_write_json(path: Path, data: dict) -> None:
    """Write `data` as JSON via a fresh unique temp file in the target dir, then rename.

    tempfile.mkstemp (O_CREAT|O_EXCL|O_RDWR, mode 0600) means we never follow a pre-planted
    symlink at a predictable `.tmp` path and never clobber an attacker-chosen target; the
    unique name also means uncoordinated writers don't share one temp path. fsync + atomic
    rename give crash-consistency on POSIX.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp.", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)  # atomic on POSIX
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
