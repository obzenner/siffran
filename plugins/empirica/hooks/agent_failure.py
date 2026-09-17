#!/usr/bin/env python3
"""Thin Claude Agent failure reconciliation entry point."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from adapters.claude.lifecycle import agent_failure_main  # noqa: E402
if __name__ == "__main__":
    raise SystemExit(agent_failure_main())
