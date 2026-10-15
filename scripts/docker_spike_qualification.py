#!/usr/bin/env python3
"""Compatibility import for the shipped sandbox qualification implementation."""
from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "empirica"
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from adapters.sandbox.docker import *  # noqa: F403,E402
