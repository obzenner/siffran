"""The pure environment proposal model for the Empirica 7.2 environment step (spec ``§4.1``, ``§5``).

An environment proposal is the author's closed four-field request for a host Docker qualification.
This module owns only pure admission decisions — closed shape, bounds, canonicalization, digest and
the host profile ceiling (``§4.4``). It performs no I/O and owns no Docker effects; image reference
syntax stays with :mod:`core.spike_plan` and is reused, never duplicated here.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import spike_plan
from .canonical import canonical_digest
from .contract_bounds import (
    ENVIRONMENT_DECISION_PAYLOAD_DOCUMENT_DIGEST_PATTERNS,
    ENVIRONMENT_DECISION_PAYLOAD_PROPOSAL_DIGEST_PATTERNS,
    ENVIRONMENT_DECISION_PAYLOAD_RECEIPT_ID_MAX_LENGTH,
    ENVIRONMENT_DECISION_PAYLOAD_RECEIPT_ID_MIN_LENGTH,
    ENVIRONMENT_DECISION_PAYLOAD_REVISION_MINIMUM,
    ENVIRONMENT_DECISION_PAYLOAD_RUN_ID_MAX_LENGTH,
    ENVIRONMENT_DECISION_PAYLOAD_RUN_ID_MIN_LENGTH,
    ENVIRONMENT_HISTORY_MAX_ITEMS,
    ENVIRONMENT_MEMBER_RECEIPTS_MAX_ITEMS,
    ENVIRONMENT_PROPOSAL_PREPARATION_MAX_ITEMS,
    ENVIRONMENT_PROPOSAL_PREPARATION_MAX_LENGTH,
    ENVIRONMENT_PROPOSAL_RATIONALE_MAX_LENGTH,
    ENVIRONMENT_PROPOSAL_RATIONALE_PATTERNS,
    ENVIRONMENT_PROPOSAL_SPIKE_IMAGES_MAX_ITEMS,
    ENVIRONMENT_RECEIPT_DOCUMENT_DIGEST_PATTERNS,
    ENVIRONMENT_RECEIPT_FINGERPRINT_PATTERNS,
    ENVIRONMENT_RECEIPT_ID_MAX_LENGTH,
    ENVIRONMENT_RECEIPT_ID_MIN_LENGTH,
    ENVIRONMENT_RECEIPT_PRESENTATION_FINGERPRINT_PATTERNS,
    ENVIRONMENT_RECEIPT_PROPOSAL_DIGEST_PATTERNS,
    ENVIRONMENT_RECEIPT_REVISION_MINIMUM,
    QUALIFICATION_ATTEMPTS_MAX_ITEMS,
)
from .governance import plain as _plain
from . import governance

PROPOSAL_SCHEMA = "empirica.environment-proposal/1"

#: The run state's environment member states (spec §4.2), the one vocabulary ``core.run`` owns.
PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"

#: The closed Block reason codes the environment admission decides with (spec §5.1). The public
#: registry owns every reason's text, sections and recovery rows; these are the core's own
#: spellings of the code identifiers it emits, exactly as every other core reason constant.
PROFILE_NOT_ALLOWED = "profile-not-allowed"
ENVIRONMENT_REQUIRED = "environment-required"
ENVIRONMENT_PENDING = "environment-pending"
ENVIRONMENT_REJECTED = "environment-rejected"
QUALIFICATION_REQUIRED = "qualification-required"
IMAGE_NOT_QUALIFIED = "image-not-qualified"
QUALIFICATION_ATTEMPTS_EXHAUSTED = "qualification-attempts-exhausted"
ENVIRONMENT_REVISIONS_EXHAUSTED = "environment-revisions-exhausted"
ENVIRONMENT_PRESENTATIONS_EXHAUSTED = "environment-presentations-exhausted"
#: A receipt that names a document other than the one the core renders for the pending revision.
DOCUMENT_MISMATCH = "environment.document_mismatch"

#: The receipt outcomes the environment deliberation records (spec §5, as the state contract's
#: ``environmentReceipt`` row owns them): one presentation of a pending revision, then the
#: decision that names it. Cancel, timeout, a missing surface and stale replies never become a
#: row — the revision simply stays pending. The rows live with their member, immutable once the
#: revision is superseded, and the reply binding and replay classification are the one shared
#: receipt engine (``core.governance.replay_verdict``), never a parallel matcher.
_PRESENT, _APPROVE, _REJECT = "present", "approve", "reject"
_PRESENT_OUTCOMES = (_PRESENT, _APPROVE, _REJECT)
_RECEIPT_FIELDS = frozenset(("id", "fingerprint", "presentation_fingerprint", "outcome",
                             "revision", "proposal_digest", "document_digest"))

#: The bounds are the contract's own word (contracts/empirica/v2/state.schema.json, regenerated straight from
#: its annotated definitions into core.contract_bounds by ``make contract-bounds``): neither side rewrites the
#: number, and drift fails ``make contract-check``.
MAX_SPIKE_IMAGES = ENVIRONMENT_PROPOSAL_SPIKE_IMAGES_MAX_ITEMS
MAX_PREPARATION_STEPS = ENVIRONMENT_PROPOSAL_PREPARATION_MAX_ITEMS
MAX_PREPARATION_CHARS = ENVIRONMENT_PROPOSAL_PREPARATION_MAX_LENGTH
MAX_RATIONALE_CHARS = ENVIRONMENT_PROPOSAL_RATIONALE_MAX_LENGTH
# State bounds (spec §4.2): every revision and its receipts are immutable history, and the row
# bound is the per-run environment revision ceiling the contract owns — a further submission past
# it refuses with its own closed reason, and nothing is ever dropped.
MAX_ENVIRONMENT_HISTORY = ENVIRONMENT_HISTORY_MAX_ITEMS
MAX_QUALIFICATION_ATTEMPTS = QUALIFICATION_ATTEMPTS_MAX_ITEMS
#: Presentations and decisions a single revision's member may carry, the row bound its state
#: contract writes (never a presentation budget of its own; the replay discipline does that).
MAX_MEMBER_RECEIPTS = ENVIRONMENT_MEMBER_RECEIPTS_MAX_ITEMS

_BODY_FIELDS = frozenset({"profile_id", "spike_images", "preparation", "rationale"})
# C0 and C1 controls never render honestly; free text must never carry them.
_CONTROL_CHARACTERS = re.compile("[\x00-\x1f\x7f-\x9f]")
# The contract writes blankness itself: the explicit cross-host whitespace class of the environmentProposal
# definition (U+FEFF inside it, where JavaScript's \S says blank and Python's strip does not). No host language's
# \s or strip semantics decides what a blank step or rationale is — the pattern is the one word.
_NON_BLANK = re.compile(ENVIRONMENT_PROPOSAL_RATIONALE_PATTERNS[0])


class EnvironmentProposalError(ValueError):
    """The environment proposal is not the exact closed ``empirica.environment-proposal/1`` body."""


def _body(value: object) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != _BODY_FIELDS:
        raise EnvironmentProposalError("environment proposal shape is not closed")
    return value


def _profile_id(value: object) -> str:
    if type(value) is not str or value not in spike_plan.NETWORK_PROFILES:
        raise EnvironmentProposalError("environment proposal profile is unknown")
    return value


def _spike_images(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= MAX_SPIKE_IMAGES:
        raise EnvironmentProposalError("environment proposal spike images must be a bounded nonempty array")
    images: list[str] = []
    for item in value:
        try:
            images.append(spike_plan.validate_local_image_reference(item))
        except spike_plan.SpikePlanError as exc:
            raise EnvironmentProposalError("environment proposal spike image is malformed") from exc
    if len(set(images)) != len(images):
        raise EnvironmentProposalError("environment proposal spike images are duplicated")
    return images


def _text(value: object, maximum: int, message: str) -> str:
    if type(value) is not str:
        raise EnvironmentProposalError("environment proposal text must be a string")
    if _CONTROL_CHARACTERS.search(value):
        raise EnvironmentProposalError("environment proposal text carries control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        # A JSON-escaped lone surrogate is not digestible and never renders honestly.
        raise EnvironmentProposalError("environment proposal text must be UTF-8") from exc
    if len(value) > maximum:
        raise EnvironmentProposalError("environment proposal text exceeds its character bound")
    if not _NON_BLANK.search(value):
        raise EnvironmentProposalError(message)
    return value


def _preparation(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= MAX_PREPARATION_STEPS:
        raise EnvironmentProposalError("environment proposal preparation must be a bounded nonempty array")
    return [
        _text(item, MAX_PREPARATION_CHARS, "environment proposal preparation step is blank")
        for item in value
    ]


def _rationale(value: object) -> str:
    return _text(value, MAX_RATIONALE_CHARS, "environment proposal rationale is blank")


def validate_proposal(value: object) -> dict[str, Any]:
    """Validate the exact closed proposal body and return it, strings as given, images in given order."""
    body = _body(value)
    return {
        "profile_id": _profile_id(body["profile_id"]),
        "spike_images": _spike_images(body["spike_images"]),
        "preparation": _preparation(body["preparation"]),
        "rationale": _rationale(body["rationale"]),
    }


def validate_action_environment(value: object) -> dict[str, object]:
    """The closed ``environment`` author action wrapper — the contract-conformance harness's validator
    (``x-core-validator``, ADR-0075): exactly ``{kind: "environment", payload: <proposal body>}``
    with the payload the closed ``empirica.environment-proposal/1`` body at its own word."""
    if type(value) is not dict or frozenset(value) != frozenset(("kind", "payload")):
        raise EnvironmentProposalError("environment action is not closed")
    if type(value["kind"]) is not str or value["kind"] != "environment":
        raise EnvironmentProposalError("environment action kind is not environment")
    validate_proposal(value["payload"])
    return value


def validate_action_qualify(value: object) -> dict[str, object]:
    """The closed ``qualify`` author action wrapper — the contract-conformance harness's validator
    (``x-core-validator``, ADR-0075): exactly ``{kind: "qualify"}``, carrying no payload."""
    if type(value) is not dict or frozenset(value) != frozenset(("kind",)):
        raise EnvironmentProposalError("qualify action is not closed")
    if type(value["kind"]) is not str or value["kind"] != "qualify":
        raise EnvironmentProposalError("qualify action kind is not qualify")
    return value


def canonical_proposal(value: object) -> dict[str, Any]:
    """The validated proposal with an order-insensitive ``spike_images`` member (sorted, as given)."""
    body = validate_proposal(value)
    body["spike_images"] = sorted(body["spike_images"])
    return body


def proposal_digest(value: object) -> str:
    """The canonical content address of the proposal body, bound to its one schema identity."""
    return canonical_digest({"schema": PROPOSAL_SCHEMA, **canonical_proposal(value)})


def profile_allowed(profile_id: object, ceiling: object) -> bool:
    """Whether ``profile_id`` stays under the host profile ceiling (spec ``§4.4``); unknown facts allow nothing."""
    if type(profile_id) is not str or profile_id not in spike_plan.NETWORK_PROFILES:
        return False
    if ceiling is None:
        return profile_id == spike_plan.ISOLATED_PROFILE
    if type(ceiling) is not str:
        return False  # a non-str ceiling can never name a profile; no equality comparison runs
    if ceiling == spike_plan.ISOLATED_PROFILE:
        return profile_id == spike_plan.ISOLATED_PROFILE
    if ceiling == spike_plan.NETWORKED_PROFILE:
        return True
    return False


def next_revision(history_revisions: object) -> int:
    """The monotonic next proposal revision for a run: 1 for no history, else one past the highest seen."""
    if type(history_revisions) not in (list, tuple):
        raise EnvironmentProposalError("environment proposal history must be a sequence of revisions")
    highest = 0
    for item in history_revisions:
        if type(item) is not int or item < 1:
            raise EnvironmentProposalError("environment proposal history is malformed")
        highest = max(highest, item)
    return highest + 1


def validate_decision(value: object) -> dict[str, object]:
    """The closed private environment receipt shape — the contract-conformance harness's validator
    (``x-core-validator``, ADR-0075): exactly the ``environmentDecisionPayload`` the request schema
    owns, every bound and pattern read from the generated module, never restated here. Behaviour
    (the write-first ordering, replay and staleness) is core-only and lives in the decision plan."""
    if type(value) is not dict or frozenset(value) != frozenset(
            ("run_id", "receipt_id", "revision", "proposal_digest", "outcome", "document_digest")):
        raise EnvironmentProposalError("environment decision is not closed")
    for name, low, high in (("run_id", ENVIRONMENT_DECISION_PAYLOAD_RUN_ID_MIN_LENGTH,
                            ENVIRONMENT_DECISION_PAYLOAD_RUN_ID_MAX_LENGTH),
                           ("receipt_id", ENVIRONMENT_DECISION_PAYLOAD_RECEIPT_ID_MIN_LENGTH,
                            ENVIRONMENT_DECISION_PAYLOAD_RECEIPT_ID_MAX_LENGTH)):
        text = value[name]
        if type(text) is not str or not low <= len(text) <= high:
            raise EnvironmentProposalError("environment decision member is malformed")
    revision = value["revision"]
    if type(revision) is not int or revision < ENVIRONMENT_DECISION_PAYLOAD_REVISION_MINIMUM:
        raise EnvironmentProposalError("environment decision revision is malformed")
    for name, patterns in (("proposal_digest", ENVIRONMENT_DECISION_PAYLOAD_PROPOSAL_DIGEST_PATTERNS),
                           ("document_digest", ENVIRONMENT_DECISION_PAYLOAD_DOCUMENT_DIGEST_PATTERNS)):
        digest_value = value[name]
        if type(digest_value) is not str or not re.fullmatch(patterns[0], digest_value):
            raise EnvironmentProposalError("environment decision digest is malformed")
    if value["outcome"] not in _PRESENT_OUTCOMES:
        raise EnvironmentProposalError("environment decision outcome is unknown")
    return value


def profile_ceiling(invocation: object) -> object:
    """The run's host profile ceiling bound once at StartRun into its invocation (spec §4.4): a
    profile id, or ``None`` — absent reads as the isolated ceiling, never a guess. Never author
    data: only the StartRun command member reaches state."""
    if not isinstance(invocation, (dict, tuple)) and not hasattr(invocation, "get"):
        return None
    return invocation.get("spike_profile")


def qualify_admission(state: object) -> str | None:
    """The pure ``qualify`` admission decision (spec §5, the effect itself is E9's): admissible
    only for the latest approved revision with attempts remaining; otherwise the one closed reason
    that names what is missing. Reads the member the run state carries and derives nothing else, so
    E9's transaction admits and charges against exactly this decision. Attempts used are the
    append-only rows, bounded by the run ceiling the contract owns (32, generated)."""
    member = getattr(state, "environment", None)
    if member is None:
        return ENVIRONMENT_REQUIRED
    member_state = member["state"]
    if member_state == PENDING:
        return ENVIRONMENT_PENDING
    if member_state == REJECTED:
        return ENVIRONMENT_REJECTED
    if len(state.qualification_attempts) >= MAX_QUALIFICATION_ATTEMPTS:
        return QUALIFICATION_ATTEMPTS_EXHAUSTED
    return None


def qualification_attempt_id(ordinal: int, delivery: object) -> str:
    """The identity of the run's ``ordinal``-th qualification attempt, asked for by one delivery
    (spec §5 step 1): the append-only row sequence makes the ordinal unique within the run, and the
    delivery — the request identity the transport carries, or ``None`` where a caller has none —
    is what lets an exact replay of a delivery find its own row again. The same delivery
    re-arriving after its attempt resolved derives the same identity and so is recognised, never
    charged again; a different delivery, or the same one asking afresh at a later ordinal, derives
    a different one."""
    return "qualification-" + canonical_digest(
        {"attempt": ordinal, "delivery": delivery}).removeprefix("sha256:")


def delivery_attempt_index(rows: object, delivery: object) -> int | None:
    """The index of the latest row this delivery's own request opened — the row whose identity is
    ``qualification_attempt_id`` of its ordinal and the delivery — or ``None`` when the delivery
    has none. Rows are an append-only sequence, so the latest match is the delivery's current
    attempt (an interrupted one closed and asked for again appends a later row)."""
    found = None
    for index, row in enumerate(rows):
        if row["attempt_id"] == qualification_attempt_id(index + 1, delivery):
            found = index
    return found


def spike_prerequisite(state: object) -> str | None:
    """The pure spike prerequisite (spec §5.1): on a run with no active qualified binding, ``environment-required`` when no approved
    environment revision exists (the way on is a proposal) and ``qualification-required`` once one
    is approved but nothing is bound (the way on is a qualification attempt)."""
    if spike_plan.spike_backend_kind(state.spike_backend) == "qualified":
        return None
    member = getattr(state, "environment", None)
    if member is not None and member["state"] == APPROVED:
        return QUALIFICATION_REQUIRED
    return ENVIRONMENT_REQUIRED


def bound_member(state: object) -> Mapping | None:
    """The approved revision the run's active qualified binding was qualified for (spec §5): the
    revision of the attempt row whose record the binding names, read from the current member or
    the immutable history (a later revision may be pending while the binding stays active), or
    ``None`` when the run holds no such binding."""
    backend = state.spike_backend
    if spike_plan.spike_backend_kind(backend) != "qualified":
        return None
    # Only a succeeded row carries a record digest.
    revision = next((row["revision"] for row in state.qualification_attempts
                     if row["record_digest"] == backend["qualification_record_digest"]), None)
    return next((member for member in superseded_history(state)
                 if member["revision"] == revision and member["state"] == APPROVED), None)


def spike_admission(state: object, image: str) -> str | None:
    """The pure spike admission against the active binding (spec §5 "Spikes"): the spike
    prerequisite first, then the plan's image reference must be one of the spike images of the
    revision the binding qualified, exactly as proposed (``image-not-qualified`` otherwise). The
    image itself is resolved per spike by the host; rebuilding a listed image needs no revision."""
    missing = spike_prerequisite(state)
    if missing is not None:
        return missing
    member = bound_member(state)
    if member is None or image not in member["proposal"]["spike_images"]:
        return IMAGE_NOT_QUALIFIED
    return None


def revision_member(proposal_body: object, revision: int, *, approved: bool) -> dict[str, object]:
    """One closed {revision, proposal, digest, state, receipt_id, receipts} member for a new
    revision: the canonical proposal body, its canonical digest, pending in a deliberative run,
    approved with no receipt row and a null receipt id (none is ever fabricated, spec §5) where
    the core derived the approval in auto, and the empty receipt list the write-first
    deliberation fills only through the shared receipt engine."""
    return {"revision": revision,
            "proposal": canonical_proposal(proposal_body),
            "digest": proposal_digest(proposal_body),
            "state": APPROVED if approved else PENDING,
            "receipt_id": None,
            "receipts": []}


def superseded_history(state: object) -> tuple[dict[str, object], ...]:
    """The immutable history after one more revision is superseded: every row ever recorded, in
    order, nothing dropped — spec §4.2 keeps every revision and its receipts because a
    qualification may still refer to an earlier revision, and the per-run ceiling refuses a
    further submission instead of evicting here (``revisions_exhausted`` guards the call)."""
    member = getattr(state, "environment", None)
    if member is None:
        return tuple(state.environment_history)
    return tuple(state.environment_history) + (_plain(member),)


def revisions_exhausted(state: object) -> bool:
    """Whether the run's environment revision allowance is spent: the per-run ceiling the state
    contract owns (the ``environment_history`` bound's own word, generated) counts the current
    member and every history row — one allowance per run, never per supersession."""
    total = len(state.environment_history) + (getattr(state, "environment", None) is not None)
    return total >= MAX_ENVIRONMENT_HISTORY


def superseded_member(state: object) -> tuple[object, tuple[dict[str, object], ...]]:
    """What a configuration revision does to the run's pending environment (spec §5, the mutual
    exclusion both ways): a pending revision is closed as superseded without approval — its
    member row, receipts and all, moves to the immutable history and the run holds no active
    revision, so the agent resubmits one once the new configuration is approved and no reply of
    the closed revision can ever approve under an unapproved configuration. A decided revision
    is returned unchanged: its approval decided under the configuration it was approved with."""
    member = getattr(state, "environment", None)
    if member is None or member["state"] != PENDING:
        return member, tuple(state.environment_history)
    return None, tuple(state.environment_history) + (_plain(member),)


def validate_receipt(value: object) -> dict[str, object]:
    """One closed private environment receipt row — the contract-conformance harness's validator
    (``x-core-validator``, ADR-0075) for the state schema's ``environmentReceipt`` definition:
    exactly the row the write-first deliberation records, every bound and pattern read from the
    generated module, never restated here. The replay binding to the reply it names stays
    core-only (the shared replay engine's discipline, mirrored by the named tests)."""
    if type(value) is not dict or frozenset(value) != _RECEIPT_FIELDS:
        raise EnvironmentProposalError("environment receipt row is not closed")
    if type(value["id"]) is not str or not (
            ENVIRONMENT_RECEIPT_ID_MIN_LENGTH <= len(value["id"]) <= ENVIRONMENT_RECEIPT_ID_MAX_LENGTH):
        raise EnvironmentProposalError("environment receipt id is malformed")
    for name, patterns in (("fingerprint", ENVIRONMENT_RECEIPT_FINGERPRINT_PATTERNS),
                           ("presentation_fingerprint", ENVIRONMENT_RECEIPT_PRESENTATION_FINGERPRINT_PATTERNS),
                           ("proposal_digest", ENVIRONMENT_RECEIPT_PROPOSAL_DIGEST_PATTERNS),
                           ("document_digest", ENVIRONMENT_RECEIPT_DOCUMENT_DIGEST_PATTERNS)):
        if type(value[name]) is not str or not re.fullmatch(patterns[0], value[name]):
            raise EnvironmentProposalError("environment receipt digest is malformed")
    if value["outcome"] not in _PRESENT_OUTCOMES:
        raise EnvironmentProposalError("environment receipt outcome is unknown")
    if type(value["revision"]) is not int or value["revision"] < ENVIRONMENT_RECEIPT_REVISION_MINIMUM:
        raise EnvironmentProposalError("environment receipt revision is malformed")
    return value


def receipt_rows(state: object):
    """Every persisted receipt row, newest first: the current member's rows in reverse, then the
    superseded revisions' in reverse — the order replay lookup resolves an id with, so a replaid
    id of an earlier revision never shadows the row that owns it now."""
    member = getattr(state, "environment", None)
    if member is not None:
        yield from reversed(member["receipts"])
    for row in state.environment_history:
        yield from reversed(row["receipts"])


def receipt_error(run_id: str, state: object, receipt: Mapping) -> str | None:
    """Validate one private environment receipt against the persisted rows (spec §5) — the same
    binding and replay discipline as ``core.governance.decision_error``, one shared engine: the
    fingerprint is the canonical digest of the entire reply. ``inert`` signals an exact historical
    replay (of the stored decision, or of the presentation its id recorded) that must never
    reopen a revision, before or after supersession; the refusal words stay the engine's private
    classes (the transaction fails closed on each): ``receipt_replay`` — this id already names
    other content than its own recorded presentation (an altered or reordered replay); a receipt
    that reaches the member checks the member itself. ``None`` admits one write: a first
    presentation (member pending, its revision named), or the approve/reject whose entire reply
    equals its named persisted presentation with only the outcome changed — the full persisted
    presentation identity: id, document digest, revision number and proposal digest, one
    binding, nothing substituted."""
    plain_receipt = _plain(dict(receipt))
    fingerprint = governance.canonical_digest(plain_receipt)
    prior = next((row for row in receipt_rows(state) if row["id"] == plain_receipt["receipt_id"]), None)
    member = getattr(state, "environment", None)
    if prior is not None:
        verdict = governance.replay_verdict(fingerprint, prior, plain_receipt["outcome"],
                                            governance.canonical_digest(
                                                {**plain_receipt, "outcome": _PRESENT}),
                                            "receipt_replay")
        if verdict is not None:
            return verdict
    if member is None:
        return "receipt_absent"  # superseded or none: nothing is pending, so no first reply decides
    if plain_receipt["run_id"] != run_id:
        return "receipt_absent"  # a receipt of another run or generation decides nothing here
    if member["state"] != PENDING:
        return "receipt_absent"  # decided: only the exact decided replay above reopens nothing
    if (plain_receipt["revision"] != member["revision"]
            or plain_receipt["proposal_digest"] != member["digest"]):
        return "receipt_stale"  # a reply for an earlier revision is stale, whatever it names
    if governance.expected_approval_kind(state.governance,
                                         governance.raises(state.governance, state.budgets)) != "host_ui":
        return "receipt_absent"  # an auto run derives approvals: no receipt ever names one
    if plain_receipt["outcome"] == _PRESENT:
        if prior is not None:
            return "receipt_absent"  # the id already named a decision: it can present nothing
        if len(member["receipts"]) >= MAX_MEMBER_RECEIPTS:
            # The per-revision presentation allowance is a
            # behavioural limit of its own, not merely a storage bound — a further
            # presentation is refused with its own closed reason (the public reason row names
            # the recovery: a new environment revision), while a presentation this revision
            # already recorded can still finalize below, and the limit is separate from the
            # per-run revision ceiling above.
            return ENVIRONMENT_PRESENTATIONS_EXHAUSTED
        return None
    if (prior is None or prior["outcome"] != _PRESENT
            or prior["revision"] != member["revision"]
            or prior["proposal_digest"] != member["digest"]):
        # defense in depth: the entire-reply fingerprint equality above already binds the
        # decision to its own persisted presentation — id, document digest, revision and proposal
        # digest — so a substituted identity of an earlier presentation cannot reach here.
        return "receipt_absent"  # nothing finalizes a presentation it does not name
    return None


def presented_member(member: Mapping, receipt: Mapping, fingerprint: str) -> dict[str, object]:
    """The member after one admitted presentation: the presentation row appended (its entire
    reply's digest recorded as both fingerprints), the revision still pending, no receipt id —
    nothing was decided."""
    row = {"id": receipt["receipt_id"], "fingerprint": fingerprint,
           "presentation_fingerprint": fingerprint, "outcome": _PRESENT,
           "revision": receipt["revision"], "proposal_digest": receipt["proposal_digest"],
           "document_digest": receipt["document_digest"]}
    return {**_plain(dict(member)), "receipts": [*_plain(list(member["receipts"])), row]}


def decided_member(member: Mapping, receipt: Mapping, fingerprint: str) -> dict[str, object]:
    """The member after the admitted approve/reject that named its own persisted presentation:
    the row's outcome and fingerprint become the decision's, the revision's state and receipt id
    bind the decision. A reject closes the presentation without approving — the member state is
    rejected ever after."""
    rows = []
    for row in member["receipts"]:
        rows.append({**row, "fingerprint": fingerprint, "outcome": receipt["outcome"]}
                    if row["id"] == receipt["receipt_id"] else row)
    return {**_plain(dict(member)), "receipts": rows,
            "state": APPROVED if receipt["outcome"] == _APPROVE else REJECTED,
            "receipt_id": receipt["receipt_id"]}
