#!/usr/bin/env python3
"""Regenerate Empirica's shipped runtime contracts from the repository SSOT."""

from check_contract_vendor import NAMES, SOURCE, VENDOR

VENDOR.mkdir(parents=True, exist_ok=True)
for path in sorted(VENDOR.rglob("*"), reverse=True):
    if path.is_file() and path.relative_to(VENDOR).as_posix() not in NAMES:
        path.unlink()
    elif path.is_dir() and not any(path.iterdir()):
        path.rmdir()
for name in sorted(NAMES):
    (VENDOR / name).write_bytes((SOURCE / name).read_bytes())
print(f"synced {len(NAMES)} Empirica runtime contract vendor files")
