#!/usr/bin/env python3
"""Stable Pi stdio entry point for the shared Methodologist bridge."""
from __future__ import annotations

import sys
from pathlib import Path

_ADAPTERS_ROOT = Path(__file__).resolve().parents[1]
if str(_ADAPTERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ADAPTERS_ROOT))

from bridge import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
