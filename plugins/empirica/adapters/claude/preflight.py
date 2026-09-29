"""Read-only Claude doctor/preflight; it never mutates or gates a run."""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from .invocation import Invocation, parse_invocation


def diagnose(
    restore_response: object, *, invocation: Invocation | None = None,
    probe: object | None = None,
) -> dict[str, Any]:
    """Return the baseline total preflight report without external actor probes."""
    del restore_response, probe
    unknown = list(invocation.unknown_flags) if invocation is not None else []
    recommendations = (["Unknown invocation flags refuse activation: " + ", ".join(unknown)]
                       if unknown else [])
    return {
        "baseline": {"harness": "claude-code", "status": "permitted"},
        "departs_from_baseline": False,
        "unknown_flags": unknown,
        "tools": {},
        "probed_optional": False,
        "spends_inference": False,
        "recommendations": recommendations,
    }


def main(argv: list[str] | None = None) -> int:
    """Print the baseline preflight report and surface unknown invocation flags."""
    args = list(sys.argv[1:] if argv is None else argv)
    invocation = parse_invocation({"command_args": " ".join(args)}, environ=os.environ)
    json.dump(diagnose({}, invocation=invocation), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
