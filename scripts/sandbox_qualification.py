#!/usr/bin/env python3
"""Checkout entry point for the plugin-owned sandbox operator CLI."""
from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "empirica"
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from adapters.sandbox.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
