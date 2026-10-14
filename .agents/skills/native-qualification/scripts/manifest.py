#!/usr/bin/env python3
"""Print a SHA-256 manifest (`<digest>  <path>`) of the tracked and untracked candidate bytes. Read-only; stages nothing."""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def paths() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, check=True).stdout.decode("utf-8", "surrogateescape")
    return sorted(p for p in out.split("\0") if p)


def main() -> int:
    for rel in paths():
        path = ROOT / rel
        if path.is_symlink():
            digest = hashlib.sha256(("symlink:" + str(path.readlink())).encode()).hexdigest()
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            continue  # deleted in the working tree
        print(f"{digest}  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
