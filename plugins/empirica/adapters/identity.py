"""Adapter-side model identity observation policy.

The core treats ``identity`` as an opaque equality class.  This module is the
only runtime code that interprets provider/model spellings.

Evidence for the transformations:
* Pi appends reasoning levels such as ``:high`` (Pi 0.84.3 model IDs and the
  retained E2 probe); Claude transcripts may append ``[1m]``.
* Bedrock model IDs use geo/vendor prefixes and deployment suffixes such as
  ``eu.anthropic.`` and ``-v1:0`` (AWS Bedrock IDs and retained F3/E1 probes).
* Provider IDs may themselves carry a Bedrock region
  (``amazon-bedrock-eu``); region is deployment provenance, not model identity.
Moving aliases and inference-profile ARNs are deliberately not guessed.
"""
from __future__ import annotations

import re
from typing import Any

POLICY_VERSION = "model-identity/1"

_ALIASES = {
    "opus", "sonnet", "haiku", "fable", "best", "default", "inherit", "opusplan",
}
_VENDORS = {"anthropic", "openai", "xai"}
REVIEWER_FAMILIES = ("fable", "opus", "sonnet", "haiku")
_GEO = re.compile(
    r"^(?:af|ap|apac|asia|au|ca|eu|europe|global|in|jp|kr|me|sa|uk|us)(?:-[a-z0-9]+)?\.",
    re.IGNORECASE,
)
_THINKING = re.compile(r":(?:low|medium|high|xhigh)$", re.IGNORECASE)
_CLAUDE_DURATION = re.compile(r"\s*\[\d+m\]$", re.IGNORECASE)
_BEDROCK_VERSION = re.compile(r"-v\d+(?::\d+)?$", re.IGNORECASE)
_CONCRETE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def family(model: object) -> str | None:
    """Return the known host alias family named by a model spelling."""
    if not isinstance(model, str):
        return None
    lowered = model.lower()
    return next((name for name in REVIEWER_FAMILIES if name in lowered), None)


def claude_observation(model: object, *, source: str) -> dict[str, object] | None:
    """Preserve Claude's raw model provenance for bridge-side normalization."""
    if not isinstance(model, str) or not model:
        return None
    return {"provider_id": "bedrock" if "." in model else "anthropic",
            "model_id": model, "source": source}


def observe(provider_id: object, model_id: object, *, source: str) -> dict[str, Any] | None:
    """Return one normalized vendor/model observation, or ``None`` when unknowable."""
    if not isinstance(provider_id, str) or not isinstance(model_id, str):
        return None
    raw_provider, raw_model = provider_id, model_id
    provider, model = provider_id.strip().lower(), model_id.strip().lower()
    if not provider or not model or not isinstance(source, str) or not source:
        return None
    if model == "<synthetic>" or model.startswith("arn:") or ":application-inference-profile/" in model:
        return None

    model = _CLAUDE_DURATION.sub("", model)
    model = _THINKING.sub("", model)
    model = _BEDROCK_VERSION.sub("", model)
    while True:
        stripped = _GEO.sub("", model, count=1)
        if stripped == model:
            break
        model = stripped

    vendor = None
    first, dot, rest = model.partition(".")
    if dot and first in _VENDORS:
        vendor, model = first, rest
    elif provider in _VENDORS:
        vendor = provider
    else:
        # Provider names like amazon-bedrock-eu describe transport and region;
        # without a vendor segment in the model they cannot establish identity.
        vendor = next((candidate for candidate in sorted(_VENDORS)
                       if provider.startswith(candidate + "-")), None)

    if (vendor is None or not model or model in _ALIASES or model.endswith("-latest")
            or not _CONCRETE.fullmatch(model)):
        return None
    return {
        "identity": f"{vendor}/{model}",
        "provider_id": raw_provider,
        "model_id": raw_model,
        "policy_version": POLICY_VERSION,
        "source": source,
    }
