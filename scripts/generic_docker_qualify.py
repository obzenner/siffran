#!/usr/bin/env python3
"""Write one Docker spike qualification candidate; never installs authority."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))

from adapters.sandbox import generic_qualification as qualification  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=sorted(qualification.spike_plan.NETWORK_PROFILES))
    parser.add_argument("--image", required=True)
    parser.add_argument("--docker", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        record = qualification.qualify_to_candidate(
            profile_id=args.profile, image_reference=args.image, docker_cli=args.docker,
            output=args.output, work_root=args.work_root)
    except qualification.GenericQualificationFailure as exc:
        print(json.dumps({"ok": False, "reason": exc.reason.value, "detail": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "candidate": str(args.output),
                      "record_digest": record["qualification_record_digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
