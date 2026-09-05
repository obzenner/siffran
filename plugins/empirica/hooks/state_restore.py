#!/usr/bin/env python3
"""Thin Claude SessionStart:compact entry point; state comes from RestoreRun."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from adapters.claude.lifecycle import restore_main  # noqa: E402
if __name__ == "__main__":
    raise SystemExit(restore_main())
