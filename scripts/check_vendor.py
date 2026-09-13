#!/usr/bin/env python3
"""Fail when Empirica's shipped obligation package differs from its source."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "lib/obligations"
VENDOR = ROOT / "plugins/empirica/vendor/obligations"
NAMES = {"__init__.py", "model.py", "verify.py", "revise.py", "project.py"}
differing = [
    name
    for name in sorted(NAMES)
    if not (VENDOR / name).is_file()
    or (SOURCE / name).read_bytes() != (VENDOR / name).read_bytes()
]
extra = sorted(path.name for path in VENDOR.glob("*.py") if path.name not in NAMES)
if differing or extra:
    raise SystemExit(f"vendor mismatch: differing/missing={differing}, extra={extra}")
print(f"ok: {len(NAMES)} byte-identical obligation vendor files")
