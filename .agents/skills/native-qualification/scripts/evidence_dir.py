#!/usr/bin/env python3
"""Create a fresh evidence directory outside the checkout, exclusively. An existing path is refused, never reused."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def create(target: str) -> Path:
    path = Path(target).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise SystemExit(f"refused: {path} is inside the source checkout")
    path.mkdir(parents=True, exist_ok=False)  # FileExistsError on a leftover: reconcile it, do not delete it
    (path / "state").mkdir()
    return path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: evidence_dir.py <new-directory>")
    try:
        print(create(sys.argv[1]))
    except FileExistsError:
        raise SystemExit("refused: the directory exists; reconcile it instead of reusing or deleting it") from None
    return 0


if __name__ == "__main__":
    sys.exit(main())
