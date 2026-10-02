#!/usr/bin/env python3
"""Read-only census of Empirica operational run state.

Why this exists: ad-hoc recursive searches (``**/run.json`` over ``$HOME``, ``/tmp`` or worktrees)
walk ``node_modules`` and ``.git`` trees and take minutes. The run store has ONE documented layout
(``adapters/state/repository.py``)::

    <home>/projects/<project_id>/runs/<run_id>/gen-<generation>/run.json

so this tool enumerates exactly that fixed depth and nothing else. Individual documents outside a
home (for example qualification snapshots) are passed explicitly with ``--file``; nothing is ever
discovered by an unbounded walk.

Guarantees:
  * Read-only: documents are opened for reading only; lock files are never touched.
  * Bounded: one fixed-depth glob per home; no recursion.
  * Honest: unreadable/undecodable documents are counted as ``corrupt``, never skipped silently.
  * Host attribution is INFERRED (operational state stores no host field) and labelled as such:
      - audit role ``empirica.empirica-auditor``                        -> ``pi``
      - audit role ``empirica:empirica-auditor`` + Claude agent id      -> ``claude``
        (Claude Code subagent ids are ``a`` + 16 hex characters)
      - audit role ``empirica:empirica-auditor`` otherwise              -> ``colon-role``
        (Claude or Codex; not distinguishable from state alone)
      - no audit child                                                  -> ``no-audit``
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

LAYOUT = ("projects", "*", "runs", "*", "gen-*", "run.json")
# Operator qualification evidence: <root>/<candidate>/<cell>/state is an EMPIRICA_HOME.
QUALIFICATION_HOME_LAYOUT = "*/*/state"
_CLAUDE_AGENT_ID = re.compile(r"\Aa[0-9a-f]{16}\Z")
PI_ROLE = "empirica.empirica-auditor"
COLON_ROLE = "empirica:empirica-auditor"
_OMIT_CHILD_FIELDS = frozenset({"audit_argument"})


@dataclass(frozen=True)
class AuditChild:
    host: str
    state: str | None
    native_id: str | None


@dataclass(frozen=True)
class RunRecord:
    path: str
    run_id: str
    generation: str
    mtime: float
    status: str | None
    protocol: str | None
    governance: str | None  # governance.state, "absent" for pre-governance documents
    control_mode: str | None
    goal: str
    audits: tuple[AuditChild, ...] = field(default_factory=tuple)

    @property
    def host(self) -> str:
        hosts = sorted({a.host for a in self.audits})
        return "+".join(hosts) if hosts else "no-audit"


@dataclass(frozen=True)
class Corrupt:
    path: str
    error: str


def default_home(environ: os._Environ | dict = os.environ) -> Path:
    override = environ.get("EMPIRICA_HOME")
    return Path(override).expanduser() if override else Path.home() / ".empirica-plugin"


def infer_host(role: object, native_id: object) -> str:
    if role == PI_ROLE:
        return "pi"
    if role == COLON_ROLE:
        if isinstance(native_id, str) and _CLAUDE_AGENT_ID.match(native_id):
            return "claude"
        return "colon-role"
    return "unknown-role"


def run_documents(home: Path) -> list[Path]:
    """Exactly the documented layout under one home; never recursive."""
    root = home / "projects"
    if not root.is_dir():
        return []
    return sorted(root.glob("/".join(LAYOUT[1:])))


def qualification_homes(root: Path) -> list[Path]:
    """Homes at exactly <root>/<candidate>/<cell>/state that contain a run store; never recursive."""
    return sorted(p for p in root.glob(QUALIFICATION_HOME_LAYOUT) if (p / "projects").is_dir())


def load(path: Path) -> RunRecord | Corrupt:
    try:
        with path.open("r", encoding="utf-8") as handle:
            doc = json.load(handle)
        mtime = path.stat().st_mtime
    except (OSError, ValueError) as exc:
        return Corrupt(str(path), f"{type(exc).__name__}: {exc}")
    if not isinstance(doc, dict):
        return Corrupt(str(path), "document is not a JSON object")
    governance = doc.get("governance")
    audits = tuple(
        AuditChild(infer_host(c.get("audit_role_profile"), c.get("native_id")),
                   c.get("state"), c.get("native_id"))
        for c in doc.get("children", []) or []
        if isinstance(c, dict) and c.get("purpose") == "audit")
    parts = path.parts
    return RunRecord(
        path=str(path),
        run_id=parts[-3] if len(parts) >= 3 else "",
        generation=parts[-2] if len(parts) >= 2 else "",
        mtime=mtime,
        status=doc.get("status"),
        protocol=doc.get("protocol"),
        governance=(governance.get("state") if isinstance(governance, dict) else "absent"),
        control_mode=(governance.get("control_mode") if isinstance(governance, dict) else None),
        goal=str(doc.get("goal", "")),
        audits=audits,
    )


def collect(homes: list[Path], files: list[Path]) -> tuple[list[RunRecord], list[Corrupt]]:
    records: list[RunRecord] = []
    corrupt: list[Corrupt] = []
    seen: set[Path] = set()
    for path in [p for home in homes for p in run_documents(home)] + files:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        item = load(path)
        (corrupt if isinstance(item, Corrupt) else records).append(item)
    return records, corrupt


def select(records: list[RunRecord], *, status: str | None, host: str | None,
           run: str | None) -> list[RunRecord]:
    out = records
    if status:
        out = [r for r in out if r.status == status]
    if host:
        out = [r for r in out if host in r.host.split("+")]
    if run:
        out = [r for r in out if r.run_id.startswith(run)]
    return sorted(out, key=lambda r: r.mtime)


def summary(records: list[RunRecord], corrupt: list[Corrupt]) -> dict:
    runs = Counter((r.host, r.status, "governed" if r.governance != "absent" else "pre-governance")
                   for r in records)
    audits = Counter((a.host, a.state) for r in records for a in r.audits)
    return {
        "runs": [{"host": h, "status": s, "governance": g, "count": n}
                 for (h, s, g), n in sorted(runs.items(), key=lambda kv: tuple(map(str, kv[0])))],
        "audit_children": [{"host": h, "state": s, "count": n}
                           for (h, s), n in sorted(audits.items(), key=lambda kv: tuple(map(str, kv[0])))],
        "corrupt": [asdict(c) for c in corrupt],
        "host_attribution": "inferred from audit role profile and native id; see module docstring",
    }


def _stamp(mtime: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def render_summary(data: dict) -> str:
    lines = ["Runs (host inferred | status | governance | count)"]
    lines += [f"  {r['host']:<18} {str(r['status']):<18} {r['governance']:<15} {r['count']}"
              for r in data["runs"]]
    lines.append("Audit children (host inferred | state | count)")
    lines += [f"  {a['host']:<18} {str(a['state']):<18} {a['count']}" for a in data["audit_children"]]
    lines.append(f"Corrupt/unreadable documents: {len(data['corrupt'])}")
    lines += [f"  {c['path']}: {c['error']}" for c in data["corrupt"]]
    return "\n".join(lines)


def render_runs(records: list[RunRecord]) -> str:
    lines = []
    for r in records:
        states = ",".join(str(a.state) for a in r.audits) or "-"
        lines.append(f"{_stamp(r.mtime)}  {r.run_id[:24]:<24} {r.generation:<6} {r.host:<12} "
                     f"{str(r.status):<17} gov={r.governance:<16} audits={states:<22} {r.goal[:60]}")
    return "\n".join(lines) if lines else "(no matching runs)"


def render_detail(record: RunRecord) -> str:
    with open(record.path, encoding="utf-8") as handle:
        doc = json.load(handle)
    doc["children"] = [{k: v for k, v in c.items() if k not in _OMIT_CHILD_FIELDS}
                       for c in doc.get("children", []) or [] if isinstance(c, dict)]
    header = f"# {record.path}\n# inferred host: {record.host}; modified {_stamp(record.mtime)}"
    return header + "\n" + json.dumps(doc, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--home", action="append", type=Path,
                        help="run-store home (repeatable; default $EMPIRICA_HOME or ~/.empirica-plugin)")
    parser.add_argument("--file", action="append", type=Path, default=[],
                        help="explicit run.json document, e.g. a qualification snapshot (repeatable)")
    parser.add_argument("--qualification-root", action="append", type=Path, default=[],
                        help="add every <root>/<candidate>/<cell>/state home (repeatable)")
    parser.add_argument("--runs", action="store_true", help="list matching runs instead of the summary")
    parser.add_argument("--status", help="filter by run status, e.g. converged")
    parser.add_argument("--host", help="filter by inferred host: pi, claude, colon-role, no-audit")
    parser.add_argument("--run", help="show one run in detail (run-id prefix; audit_argument omitted)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    homes = list(args.home or [])
    for root in args.qualification_root:
        homes += qualification_homes(root)
    if not homes and not args.file and not args.qualification_root:
        homes = [default_home()]
    records, corrupt = collect(homes, args.file)
    chosen = select(records, status=args.status, host=args.host, run=args.run)

    if args.run:
        if len(chosen) != 1:
            print(f"--run {args.run!r} matched {len(chosen)} runs; use a longer prefix", file=sys.stderr)
            print(render_runs(chosen), file=sys.stderr)
            return 2
        print(json.dumps(asdict(chosen[0]), indent=2) if args.json else render_detail(chosen[0]))
        return 0
    if args.runs:
        print(json.dumps([asdict(r) for r in chosen], indent=2) if args.json else render_runs(chosen))
        return 0
    data = summary(chosen, corrupt)
    print(json.dumps(data, indent=2) if args.json else render_summary(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
