"""Digest-pinned generic Docker qualification probe image.

The one trusted probe image is a field of the vendored Docker spike support contract
(``qualification.probe_image``): one OCI image *index* reference
``docker.io/library/<name>:<tag>@sha256:<64-hex digest>`` whose single index covers
both canonical architectures, ``linux/arm64`` and ``linux/amd64``. Pins are official
Docker Hub library images only: no other registry and no other namespace qualifies,
so the pin stays one closed canonical form Docker can always inspect (admission,
below, matches the pinned digest under any repository name, so a mirror's own name
never disqualifies the image). Admission (E5) keys on the image's ``RepoDigests``
carrying the pinned index digest in some entry — the digest is the image's content
identity, the repository name is not — present (never singleton-equal), and never on
the daemon-local ``Id``: the classic store reports the platform config digest as
``Id``, and a locally built, loaded or retagged image has no ``RepoDigests`` entry with
the pinned digest at all. The contract digest is part of the implementation manifest,
so a pin change is a code change that invalidates installed qualifications.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_EMPIRICA_ROOT = Path(__file__).resolve().parents[2]
#: The vendored copy the shipped plugin reads at runtime; `make vendor-contracts` syncs it.
PROBE_IMAGE_CONTRACT = _EMPIRICA_ROOT / "vendor" / "contracts" / "empirica-docker-spike" / "v1" / "support-contract.json"

PROBE_IMAGE_FIELDS = frozenset({"reference", "repository", "index_digest"})
#: Pins are official Docker Hub library images only (Empirica 7.2 coordinator decision on E3b finding 1).
OFFICIAL_REGISTRY = "docker.io"
OFFICIAL_NAMESPACE = "library"
#: distribution/reference caps the repository path, the ``library/`` namespace included, at 255
#: characters; the pin must name an image Docker can actually inspect.
REPOSITORY_PATH_MAX = 255
_INDEX_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
_REPOSITORY_COMPONENT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")

__all__ = ["ProbeImageError", "ProbeImagePin", "parse_probe_image", "pinned_probe_image",
           "probe_image_pin", "PROBE_IMAGE_CONTRACT"]


class ProbeImageError(ValueError):
    """A vendored probe-image pin is absent, malformed, or not exactly digest-pinned."""


@dataclass(frozen=True)
class ProbeImagePin:
    reference: str
    repository: str
    index_digest: str

    @property
    def repo_digest(self) -> str:
        """The one ``RepoDigests`` form admission keys on; the daemon ``Id`` is not it."""
        return f"{self.repository}@{self.index_digest}"


def probe_image_pin(data: object) -> ProbeImagePin:
    """Read the pin out of a whole support-contract document, failing closed."""
    qualification = data.get("qualification") if isinstance(data, dict) else None
    probe = qualification.get("probe_image") if isinstance(qualification, dict) else None
    return parse_probe_image(probe)


def parse_probe_image(data: object) -> ProbeImagePin:
    """Validate ``qualification.probe_image`` and return the sealed pin.

    The data is untrusted: public-contract documents may render anywhere, so the
    parser accepts exactly the three-field digest-pinned official-library shape and
    nothing else — the full name is validated, not just the reference tail.
    """
    if not isinstance(data, dict) or set(data) != PROBE_IMAGE_FIELDS:
        raise ProbeImageError(
            f"qualification.probe_image must be an object with exactly {sorted(PROBE_IMAGE_FIELDS)}")
    reference, repository, index_digest = (
        data["reference"], data["repository"], data["index_digest"])
    if not all(isinstance(value, str) for value in (reference, repository, index_digest)):
        raise ProbeImageError("qualification.probe_image fields must be strings")
    if reference.count("@") != 1:
        raise ProbeImageError(
            f"qualification.probe_image.reference must be {OFFICIAL_REGISTRY}/{OFFICIAL_NAMESPACE}/"
            "<name>:<tag>@<digest>")
    name_tag, digest = reference.split("@", 1)
    matched = _INDEX_DIGEST.fullmatch(digest)
    if matched is None:
        raise ProbeImageError(
            "qualification.probe_image.reference must end in @sha256:<64 lowercase hex>")
    if not _INDEX_DIGEST.fullmatch(index_digest) or index_digest != digest:
        raise ProbeImageError(
            "qualification.probe_image.index_digest must equal the reference digest")
    tag = name_tag.rpartition(":")[2]
    if not name_tag.rpartition(":")[1] or "/" in tag or not _TAG.fullmatch(tag):
        raise ProbeImageError(
            f"qualification.probe_image.reference must be {OFFICIAL_REGISTRY}/{OFFICIAL_NAMESPACE}/"
            "<name>:<tag>@<digest> with a bare tag")
    components = name_tag.rsplit(":", 1)[0].split("/")
    official = (len(components) == 3 and components[0] == OFFICIAL_REGISTRY
                and components[1] == OFFICIAL_NAMESPACE and repository == components[2])
    if (not official or any(not component for component in components)
            or not _REPOSITORY_COMPONENT.fullmatch(repository)
            or len(f"{OFFICIAL_NAMESPACE}/{repository}") > REPOSITORY_PATH_MAX):
        raise ProbeImageError(
            "qualification.probe_image.reference must be exactly "
            f"{OFFICIAL_REGISTRY}/{OFFICIAL_NAMESPACE}/<name>:<tag>@<digest> for an official "
            "Docker Hub library image of at most 255 repository-path characters, the only "
            "form whose RepoDigests are <repository>@<index_digest>")
    return ProbeImagePin(reference, repository, index_digest)


def pinned_probe_image() -> ProbeImagePin:
    """The pin of record: the pin from the vendored Docker support contract."""
    try:
        raw = PROBE_IMAGE_CONTRACT.read_bytes()
    except OSError as exc:
        raise ProbeImageError(
            "the vendored Docker spike support contract is unreadable") from exc
    try:
        loaded = json.loads(raw)
    except ValueError as exc:
        raise ProbeImageError(
            "the vendored Docker spike support contract is malformed") from exc
    return probe_image_pin(loaded)
