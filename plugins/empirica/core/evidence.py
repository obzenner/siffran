"""Pure evidence-record classification shared by core projection and application storage."""
from __future__ import annotations


def evidence_fold(statement: object) -> str | None:
    """Return the fold named by a validated in-toto predicate type, if supported."""
    if not isinstance(statement, dict):
        return None
    predicate_type = statement.get("predicateType")
    if not isinstance(predicate_type, str):
        return None
    if predicate_type.endswith("/research/v1"):
        return "research"
    if predicate_type.endswith("/spike/v1"):
        return "spike"
    return None
