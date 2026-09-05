#!/usr/bin/env python3
"""Thin Claude UserPromptExpansion entry point; policy lives behind adapters/bridge.py."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from adapters.claude.lifecycle import run_start_main  # noqa: E402
if __name__ == "__main__":
    raise SystemExit(run_start_main())
