"""Host-neutral actor identity — who resolved or reviewed a claim, and how well we know it (ADR-24).

This is the application-layer port of the pure logic in ``hooks/actors.py``: it records and compares
actor attribution without touching a filesystem, a subprocess, or any host concept. The application
owns it because attribution is operational bookkeeping (ADR-31 puts dispatch tickets on the
operational plane), and because the one property this protects is epistemic, not host-specific.

THE FINDING THAT SHAPES IT — a model cannot report its own identity (ADR-24 finding 3), so an
attribution is written by *whatever dispatched the actor*, never by the actor, and every record
carries the strength of its own evidence:

* ``witnessed`` — the dispatcher chose the model and invoked the process itself. It knows because it
  decided (a CLI-exec dispatch).
* ``declared``  — an in-session spawn: the harness sees a subagent *type*, and the model resolves
  from configuration the harness never observes. Recorded, unverified — the same trust level as "a
  citation is true".

Keeping the two apart is the whole point: reporting a declared attribution as proven would be the
overclaim ADR-21 forbids. This module records and compares; it never blocks.
"""
from __future__ import annotations

import re

HUMAN, LLM_JUDGE, CODE = "HUMAN", "LLM_JUDGE", "CODE"
SOURCE_TYPES = frozenset({HUMAN, LLM_JUDGE, CODE})

WITNESSED, DECLARED = "witnessed", "declared"
ATTRIBUTIONS = frozenset({WITNESSED, DECLARED})

# Tier aliases name a cost class, not a model generation. Accepted as ADR-24 §1's explicit fallback
# but flagged, never treated as an identity.
TIER_ALIASES = frozenset({"fast", "capable", "frontier", "haiku", "sonnet", "opus"})

# Models excluded by POLICY (ADR-24 §7), not by capability. Recorded with its reason.
POLICY_EXCLUDED = {
    "fable": ("data retention — fable records and stores content for 30 days, incompatible with "
              "users routing inference through their own tenancy. A policy exclusion, not a "
              "capability judgement."),
}

# A model identifier: alphanumerics plus the separators real ids use. FULLMATCH, anchored — a search
# would accept `"claude opus; rm -rf /"` because it matches a leading substring and ignores the rest.
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,127}")


def policy_excluded(model: object) -> str | None:
    """The reason ``model`` is excluded by policy, or None. Matches on a token boundary so
    ``claude-fable-5`` and a bare ``fable`` both hit, while ``fabletown-7`` does not."""
    if not isinstance(model, str):
        return None
    tokens = set(re.split(r"[^A-Za-z0-9]+", model.lower()))
    for banned, reason in POLICY_EXCLUDED.items():
        if banned in tokens:
            return reason
    return None


def is_tier_alias(model: object) -> bool:
    """True when ``model`` names a TIER rather than a concrete model generation."""
    return isinstance(model, str) and model.strip().lower() in TIER_ALIASES


def normalise(raw: object, *, attribution: str = DECLARED,
              force_attribution: bool = False) -> dict | None:
    """A normalised actor record, or None if ``raw`` is not one.

    Returns None rather than raising: an actor is OPTIONAL everywhere it appears, so a malformed one
    degrades to "no attribution recorded" instead of breaking a gate that would otherwise work. A
    policy-excluded model is refused here — the single choke point, so §7 cannot be bypassed.

    ``force_attribution=True`` imposes ``attribution`` regardless of what ``raw`` claims. An
    in-session spawn is structurally incapable of witnessing the resolved model, so a record from
    that path claiming ``witnessed`` is wrong no matter who wrote it; honouring it would let the
    weaker path present itself as the stronger one.
    """
    if not isinstance(raw, dict):
        return None
    model = raw.get("model")
    if not isinstance(model, str):
        return None
    model = model.strip()
    if not _MODEL_RE.fullmatch(model) or policy_excluded(model):
        return None
    if force_attribution:
        attr = attribution
    else:
        attr = raw.get("attribution") if raw.get("attribution") in ATTRIBUTIONS else attribution
    if attr not in ATTRIBUTIONS:
        attr = DECLARED
    harness, provider, source_type = raw.get("harness"), raw.get("provider"), raw.get("source_type")
    return {
        "source_type": source_type if source_type in SOURCE_TYPES else LLM_JUDGE,
        "model": model,
        "harness": harness if isinstance(harness, str) and harness.strip() else None,
        "provider": provider if isinstance(provider, str) and provider.strip() else None,
        "attribution": attr,
        "is_tier": is_tier_alias(model),
    }


def same_actor(a: object, b: object) -> bool:
    """Do two actor records name the SAME model? Compares MODEL only (the same weights reached
    through two harnesses is still the same weights re-grading their own reasoning — ADR-20 P6). Two
    tier aliases are never the same actor: the string says nothing about which weights ran."""
    na, nb = normalise(a), normalise(b)
    if na is None or nb is None or na["is_tier"] or nb["is_tier"]:
        return False
    return na["model"] == nb["model"]
