#!/usr/bin/env python3
"""Fail when Empirica's shipped runtime contracts differ from their sources."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: One row per vendored runtime contract directory: (source, vendor, shipped file names).
CONTRACT_COPIES = (
    (ROOT / "contracts/empirica/v2",
     ROOT / "plugins/empirica/vendor/contracts/empirica/v2",
     ("host-profiles.json", "host-profiles.schema.json", "public-contract.json", "public-contract.schema.json",
      "public-tools.json", "request.schema.json", "response.schema.json", "state.schema.json")),
    # The digest-pinned probe image (Empirica 7.2 §4.3) is read at runtime by
    # adapters/sandbox/probe_image.py; its digest is part of the implementation manifest.
    # The /3 run-scoped qualification record shape (§4.5) is the definition home for
    # adapters/sandbox/run_qualification.py.
    (ROOT / "contracts/empirica-docker-spike/v1",
     ROOT / "plugins/empirica/vendor/contracts/empirica-docker-spike/v1",
     ("support-contract.json", "qualification-record.schema.json")),
)


def problems(copies=CONTRACT_COPIES) -> list[str]:
    found = []
    for source, vendor, names in copies:
        for name in sorted(names):
            if not (source / name).is_file():
                found.append(f"missing source {source / name}")
            elif not (vendor / name).is_file() or (source / name).read_bytes() != (vendor / name).read_bytes():
                found.append(f"differing/missing {vendor / name}")
        if vendor.is_dir():
            extra = sorted(
                path.relative_to(vendor).as_posix()
                for path in vendor.rglob("*") if path.is_file()
                if path.relative_to(vendor).as_posix() not in names)
            found += [f"extra {vendor / name}" for name in extra]
    return found


def count() -> int:
    return sum(len(names) for _, _, names in CONTRACT_COPIES)


def main() -> None:
    found = problems()
    if found:
        raise SystemExit(f"contract vendor mismatch: {found}")
    print(f"ok: {count()} byte-identical Empirica runtime contract vendor files")


if __name__ == "__main__":
    main()
