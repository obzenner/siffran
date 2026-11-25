"""The core's own declaration of which v2 contract definitions it validates.

The ADR-0075 registration is deliberately two independent declarations, one on each side of the
boundary, and they must agree:

* the contract's side is its ``x-core-ingress`` inventory — the entry-point roots and the annotated
  ``$defs`` their closures reach (the review's finding: an inventory can be edited away together with
  its validator);
* this module is *the core's* side: the one place the core names the registered keys itself, as
  ``(document name, local pointer)`` pairs.

``scripts/check_core_annotations.py`` refuses any disagreement between the two, so a validator and
its ingress entry can both disappear from the contract and the registration still fails on this
module's word; an unregistration here fails on the contract's word just the same. Nothing imports
this module — it is a declaration, not a dependency, and the guard reads it as pure data.
"""
from __future__ import annotations

#: Every registered definition key, in the core's own hand — this tuple is the data the static guard
#: compares with the contracts' ``x-core-ingress`` + ``x-core-validator`` inventory (its callers write
#: it ``"<document>#<local pointer>"``). The Docker-spike qualification record's keys (E5, ADR-0075
#: registration) declare the closed /3 record shape: the schema's own root and every ``$defs``
#: definition its ingress reaches.
REGISTERED_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("request", "/$defs/actionEnvironment"),
    ("request", "/$defs/actionQualify"),
    ("request", "/$defs/environmentDecisionPayload"),
    ("request", "/$defs/environmentProposal"),
    ("response", "/$defs/digest256"),
    ("response", "/$defs/environmentPresentation"),
    ("response", "/$defs/environmentView"),
    ("response", "/$defs/qualificationProbeName"),
    ("response", "/$defs/qualificationView"),
    ("state", "/$defs/environmentMember"),
    ("state", "/$defs/environmentProposal"),
    ("state", "/$defs/environmentReceipt"),
    ("state", "/$defs/qualificationAttempt"),
    ("state", "/$defs/qualificationProbeName"),
    ("state", "/properties/environment"),
    ("state", "/properties/environment_history"),
    ("state", "/properties/qualification_attempts"),
    ("qualification-record", "/"),
    ("qualification-record", "/$defs/digest"),
    ("qualification-record", "/$defs/elapsedMs"),
    ("qualification-record", "/$defs/identifier"),
    ("qualification-record", "/$defs/counter"),
    ("qualification-record", "/$defs/runBinding"),
    ("qualification-record", "/$defs/spikeImageRow"),
    ("qualification-record", "/$defs/probeImage"),
    ("qualification-record", "/$defs/repoDigest"),
    ("qualification-record", "/$defs/spikeImages"),
    ("qualification-record", "/$defs/probe"),
)
