#!/usr/bin/env python3
"""Pin the generic Docker qualification probe image by its OCI image index digest.

`make empirica-probe-image-pin TAG=<repo:tag>` resolves the tag's multi-platform index
with `docker buildx imagetools inspect <ref> --format '{{json .Manifest}}'` (network, no
daemon, no pull) and rewrites `qualification.probe_image` in the normative support
contract: `reference` becomes the canonical `<name>:<tag>@sha256:<index digest>`,
`repository` is the short last name component, and `index_digest` is the index digest.
The index must be an OCI or Docker *index* (manifest list) carrying both canonical
architectures, `linux/arm64` and `linux/amd64`; otherwise nothing is written.

A pin change invalidates installed qualifications (the contract is part of the
implementation manifest), and the vendored copy the plugin ships is refreshed by
`make vendor-contracts`, which this tool reminds you to run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "empirica-docker-spike" / "v1" / "support-contract.json"
FORMAT = "{{json .Manifest}}"
#: One OCI index reference covers both canonical architectures; nothing else pins.
INDEX_MEDIA_TYPES = ("application/vnd.oci.image.index.v1+json",
                     "application/vnd.docker.distribution.manifest.list.v2+json")
CANONICAL_ARCHITECTURES = ("amd64", "arm64")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

sys.path.insert(0, str(ROOT / "plugins" / "empirica"))
from adapters.sandbox import probe_image  # noqa: E402


class PinError(Exception):
    """The tag is malformed, or its registry index is not a digest-addressable multi-arch index."""


def canonical_name(tag: str) -> str:
    """The fully-qualified repository name of `<name:tag>`, with Docker Hub's default registry."""
    name, separator, tail = tag.rpartition(":")
    if not separator or "/" in tail or "@" in tag or not _TAG.fullmatch(tail):
        raise PinError(f"TAG must be exactly <name:tag> (got {tag!r}); the digest is resolved here")
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        return name
    return f"docker.io/{'library/' if len(parts) == 1 else ''}{name}"


def _platform_architectures(manifest: object) -> set[str]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("manifests"), list):
        raise PinError("the buildx index answer carries no manifests list")
    found = set()
    for entry in manifest["manifests"]:
        platform = entry.get("platform") if isinstance(entry, dict) else None
        if (isinstance(platform, dict) and platform.get("os") == "linux"
                and platform.get("architecture") in CANONICAL_ARCHITECTURES):
            found.add(platform["architecture"])
    return found


def pin_probe_image(
    tag: str,
    *,
    contract_path: Path = CONTRACT,
    runner: Callable | None = None,
) -> probe_image.ProbeImagePin:
    """Rewrite `qualification.probe_image` in the contract from the tag's index digest."""
    run = runner if runner is not None else _subprocess_run
    name = canonical_name(tag)
    reference = f"{name}:{tag.rpartition(':')[2]}"
    completed = run(["docker", "buildx", "imagetools", "inspect", reference, "--format", FORMAT],
                    capture_output=True, text=True, check=False)
    if getattr(completed, "returncode", 1) != 0:
        raise PinError(f"docker buildx imagetools inspect failed for {reference}: "
                       f"{getattr(completed, 'stderr', '') or 'no diagnostics'}")
    try:
        manifest = json.loads(completed.stdout)
    except (ValueError, AttributeError) as exc:
        raise PinError(f"the buildx index answer for {reference} is not JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("mediaType") not in INDEX_MEDIA_TYPES:
        raise PinError(f"{reference} is not an OCI or Docker index media type; "
                       "a digest-pinned probe image must be multi-platform")
    digest = manifest.get("digest")
    if not isinstance(digest, str) or not _SHA256_DIGEST.fullmatch(digest):
        raise PinError("the buildx index answer names no sha256:<64-hex> index digest")
    architectures = _platform_architectures(manifest)
    for architecture in CANONICAL_ARCHITECTURES:
        if architecture not in architectures:
            raise PinError(f"the index for {reference} does not carry linux/{architecture}; "
                          "one pinned index must cover both canonical architectures")
    pin = probe_image.parse_probe_image({
        "reference": f"{reference}@{digest}",
        "repository": name.rsplit("/", 1)[-1],
        "index_digest": digest,
    })
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract.get("qualification"), dict):
        raise PinError(f"{contract_path.name} carries no qualification section")
    contract["qualification"]["probe_image"] = {
        "reference": pin.reference, "repository": pin.repository, "index_digest": pin.index_digest}
    contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return pin


def _subprocess_run(argv, **kwargs):
    import subprocess

    return subprocess.run(argv, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", metavar="TAG",
                        help="the <repo:tag> of the multi-platform probe image, e.g. alpine:3.22")
    arguments = parser.parse_args()
    try:
        pin = pin_probe_image(arguments.tag)
    except (PinError, probe_image.ProbeImageError) as exc:
        print(f"pin-probe-image: {exc}", file=sys.stderr)
        return 1
    print(f"pin-probe-image: pinned {pin.repo_digest} as {pin.reference}")
    print("pin-probe-image: a pin change invalidates installed qualifications; "
          "run `make vendor-contracts` to refresh the shipped copy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
