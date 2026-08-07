#!/usr/bin/env python3
"""Actor identity: who resolved a claim, and how well we know it (ADR-24 §1–§4, §6, §7).

ADR-23 routed work by TIER (`fast|capable|frontier`). ADR-24 supersedes that primitive because a
tier collapses the one property a second model is worth having: `opus-5` and `gpt-5.6-sol` are
both "capable+" and are not interchangeable — different training data, different blind spots.
What a second model buys is DECORRELATED ERROR, an epistemic property, so it belongs on the claim
rather than in a cost ladder. Tier survives as an optional fallback for claims that do not care.

THE FINDING THAT SHAPES THIS MODULE — a model cannot report its own identity. Verified live
(ADR-24 finding 3): over a Bedrock Mantle endpoint pinned to `openai.gpt-5.6-sol`, codex answered
"I'm GPT-5.4" and pi answered "I'm ChatGPT", while the HTTP response carried
`"model":"openai.gpt-5.6-sol"`. So attribution is written by WHATEVER DISPATCHED THE ACTOR, never
by the actor, and every recorded attribution carries the strength of its own evidence:

  WITNESSED — empirica chose the model and invoked the process itself (CLI-exec, Mode B). The
              dispatcher knows because it decided.
  DECLARED  — an in-session `Agent` spawn. `spawn_gate.py` sees `subagent_type`; the model
              resolves from agent frontmatter AFTER the hook fires, so the harness never observes
              it. Same trust level as "a citation is true": recorded, unverified.

Keeping those two apart is the whole point. Reporting a declared attribution as proven would be
the overclaim ADR-21 forbids — and it is the specific failure ADR-24 finding 1 caught in the
shipped plugin, where the auditor and the author both resolved to `opus` and nothing recorded it.

SHAPE, borrowed rather than invented (ADR-22's standards-over-invention rule). MLflow's
`AssessmentSource` is actor-type + actor-identity in two fields — `source_type ∈ {HUMAN,
LLM_JUDGE, CODE}` plus `source_id="gpt-4o-mini"` — attached to the assessment itself. empirica
mirrors it and adds only what MLflow has no need for: the harness that ran the model, the provider
it was reached through, and the attribution strength above.

    {"source_type": "LLM_JUDGE", "model": "claude-opus-4-8", "harness": "claude-code",
     "provider": "anthropic", "attribution": "declared"}

WHAT THIS MODULE DOES NOT DO. It records and compares; it never blocks. ADR-24 §3.3 defers
blocking deliberately, following the P1 precedent: a signal resting on a DECLARED field must not
be the sole reason a run fails closed, because a false accusation and a silent pass are both lies.
Blocking becomes appropriate for a given path only once attribution there is witnessed.

Time: every `ts` is caller-stamped. Hooks generate no timestamps in a resumable run (ADR-19).
"""
import re
import uuid

# --- source types (MLflow AssessmentSource vocabulary) -----------------------
HUMAN, LLM_JUDGE, CODE = "HUMAN", "LLM_JUDGE", "CODE"
SOURCE_TYPES = frozenset({HUMAN, LLM_JUDGE, CODE})

# --- attribution strength (ADR-24 §2's trust table) -------------------------
WITNESSED, DECLARED = "witnessed", "declared"
ATTRIBUTIONS = frozenset({WITNESSED, DECLARED})

# --- harnesses empirica knows how to talk about ------------------------------
# `claude-code` is the baseline and always available (the plugin is running inside it). The others
# are OPTIONAL and only ever probed when Mode A is on (ADR-24 §5).
HARNESS_BASELINE = "claude-code"
OPTIONAL_HARNESSES = ("codex", "pi")
KNOWN_HARNESSES = frozenset({HARNESS_BASELINE, *OPTIONAL_HARNESSES})

# --- §7: models excluded BY POLICY, not by capability -----------------------
# Recorded with its reason so a later reader does not "clean up" an unused tier. `fable` is a
# valid value of Claude Code's `model:` frontmatter field, so nothing in the harness stops it —
# this list is the only thing that does.
POLICY_EXCLUDED = {
    "fable": ("data retention — fable records and stores content at Anthropic for 30 days, "
              "which is incompatible with users who route inference through their own tenancy "
              "for governance reasons. A policy exclusion, not a capability judgement."),
}

# A tier alias is NOT an actor identity: it names a cost class, and two models in one class have
# different blind spots. Accepted only as ADR-24 §1's explicit fallback, never as attribution.
TIER_ALIASES = frozenset({"fast", "capable", "frontier", "haiku", "sonnet", "opus"})

# A model identifier: alphanumerics plus the separators real ids use. FULLMATCH, anchored — an audit
# found that relaxing this to a search accepted `"claude opus; rm -rf /"` as a model id, because a
# search matches the leading substring and ignores the rest. This value reaches log lines, report
# text, and (under Mode B) an argv, so a permissive match is the wrong kind of permissive.
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,127}")


