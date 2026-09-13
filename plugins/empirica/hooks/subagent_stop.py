#!/usr/bin/env python3
"""Thin Claude SubagentStop entry point; ingestion policy lives in the adapter."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from adapters.claude.lifecycle import subagent_stop_main  # noqa: E402
if __name__ == "__main__":
    raise SystemExit(subagent_stop_main())
