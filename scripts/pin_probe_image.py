#!/usr/bin/env python3
"""Pin the generic Docker qualification probe image by its OCI image index digest.

`make empirica-probe-image-pin TAG=<repo:tag>` resolves the tag's multi-platform index
with `docker buildx imagetools inspect <ref> --format '{{json .Manifest}}'` (network, no
daemon, no pull) and rewrites `qualification.probe_image` in the normative support
contract: `reference` becomes `docker.io/library/<name>:<tag>@sha256:<index digest>`,
`repository` is the short library name, and `index_digest` is the index digest.

Pins are official Docker Hub library images only (`docker.io/library/<name>`, entered as
`<name>:<tag>` or fully spelled out): other registries and other namespaces are refused
before any buildx call, and their RepoDigests would not be `<repository>@<index_digest>`.
The index must be an OCI or Docker *index* (manifest list) carrying both canonical
architectures, `linux/arm64` and `linux/amd64`; otherwise nothing is written. The rewrite
replaces only the `probe_image` member and is published atomically, so the rest of the
hand-formatted contract stays byte-identical.

A pin change invalidates installed qualifications (the contract is part of the
implementation manifest), and the vendored copy the plugin ships is refreshed by
`make vendor-contracts`, which this tool reminds you to run.
"""
from __future__ import annotations

import argparse
import json
import os
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
_PIN_MEMBER = re.compile(r'    "probe_image": \{[^{}]*\}')

sys.path.insert(0, str(ROOT / "plugins" / "empirica"))
from adapters.sandbox import probe_image  # noqa: E402


class PinError(Exception):
    """The tag is malformed, not an official library image, or its registry index is not
    a digest-addressable multi-arch index covering both canonical architectures."""


def official_reference(tag: str) -> tuple[str, str]:
    """The fully-qualified `docker.io/library/<name>:<tag>` reference and short name of `<name:tag>`.

    Validates the complete input — shape, registry, namespace and name — through the
    runtime parser, so nothing ambiguous survives to a buildx call.
    """
    if "@" in tag:
        raise PinError(f"TAG must be exactly <name:tag> (got {tag!r}); the digest is resolved here")
    name, separator, tail = tag.rpartition(":")
    if not separator or "/" in tail or not _TAG.fullmatch(tail):
        raise PinError(f"TAG must be exactly <name:tag> (got {tag!r}); the digest is resolved here")
    components = name.split("/")
    if len(components) == 1:
        components = [probe_image.OFFICIAL_REGISTRY, probe_image.OFFICIAL_NAMESPACE] + components
    if (len(components) != 3 or components[0] != probe_image.OFFICIAL_REGISTRY
            or components[1] != probe_image.OFFICIAL_NAMESPACE
            or any(not component for component in components)):
        raise PinError("probe-image pins are official Docker Hub library images only: "
                       "TAG must be <name:tag> or docker.io/library/<name>:<tag>")
    # The runtime parser owns every spelling rule (the library name's charset included),
    # so validate the complete would-be pin here, before any network call.
    placeholder = "sha256:" + "0" * 64
    try:
        probed = probe_image.parse_probe_image({
            "reference": (f"{probe_image.OFFICIAL_REGISTRY}/{probe_image.OFFICIAL_NAMESPACE}/"
                          f"{components[2]}:{tail}@{placeholder}"),
            "repository": components[2],
            "index_digest": placeholder,
        })
    except probe_image.ProbeImageError as exc:
        raise PinError("TAG names no official Docker Hub library image: "
                       f"{exc}") from exc
    return probed.reference.rsplit("@", 1)[0], probed.repository


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


