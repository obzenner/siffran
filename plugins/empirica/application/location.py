"""D7 identity/location codec (spec §8).

Pure codec for storage IDs and ``er2`` self-locating run handles. No I/O, no
environment access, no repository or adapter — just deterministic functions that
map between raw selectors, :class:`~core.records.RunKey` values, and opaque tokens.

* ``storage_id(raw)`` — derives an opaque ``s256-<64hex>`` storage ID from a raw
  selector string. Non-string raises :class:`TypeError`; empty string is valid.
* ``encode_handle(RunKey)`` — produces the canonical ``er2`` self-locating token.
  Non-``RunKey`` raises :class:`TypeError`; invalid ``p``/``s``/``g`` raises
  :class:`ValueError`.
* ``decode_handle(token)`` — returns ``RunKey | None``; **never raises** for
  arbitrary input.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

from core.records import RunKey

# --- Grammar ---------------------------------------------------------------

_SID_RE = re.compile(r"\As256-[0-9a-f]{64}\Z")
_B64URL_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")

# --- helpers ---------------------------------------------------------------


def _b64url_nopad(data: bytes) -> str:
    """Base64url-encode *data* without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _canonical_payload_bytes(p: str, s: str, g: int) -> bytes:
    """Produce the canonical compact sorted-key JSON payload bytes."""
    return json.dumps(
        {"g": g, "p": p, "s": s}, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _valid_fields(p: str, s: str, g: int) -> bool:
    """True iff *p*/*s* are exact ``str`` ``s256-<64hex>`` and *g* a positive ``int``."""
    return (
        type(p) is str and type(s) is str and type(g) is int
        and g > 0
        and _SID_RE.match(p) is not None and _SID_RE.match(s) is not None
    )


# --- public codec ----------------------------------------------------------


def storage_id(raw: str) -> str:
    """Derive ``s256-<64hex>`` from a raw selector. Non-string raises TypeError."""
    if type(raw) is not str:
        raise TypeError(f"storage_id requires str, got {type(raw).__name__}")
    return "s256-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def encode_handle(key: RunKey) -> str:
    """Produce the canonical ``er2`` token for a :class:`RunKey`.

    Non-``RunKey`` raises :class:`TypeError`. Invalid ``p``/``s``/``g`` raises
    :class:`ValueError`.
    """
    if type(key) is not RunKey:
        raise TypeError(f"encode_handle requires RunKey, got {type(key).__name__}")
    p, s, g = key.project_id, key.run_id, key.generation
    if not _valid_fields(p, s, g):
        raise ValueError("encode_handle requires valid s256-<64hex> p/s and positive int g")
    payload = _canonical_payload_bytes(p, s, g)
    checksum = hashlib.sha256(payload).digest()
    return "er2:" + _b64url_nopad(payload) + ":" + _b64url_nopad(checksum)


def decode_handle(token: object) -> RunKey | None:
    """Decode an ``er2`` token into a :class:`RunKey`, or ``None`` if invalid.

    **Never raises** for arbitrary input. Requires exact built-in ``str``
    (subclasses rejected before any method call). Validates strict base64url,
    rejects nonzero pad-bit aliases via canonical segment roundtrip, verifies the
    full 32-byte SHA-256 checksum, and requires a canonical payload.
    """
    if type(token) is not str:
        return None

    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "er2":
        return None

    pb64, cb64 = parts[1], parts[2]
    if _B64URL_RE.match(pb64) is None or _B64URL_RE.match(cb64) is None:
        return None
    if len(pb64) % 4 == 1 or len(cb64) % 4 == 1:
        return None

    try:
        payload = base64.urlsafe_b64decode(pb64 + "=" * (-len(pb64) % 4))
        checksum = base64.urlsafe_b64decode(cb64 + "=" * (-len(cb64) % 4))
    except Exception:
        return None

    # Reject nonzero pad-bit aliases: the canonical re-encode must match exactly.
    if _b64url_nopad(payload) != pb64 or _b64url_nopad(checksum) != cb64:
        return None
    if len(checksum) != 32:
        return None
    if not hmac.compare_digest(hashlib.sha256(payload).digest(), checksum):
        return None

    try:
        parsed = json.loads(payload.decode("utf-8"))
    except Exception:
        return None

    if not isinstance(parsed, dict) or set(parsed.keys()) != {"g", "p", "s"}:
        return None
    p, s, g = parsed["p"], parsed["s"], parsed["g"]
    if not _valid_fields(p, s, g):
        return None
    if _canonical_payload_bytes(p, s, g) != payload:
        return None
    return RunKey(project_id=p, run_id=s, generation=g)
