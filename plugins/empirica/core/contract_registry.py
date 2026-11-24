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

#: Every registered definition key of ``contracts/empirica/v2``, in the core's own hand — this tuple is
#: the data the static guard compares with the contracts' ``x-core-ingress`` + ``x-core-validator``
#: inventory (its callers write it ``"<document>#<local pointer>"``).
REGISTERED_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("request", "/$defs/environmentProposal"),
    ("state", "/$defs/environmentMember"),
    ("state", "/$defs/environmentProposal"),
    ("state", "/$defs/qualificationAttempt"),
    ("state", "/properties/environment"),
    ("state", "/properties/environment_history"),
    ("state", "/properties/qualification_attempts"),
)