def policy_excluded(model: str) -> str | None:
    """The reason `model` is excluded by policy, or None. Matches on a token boundary so
    `claude-fable-5` and a bare `fable` both hit, while a model that merely CONTAINS the letters
    (a hypothetical `fabletown-7`) does not — an over-broad exclusion is its own bug."""
    if not isinstance(model, str):
        return None
    tokens = set(re.split(r"[^A-Za-z0-9]+", model.lower()))
    for banned, reason in POLICY_EXCLUDED.items():
        if banned in tokens:
            return reason
    return None


def is_tier_alias(model: str) -> bool:
    """True when `model` names a TIER rather than a concrete model generation."""
    return isinstance(model, str) and model.strip().lower() in TIER_ALIASES


def normalise(raw, *, attribution: str = DECLARED, force_attribution: bool = False) -> dict | None:
    """A normalised actor record, or None if `raw` is not one.

    Returns None rather than raising: an actor is OPTIONAL everywhere it appears (ADR-24 §1 — a
    claim with no actor resolves exactly as it does today), so a malformed one must degrade to
    "no attribution recorded" and never break a graph or a gate that would otherwise work.

    A policy-excluded model is refused here, which is the single choke point: every writer in this
    module goes through `normalise`, so §7 cannot be bypassed by writing a leaf directly.

    `attribution` is the DEFAULT strength when `raw` names none. `force_attribution=True` ignores
    what `raw` claims and imposes it — which is what a caller on a known dispatch path must do.
    An in-session spawn is structurally incapable of witnessing the resolved model, so a record
    arriving from that path claiming `witnessed` is wrong no matter who wrote it, and honouring it
    would let the weaker path present itself as the stronger one.
    """
    if not isinstance(raw, dict):
        return None
    model = raw.get("model")
    # Two SEPARATE guards rather than one `or`. An audit found that flipping the `or` to `and`
    # persisted `claude opus; rm -rf /` and an embedded-newline value into a real evidence leaf,
    # because a non-str would then have to ALSO fail the regex to be rejected. Splitting them means
    # neither guard can be neutralised by the other, and each fails on its own terms.
    if not isinstance(model, str):
        return None
    model = model.strip()
    if not _MODEL_RE.fullmatch(model):
        return None
    if policy_excluded(model):
        return None
    harness = raw.get("harness")
    provider = raw.get("provider")
    source_type = raw.get("source_type")
    if force_attribution:
        attr = attribution
    else:
        attr = raw.get("attribution") if raw.get("attribution") in ATTRIBUTIONS else attribution
    if attr not in ATTRIBUTIONS:  # belt and braces: never emit an unknown strength
        attr = DECLARED
    return {
        "source_type": source_type if source_type in SOURCE_TYPES else LLM_JUDGE,
        "model": model,
        "harness": harness if isinstance(harness, str) and harness.strip() else None,
        "provider": provider if isinstance(provider, str) and provider.strip() else None,
        "attribution": attr if attr in ATTRIBUTIONS else DECLARED,
        # A tier was passed where a generation belongs. Kept (ADR-24 §1 allows tier as a
        # fallback) but FLAGGED, so a report can say the attribution is a cost class rather than
        # an identity instead of quietly presenting it as one.
        "is_tier": is_tier_alias(model),
    }


def same_actor(a, b) -> bool:
    """Do two actor records name the SAME model? The comparison that decides whether audit
    independence was actually obtained (ADR-24 §3.2).

    Compares MODEL only, deliberately. The same model reached through two harnesses or two
    providers is still the same weights re-grading their own reasoning, which is exactly the
    failure ADR-20 P6 exists to close — so harness/provider must not be able to launder it.

    Two records that name a TIER are never treated as the same actor even when the strings match:
    "capable" == "capable" says nothing about which weights ran, so claiming a clash would be an
    accusation the evidence does not support. Reported as unverifiable elsewhere, not as a clash.
    """
    na, nb = normalise(a), normalise(b)
    if na is None or nb is None:
        return False
    if na["is_tier"] or nb["is_tier"]:
        return False
    return na["model"] == nb["model"]


# --- §6: deterministic per-claim session identity ---------------------------
# uuid5, because `claude --session-id` requires a VALID UUID (verified: V1) while
# `pi --session-id` and `codex exec resume` accept arbitrary strings or thread names. One
# derivation satisfies all three. uuid5 not uuid4: hooks must not use randomness in a resumable
# run (ADR-19), and a derived id means pass 3 of an audit resumes passes 1–2 instead of starting
# cold.
SESSION_NAMESPACE = uuid.NAMESPACE_URL


def session_id_for(run_id: str, claim_id: str) -> str:
    """The stable session id for (run, claim). Deterministic: same inputs → same UUID, forever,
    in any process."""
    return str(uuid.uuid5(SESSION_NAMESPACE, f"empirica:{run_id}:{claim_id}"))
