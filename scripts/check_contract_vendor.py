#!/usr/bin/env python3
"""Fail when Empirica's shipped runtime contracts differ from their source."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/empirica/v2"
VENDOR = ROOT / "plugins/empirica/vendor/contracts/empirica/v2"
NAMES = {
    "host-profiles.json",
    "public-contract.json",
    "public-contract.schema.json",
    "public-tools.json",
    "request.schema.json",
    "response.schema.json",
    "state.schema.json",
}
def main() -> None:
    differing = [
        name for name in sorted(NAMES)
        if not (VENDOR / name).is_file()
        or (SOURCE / name).read_bytes() != (VENDOR / name).read_bytes()
    ]
    extra = sorted(
        path.relative_to(VENDOR).as_posix()
        for path in VENDOR.rglob("*") if path.is_file()
        if path.relative_to(VENDOR).as_posix() not in NAMES
    ) if VENDOR.is_dir() else []
    if differing or extra:
        raise SystemExit(
            f"contract vendor mismatch: differing/missing={differing}, extra={extra}")
    print(f"ok: {len(NAMES)} byte-identical Empirica runtime contract vendor files")


if __name__ == "__main__":
    main()