def _refuse_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON: `json.loads` silently keeps only the last of duplicate
    object keys, so exactly one replacement candidate could otherwise verify against
    the collapsed document it is checked with."""
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise PinError(f"the support contract carries duplicate object keys: {key!r}; "
                            "refusing ambiguous JSON")
        document[key] = value
    return document


def _pin_member(pin: probe_image.ProbeImagePin) -> str:
    """The localized replacement text for the qualification `probe_image` member."""
    return ('    "probe_image": {\n'
            f'      "reference": {json.dumps(pin.reference)},\n'
            f'      "repository": {json.dumps(pin.repository)},\n'
            f'      "index_digest": {json.dumps(pin.index_digest)}\n'
            '    }')


def verified_rewrite(raw: str, pin: probe_image.ProbeImagePin) -> str:
    """The one candidate publication whose parsed JSON differs from the original in exactly
    `qualification.probe_image` — set to the resolved pin and nothing else.

    Every `probe_image`-shaped block in the raw text is tried, and each candidate is parsed
    and compared with the expected document *before* anything is published: a wrong-location
    match (a decoy `probe_image` elsewhere in the contract) never verifies, several verifying
    matches are refused as ambiguous, duplicate object keys anywhere in the contract are
    rejected before any replacement is tried, and no verified match means the `qualification`
    member is absent. The original bytes survive every refusal.
    """
    try:
        expected = json.loads(raw, object_pairs_hook=_refuse_duplicate_keys)
    except ValueError as exc:
        raise PinError("the support contract is not JSON the pin tool can rewrite") from exc
    if not isinstance(expected.get("qualification"), dict):
        raise PinError("the support contract carries no qualification object to rewrite")
    member = _pin_member(pin)
    expected["qualification"]["probe_image"] = {
        "reference": pin.reference, "repository": pin.repository, "index_digest": pin.index_digest}
    verified = []
    for match in _PIN_MEMBER.finditer(raw):
        candidate = raw[:match.start()] + member + raw[match.end():]
        try:
            if json.loads(candidate, object_pairs_hook=_refuse_duplicate_keys) == expected:
                verified.append(candidate)
        except ValueError:
            continue
    if len(verified) > 1:
        raise PinError("the support contract carries ambiguous probe_image members: "
                       "refusing to rewrite")
    if not verified:
        raise PinError("the support contract carries no qualification probe_image member "
                       "to rewrite; a first pin belongs in a separate, reviewed change")
    return verified[0]


def publish_atomically(path: Path, text: str) -> None:
    staged = path.with_name(f".{path.name}.{os.getpid()}.pin")
    try:
        staged.write_text(text, encoding="utf-8")
        os.replace(staged, path)
    except OSError:
        staged.unlink(missing_ok=True)
        raise


def pin_probe_image(
    tag: str,
    *,
    contract_path: Path = CONTRACT,
    runner: Callable | None = None,
) -> probe_image.ProbeImagePin:
    """Rewrite `qualification.probe_image` in the contract from the tag's index digest."""
    run = runner if runner is not None else _subprocess_run
    reference, repository = official_reference(tag)
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
        "repository": repository,
        "index_digest": digest,
    })
    raw = contract_path.read_text(encoding="utf-8")
    # Publish only after the rewritten candidate parses and differs from the original in
    # exactly qualification.probe_image; every refusal leaves the original bytes untouched.
    publish_atomically(contract_path, verified_rewrite(raw, pin))
    return pin


def _subprocess_run(argv, **kwargs):
    import subprocess

    return subprocess.run(argv, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", metavar="TAG",
                        help="the <name:tag> (or docker.io/library/<name>:<tag>) of the "
                             "multi-platform probe image, e.g. alpine:3.22")
    arguments = parser.parse_args()
    try:
        pin = pin_probe_image(arguments.tag)
    except (PinError, probe_image.ProbeImageError, OSError) as exc:
        print(f"pin-probe-image: {exc}", file=sys.stderr)
        return 1
    print(f"pin-probe-image: pinned {pin.repo_digest} as {pin.reference}")
    print("pin-probe-image: a pin change invalidates installed qualifications; "
          "run `make vendor-contracts` to refresh the shipped copy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
