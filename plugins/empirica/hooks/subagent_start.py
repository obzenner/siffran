#!/usr/bin/env python3
"""Thin Claude SubagentStart entry point; correlation policy lives in the adapter."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from adapters.claude.lifecycle import subagent_start_main  # noqa: E402
if __name__ == "__main__":
    raise SystemExit(subagent_start_main())
