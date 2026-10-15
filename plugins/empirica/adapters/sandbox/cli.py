#!/usr/bin/env python3
"""Operator-owned Docker sandbox qualification CLI shipped with Empirica.

This command never installs Docker, pulls an image, starts/stops a daemon or VM,
switches context, repairs permissions, or runs from inside an Empirica workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from adapters.sandbox import docker, record  # noqa: E402

DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "siffran-sandbox-qualification"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    preflight = commands.add_parser("preflight", help="passively inspect availability; never install")
    qualify = commands.add_parser("qualify", help="run one probe or the complete diagnostic; never install")
    install = commands.add_parser("install", help="run all probes and atomically publish qualification authority")
    for command in (preflight, qualify, install):
        command.add_argument("--backend", choices=(docker.BACKEND,), default=docker.BACKEND)
    qualify.add_argument(
        "--probe", choices=("preflight", "all", *docker.MANDATORY_PROBES), default="all")
    for command in (preflight, qualify):
        command.add_argument("--output", type=Path)
    install.add_argument("--sandbox-home", type=Path,
                         help="explicit operator/test override; independent of EMPIRICA_HOME")
    return parser.parse_args(argv)


def render_report(result: dict[str, Any]) -> str:
    preflight = result["preflight"]
    environment_preflight = result.get("environment_preflight") or {}
    requested = result.get("requested_probe", "all")
    if result.get("complete_qualification"):
        complete_status = "PASS"
    elif requested == "all":
        complete_status = "FAIL"
    else:
        complete_status = "INCOMPLETE"
    if requested == "preflight":
        operation = "AVAILABLE" if result.get("preflight_available") and result.get("passed") else "UNAVAILABLE"
    else:
        operation = "PASS" if result.get("selected_probe_passed") else "FAIL"
    lines = [
        "# Sandbox qualification: Docker spike backend", "",
        f"- Requested operation: **{requested} — {operation}**",
        f"- Complete qualification: **{complete_status}**",
        f"- Docker backend eligible: **{str(result['docker_backend_eligible']).lower()}**",
        f"- Empirica contract compatible: **{str(result['empirica_contract_compatible']).lower()}**",
        f"- Host subprocess fallback eligible: **{str(result['fallback_eligible']).lower()}**",
        f"- Qualified for untrusted code: **{str(result['qualified_for_untrusted_code']).lower()}**",
        f"- Cleanup verified: **{str(result['cleanup_verified']).lower()}**",
        f"- Host environment ready: **{str(environment_preflight.get('available') is True).lower()}**",
        f"- Qualification run: `{result['qualification_run_id']}`",
        f"- Generated: `{result['generated_at']}`",
        f"- Host: `{preflight['host_os']} {preflight['host_release']}` (`{preflight['host_arch']}`)",
        f"- Docker CLI: `{preflight['docker_cli'] or 'unavailable'}`",
        f"- Docker CLI SHA-256: `{preflight['docker_cli_sha256'] or 'unavailable'}`",
        f"- Docker context/endpoint: `{preflight['context'] or 'unavailable'}` / "
        f"`{preflight['endpoint'] or 'unavailable'}`",
        f"- Docker client/server: `{preflight['client_version'] or 'unavailable'}` / "
        f"`{preflight['server_version'] or 'unavailable'}`",
        f"- Engine platform: `{preflight['server_os'] or 'unavailable'}/"
        f"{preflight['server_arch'] or 'unavailable'}`",
        f"- Image RepoDigest: `{preflight['image_repo_digest']}`",
        f"- Image config ID: `{preflight['image_config_id'] or 'unavailable'}`",
        f"- Policy: `{result['policy_sha256'] or 'unavailable'}`", "",
    ]
    environment_reasons = environment_preflight.get("reasons") or []
    if environment_reasons:
        lines.extend(("## Environment preflight refusals", ""))
        lines.extend(f"- {reason}" for reason in environment_reasons)
        lines.append("")
    python_identity = environment_preflight.get("python") or {}
    if python_identity:
        lines.extend(("## Host environment identity", "",
                      f"- Python: `{python_identity.get('implementation')} "
                      f"{python_identity.get('version')}` (`{python_identity.get('cache_tag')}`)",
                      f"- Executable digest: `{python_identity.get('executable_sha256')}`", ""))
    if preflight["reasons"]:
        lines.extend(("## Preflight refusals", ""))
        lines.extend(f"- {reason}" for reason in preflight["reasons"])
        lines.append("")
    if result.get("fatal_error"):
        lines.extend(("## Fatal qualification error", "", f"- `{result['fatal_error']['class']}`: "
                      f"{result['fatal_error']['message']}", ""))
    if result.get("cleanup_error"):
        lines.extend(("## Cleanup error", "", f"- `{result['cleanup_error']['class']}`: "
                      f"{result['cleanup_error']['message']}", ""))
    lines.extend(("## Probes", "", "| Probe | Result | Elapsed |", "|---|---:|---:|"))
    for probe in result["probes"]:
        status = "SKIP" if probe["details"].get("skipped") else "PASS" if probe["passed"] else "FAIL"
        lines.append(f"| `{probe['name']}` | {status} | {probe['elapsed_ms']} ms |")
    if not result["probes"]:
        lines.append("| _(preflight only)_ | — | — |")
    lines.extend(("", "## Resource census", ""))
    for when in ("before", "after"):
        census = result.get(f"census_{when}")
        if census is None:
            lines.append(f"- {when}: unavailable")
            continue
        lines.append(
            f"- {when}: {census['all_containers']['count']} containers "
            f"({census['qualification_containers']['count']} qualification); "
            f"{census['all_networks']['count']} networks "
            f"({census['qualification_networks']['count']} qualification)")
    recovered = result.get("recovered_orphan_container_ids", [])
    lines.append(f"- qualification-owned interrupted containers recovered: {len(recovered)}")
    lines.extend(("", "## Limitations", ""))
    lines.extend(f"- {item}" for item in result["limitations"])
    lines.extend(("", "Machine-readable identities, policy, probe details, output hashes/counts/tails, "
                  "and census IDs are in the adjacent JSON file.", ""))
    return "\n".join(lines)


def write_reports(result: MappingLike, output: Path | None = None) -> tuple[Path, Path]:
    selected = output or DEFAULT_OUTPUT_DIR / (
        f"docker-{result['requested_probe']}-{result['qualification_run_id']}.json")
    selected = selected.expanduser().resolve()
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = selected.with_suffix(".md")
    report.write_text(render_report(dict(result)), encoding="utf-8")
    return selected, report


MappingLike = dict[str, Any]


def main(
    argv: Sequence[str] | None = None, *,
    qualifier: Callable[[str], dict[str, Any]] = docker.qualify_docker,
    store_factory: Callable[[Path | None], record.QualificationStore] = record.QualificationStore,
) -> int:
    args = parse_args(argv)
    authority_store = None
    if args.operation == "install":
        try:
            authority_store = store_factory(args.sandbox_home)
            authority_store.prepare()
        except (record.QualificationRecordError, OSError) as exc:
            print(f"install refused: {exc}", file=sys.stderr)
            return 1
    probe = "preflight" if args.operation == "preflight" else "all" if args.operation == "install" else args.probe
    result = qualifier(probe)
    output, markdown = write_reports(result, getattr(args, "output", None))
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"output: {output}")
    print(f"report: {markdown}")
    if args.operation == "install":
        if not result.get("complete_qualification"):
            print("install refused: complete current-code all-probe qualification required", file=sys.stderr)
            return 1
        try:
            authority = record.build_record(result, report_paths=(output, markdown))
            assert authority_store is not None
            installed = authority_store.publish(authority)
        except record.QualificationRecordError as exc:
            print(f"install refused: {exc}", file=sys.stderr)
            return 1
        print(f"installed qualification record: {installed}")
        print(f"execution binding: {authority['execution_binding_digest']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
