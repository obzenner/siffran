#!/usr/bin/env python3
"""Shared JSON bridge from host adapters to the Methodologist core.

Both in-process hosts and stdio shims call :func:`handle`; this module only wires
the host-neutral application service to the shipped shared skill resources.
It performs no semantic routing and writes no state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from application import MethodologistService  # noqa: E402

_SKILL_DIR = _PLUGIN_ROOT / "skills" / "think"


def handle(request: object) -> dict:
    return MethodologistService(_SKILL_DIR).handle(request)


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except (TypeError, ValueError):
        request = None
    json.dump(handle(request), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
