#!/usr/bin/env python3
"""Regenerate Empirica's shipped runtime contracts from the repository SSOT.

Deletion compares repository-relative vendor paths, never bare file names: a nested file
with a shipped basename (an old `…/old/support-contract.json`) is an extra byte the shipped
copy must not carry, and `make vendor-contracts` must be able to repair that drift
(check_contract_vendor rejects the same relative path).
"""
from __future__ import annotations

import check_contract_vendor


def sync(copies) -> int:
    """Refresh every vendored directory in `copies`; returns the number of shipped files."""
    shipped = 0
    for source, vendor, names in copies:
        vendor.mkdir(parents=True, exist_ok=True)
        for path in sorted(vendor.rglob("*"), reverse=True):
            if path.is_file() and path.relative_to(vendor).as_posix() not in names:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        for name in sorted(names):
            (vendor / name).write_bytes((source / name).read_bytes())
        shipped += len(names)
    return shipped


def main() -> None:
    print(f"synced {sync(check_contract_vendor.CONTRACT_COPIES)} "
          "Empirica runtime contract vendor files")


if __name__ == "__main__":
    main()
