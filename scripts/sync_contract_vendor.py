#!/usr/bin/env python3
"""Regenerate Empirica's shipped runtime contracts from the repository SSOT."""

from check_contract_vendor import CONTRACT_COPIES

for source, vendor, names in CONTRACT_COPIES:
    vendor.mkdir(parents=True, exist_ok=True)
    for path in sorted(vendor.rglob("*"), reverse=True):
        if path.is_file() and path.name not in names:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for name in sorted(names):
        (vendor / name).write_bytes((source / name).read_bytes())
print(f"synced {sum(len(names) for _, _, names in CONTRACT_COPIES)} Empirica runtime contract vendor files")
