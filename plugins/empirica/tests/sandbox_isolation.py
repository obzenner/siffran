"""Process-lifetime isolation from developer Docker qualification authority.

Tests which reach the production bridge import and invoke this helper before the
bridge.  The temporary root stays alive for the Python process, its absent child
is never created, and subprocesses inherit the explicit environment value.
Qualification-store and sandbox-home semantic tests deliberately do not import
this module because they own their authority paths under their test fixtures.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_PROCESS_SANDBOX_ROOT = tempfile.TemporaryDirectory(
    prefix="empirica-test-sandbox-isolation-")
ABSENT_SANDBOX_HOME = (
    Path(_PROCESS_SANDBOX_ROOT.name).resolve() / "absent-authority")


def force_absent_sandbox_home() -> Path:
    """Force production composition to an absent authority for this process."""
    os.environ["EMPIRICA_SANDBOX_HOME"] = str(ABSENT_SANDBOX_HOME)
    return ABSENT_SANDBOX_HOME


# Importing the shared helper establishes isolation before a test imports the
# production bridge; each test module also invokes the idempotent function to
# make its dependency explicit and to restore it after local environment edits.
force_absent_sandbox_home()
