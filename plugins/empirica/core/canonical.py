"""The single canonical JSON serializer and digest implementation for Empirica core."""
from __future__ import annotations

import hashlib
import json
import math


class CanonicalJSONError(ValueError):
    pass


def canonical_json(value) -> str:
    """Serialize sorted compact JSON as literal UTF-8 text without ASCII escaping."""
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalJSONError("canonical JSON rejects non-finite floats")
    if value is None or isinstance(value, bool) or isinstance(value, (int, float, str)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            raise CanonicalJSONError("canonical JSON mapping keys must be strings")
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + canonical_json(value[key])
            for key in sorted(keys)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, (set, frozenset)):
        raise CanonicalJSONError("canonical JSON rejects unordered containers")
    raise CanonicalJSONError(
        f"canonical JSON rejects unsupported type {type(value).__name__}")


def canonical_digest(value) -> str:
    """Return the SHA-256 identity of canonical UTF-8 JSON bytes."""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
