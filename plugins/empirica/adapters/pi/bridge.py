#!/usr/bin/env python3
"""Pi stdio bridge entry point — a thin shim over the shared bridge (ADR-30/31/32).

The bridge logic is consolidated in ``plugins/empirica/adapters/bridge.py`` so there is one
place that wires the host-neutral core to the real persistence adapters, shared by the Pi stdio
transport (which spawns this file) and the Claude hooks (which call the shared bridge in-process).
This file only exists as the stable subprocess target the Pi transport resolves; it adds nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

# plugins/empirica/adapters/pi/bridge.py -> plugins/empirica/adapters (the shared bridge lives here).
_ADAPTERS_ROOT = Path(__file__).resolve().parents[1]
if str(_ADAPTERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ADAPTERS_ROOT))

from bridge import main  # noqa: E402 - path shim must run before the import

if __name__ == "__main__":
    raise SystemExit(main())
