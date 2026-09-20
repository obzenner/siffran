#!/usr/bin/env python3
"""D7-B tests for the ``application.location`` codec (spec §8).

Green: the production module ``plugins/empirica/application/location.py`` implements the
storage_id / encode_handle / decode_handle contract. Each test method specifies the exact
contract that the codec satisfies.

Scope (D7 spec §8 — identity and location), D7-B codec functions only:

* ``storage_id(raw)`` — derives a safe, opaque ``s256-<64hex>`` storage ID from a raw selector
  string. Non-string raises ``TypeError``; empty string is valid (returns the formula literal).
* ``encode_handle(RunKey)`` — produces the canonical ``er2`` self-locating token. Non-``RunKey``
  raises ``TypeError``; invalid ``p``/``s``/``g`` (bool/zero/non-positive) raises ``ValueError``.
* ``decode_handle(token)`` — returns ``RunKey | None``; **never raises**. Requires exact
  built-in ``str``; rejects ``str`` subclasses before any method call.
* Canonical payload JSON (sorted keys, no whitespace), base64url without padding, full raw
  32-byte SHA-256 checksum, strict canonical re-encode, canonical b64 roundtrip (rejects
  nonzero pad-bit aliases).
* ``p`` / ``s`` exact ``s256-<64hex>`` grammar; generation positive int (bool rejected).
* Unicode determinism; distinct selectors → distinct IDs; token roundtrip.

**Not in scope:** service mapping, Inert tests, bridge wiring, located adapter/facade — Slice F.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_PLUGIN_ROOT = _HERE.parent.parent  # plugins/empirica — makes `core` and `application` importable
sys.path.insert(0, str(_PLUGIN_ROOT))

from core.records import RunKey  # noqa: E402

# --- Import the production codec module. -------------------------------------------
import application.location as location  # noqa: E402,F401

# --- Independently generated, then pasted literal vectors/constants. -----------------
# storage_id("alpha"), storage_id("beta"), storage_id("ünïcödé-pröject"), storage_id("")
SID_ALPHA = "s256-8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
SID_BETA = "s256-f44e64e75f3948e9f73f8dfa94721c4ce8cbb4f265c4790c702b2d41cfbf2753"
SID_UNICODE = "s256-f41a55d1f223df4614cd4b121a8e05d538cae96a0705ed42f7bd17bc942a344e"
SID_EMPTY = "s256-e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# One exact RunKey and its canonical er2 token (sorted compact payload, full checksum, no pad).
CANON_KEY = RunKey(project_id=SID_ALPHA, run_id=SID_BETA, generation=7)
CANON_TOKEN = "er2:eyJnIjo3LCJwIjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4IiwicyI6InMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyJ9:T8R-dXdb4f9W3jdnpp1H1OS4yXgXkyLxd7rt97zj0ww"

# Valid-checksum tokens with non-canonical or schema-invalid payloads (all must decode to None).
BAD_TOKENS = {
    "noncanonical_json": "er2:eyJzIjogInMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyIsICJwIjogInMyNTYtOGVkM2Y2YWQ2ODViOTU5ZWFkNzAyMjUxOGUxYWY3NmNkODE2ZjhlOGVjN2NjZGRhMWVkNDAxOGU4ZjIyMjNmOCIsICJnIjogN30:L1ftMlRuTJ8hqiDkhFGfiN06m-KV-pVZjMnC8E2hG_M",
    "wrong_keys": "er2:eyJnIjo3LCJwcm9qZWN0IjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4Iiwic2Vzc2lvbiI6InMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyJ9:x67a6mp4BEq9QlzpwiSQ8nsTAon43WMIxAatOnAF_o4",
    "extra_keys": "er2:eyJnIjo3LCJwIjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4IiwicyI6InMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyIsIngiOiJleHRyYSJ9:lnDFsG9VI2DEdlp_AKCTedfRW5tvOr0rxA28qssBC78",
    "bad_p": "er2:eyJnIjo3LCJwIjoibm90LWEtdmFsaWQtc3RvcmFnZS1pZCIsInMiOiJzMjU2LWY0NGU2NGU3NWYzOTQ4ZTlmNzNmOGRmYTk0NzIxYzRjZThjYmI0ZjI2NWM0NzkwYzcwMmIyZDQxY2ZiZjI3NTMifQ:7X4zekU1Op86NFVLcHL5lW5xOL9RxbmPBIw4TZgKRqE",
    "bad_s": "er2:eyJnIjo3LCJwIjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4IiwicyI6Im5vdC1hLXZhbGlkLXN0b3JhZ2UtaWQifQ:IQoHgApTMmkdYamXC5aDrlvYOHvFekpCG5n_uKjjVEk",
    "g_zero": "er2:eyJnIjowLCJwIjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4IiwicyI6InMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyJ9:hR2RzGEQ-YyLi8Q6O3Gi12X0N06Iu4o3jo8NVpo7gTk",
    "g_negative": "er2:eyJnIjotMSwicCI6InMyNTYtOGVkM2Y2YWQ2ODViOTU5ZWFkNzAyMjUxOGUxYWY3NmNkODE2ZjhlOGVjN2NjZGRhMWVkNDAxOGU4ZjIyMjNmOCIsInMiOiJzMjU2LWY0NGU2NGU3NWYzOTQ4ZTlmNzNmOGRmYTk0NzIxYzRjZThjYmI0ZjI2NWM0NzkwYzcwMmIyZDQxY2ZiZjI3NTMifQ:4saq6ZCGMz28mEa8IrSNf3Xj7Q5E71HaDxJ0CUUVbtA",
    "g_bool": "er2:eyJnIjp0cnVlLCJwIjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4IiwicyI6InMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyJ9:w9n6lCXQouFNh0kjAL4KMpXY5MbpbQ0srndYUiRmF28",
    "g_float": "er2:eyJnIjoxLjAsInAiOiJzMjU2LThlZDNmNmFkNjg1Yjk1OWVhZDcwMjI1MThlMWFmNzZjZDgxNmY4ZThlYzdjY2RkYTFlZDQwMThlOGYyMjIzZjgiLCJzIjoiczI1Ni1mNDRlNjRlNzVmMzk0OGU5ZjczZjhkZmE5NDcyMWM0Y2U4Y2JiNGYyNjVjNDc5MGM3MDJiMmQ0MWNmYmYyNzUzIn0:6c_krJOyRUKcJ7fKYwi3N3_d1vJJ4WkflFT6Y_1dGoA",
}

# Pad-bit alias tokens (independently generated). Payload alias: g=10 with nonzero pad
# bits in last b64 char — same decoded bytes, checksum still verifies. Checksum alias:
# CANON_TOKEN final 'w' -> 'x', same decoded 32 bytes. Both None via canonical roundtrip.
PAD_ALIAS_PAYLOAD = "er2:eyJnIjoxMCwicCI6InMyNTYtOGVkM2Y2YWQ2ODViOTU5ZWFkNzAyMjUxOGUxYWY3NmNkODE2ZjhlOGVjN2NjZGRhMWVkNDAxOGU4ZjIyMjNmOCIsInMiOiJzMjU2LWY0NGU2NGU3NWYzOTQ4ZTlmNzNmOGRmYTk0NzIxYzRjZThjYmI0ZjI2NWM0NzkwYzcwMmIyZDQxY2ZiZjI3NTMifR:YiWm1Dg7hywIIA999kOm-qmj2n5KO3rgShofTzWBeGQ"
PAD_ALIAS_CHECKSUM = "er2:eyJnIjo3LCJwIjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4IiwicyI6InMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyJ9:T8R-dXdb4f9W3jdnpp1H1OS4yXgXkyLxd7rt97zj0wx"

# --- Narrow helpers: base64 decode for structural assertions; byte mutation of literal token.
_SAFE_SEGMENT = re.compile(r"\A[A-Za-z0-9_.-]+\Z")


def _b64decode(s: str) -> bytes:
    """base64url-decode for structural assertions only (no token signing)."""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _flip_part(token: str, part: int, idx: int, mask: int = 0x01) -> str:
    """Flip one byte of the payload (part=1) or checksum (part=2) of a literal token."""
    prefix, p, c = token.split(":")
    parts = [prefix, p, c]
    raw = bytearray(_b64decode(parts[part]))
    raw[idx] ^= mask
    parts[part] = _b64url_nopad(bytes(raw))
    return ":".join(parts)


def _inject(token: str, part: int, char: str) -> str:
    """Replace one character in the payload (part=1) or checksum (part=2) with char."""
    prefix, p, c = token.split(":")
    parts = [prefix, p, c]
    s = parts[part]
    mid = len(s) // 2
    parts[part] = s[:mid] + char + s[mid + 1:]
    return ":".join(parts)


def _insert_edge(token: str, part: int, char: str) -> tuple[str, str]:
    """Insert char as prefix and suffix of the payload (part=1) or checksum (part=2).

    Unlike ``_inject`` (which replaces), this *inserts* without removing the original
    content, so the valid field text is preserved and the token is still rejected.
    """
    prefix, p, c = token.split(":")
    parts = [prefix, p, c]
    s = parts[part]
    parts[part] = char + s
    prefixed = ":".join(parts)
    parts[part] = s + char
    suffixed = ":".join(parts)
    return prefixed, suffixed


class ExplodingStr(str):
    """A ``str`` subclass whose ``split`` raises — proves ``type(token) is str`` rejects
    subclasses before any method is called."""

    def split(self, *args, **kwargs):  # type: ignore[override]
        raise AssertionError("ExplodingStr.split must never be called")


class TestLocationCodec(unittest.TestCase):
    """D7 spec §8 — storage_id, encode_handle, decode_handle codec contract."""

    # --- storage_id ----------------------------------------------------------

    def test_storage_id_formula_literals(self):
        """storage_id(raw) == 's256-' + sha256(raw_utf8).hexdigest() for pasted literals."""
        for raw, expected in [
            ("alpha", SID_ALPHA),
            ("beta", SID_BETA),
            ("ünïcödé-pröject", SID_UNICODE),
            ("", SID_EMPTY),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(location.storage_id(raw), expected)

    def test_storage_id_non_string_raises_type_error(self):
        for bad in [None, 42, 3.14, [], {}, b"bytes", True]:
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    location.storage_id(bad)  # type: ignore[arg-type]

    def test_storage_id_empty_valid(self):
        """Empty string is valid: storage_id('') == formula literal, not rejected."""
        self.assertEqual(location.storage_id(""), SID_EMPTY)

    def test_storage_id_distinct_pair(self):
        """Two distinct selectors produce two distinct IDs (no pseudo-collision loop)."""
        self.assertNotEqual(
            location.storage_id("selector-a"), location.storage_id("selector-b")
        )

    def test_storage_id_path_opacity(self):
        """No host path or traversal leaks into the opaque storage ID."""
        sid = location.storage_id("/home/user/runs/../../etc")
        self.assertNotIn("/", sid)
        self.assertNotIn("..", sid)
        self.assertTrue(_SAFE_SEGMENT.match(sid))

    # --- encode_handle -------------------------------------------------------

    def test_encode_handle_exact_token(self):
        """encode_handle produces exactly the pasted canonical er2 token."""
        self.assertEqual(location.encode_handle(CANON_KEY), CANON_TOKEN)

    def test_encode_handle_canonical_structure(self):
        """Sorted compact payload, no padding, full 32-byte SHA-256 checksum."""
        token = location.encode_handle(CANON_KEY)
        _, p_b64, c_b64 = token.split(":")
        payload = json.loads(_b64decode(p_b64))
        self.assertEqual(list(payload.keys()), ["g", "p", "s"])  # sorted
        raw = _b64decode(p_b64).decode("utf-8")
        self.assertNotIn(" ", raw)
        self.assertNotIn("\n", raw)
        self.assertNotIn("=", p_b64)
        self.assertNotIn("=", c_b64)
        self.assertEqual(_b64decode(c_b64), hashlib.sha256(_b64decode(p_b64)).digest())
        self.assertEqual(len(_b64decode(c_b64)), 32)

    def test_encode_handle_non_runkey_raises_type_error(self):
        for bad in [None, 42, "string", [], {}]:
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    location.encode_handle(bad)  # type: ignore[arg-type]

    def test_encode_handle_invalid_fields_raise_value_error(self):
        for label, key in [
            ("bool_p", RunKey(True, SID_BETA, 7)),  # type: ignore[arg-type]
            ("bool_s", RunKey(SID_ALPHA, True, 7)),  # type: ignore[arg-type]
            ("bool_g", RunKey(SID_ALPHA, SID_BETA, True)),  # type: ignore[arg-type]
            ("zero_g", RunKey(SID_ALPHA, SID_BETA, 0)),
            ("neg_g", RunKey(SID_ALPHA, SID_BETA, -1)),
            ("num_p", RunKey(42, SID_BETA, 7)),  # type: ignore[arg-type]
            ("list_s", RunKey(SID_ALPHA, [], 7)),  # type: ignore[arg-type]
            ("lower_p", RunKey("not-a-storage-id", SID_BETA, 7)),
            ("upper_s", RunKey(SID_ALPHA, "s256-" + "A" * 64, 7)),
            ("empty_p", RunKey("", SID_BETA, 7)),
            ("empty_s", RunKey(SID_ALPHA, "", 7)),
            ("float_g", RunKey(SID_ALPHA, SID_BETA, 1.0)),  # type: ignore[arg-type]
        ]:
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    location.encode_handle(key)

    # --- decode_handle -------------------------------------------------------

    def test_decode_handle_roundtrip(self):
        for label, key in [
            ("canon", CANON_KEY),
            ("unicode", RunKey(SID_UNICODE, SID_BETA, 1)),
            ("large_gen", RunKey(SID_ALPHA, SID_BETA, 999999)),
        ]:
            with self.subTest(label):
                decoded = location.decode_handle(location.encode_handle(key))
                self.assertEqual(decoded, key)
                assert decoded is not None
                self.assertTrue(_SAFE_SEGMENT.match(decoded.project_id))
                self.assertTrue(_SAFE_SEGMENT.match(decoded.run_id))

    def test_decode_handle_never_raises(self):
        """Representative arbitrary inputs return None, never raise."""
        for bad in ["", "er2", "er2:", "er2::", ":payload:checksum", "er2:payload",
                    "er2:payload:checksum:extra", "not-a-token", "er2:!!!:???",
                    None, 42, 3.14, [], {}, object(), b"bytes", True]:
            with self.subTest(bad=bad):
                self.assertIsNone(location.decode_handle(bad))  # type: ignore[arg-type]

    def test_decode_handle_tamper(self):
        """Flipped payload or checksum byte returns None."""
        for label, token in [
            ("flip_payload", _flip_part(CANON_TOKEN, 1, 0)),
            ("flip_checksum", _flip_part(CANON_TOKEN, 2, 0)),
        ]:
            with self.subTest(label):
                self.assertIsNone(location.decode_handle(token))

    def test_decode_handle_schema_negatives(self):
        """Valid-checksum tokens with non-canonical or schema-invalid payloads → None."""
        for label, token in BAD_TOKENS.items():
            with self.subTest(label):
                self.assertIsNone(location.decode_handle(token))

    def test_decode_handle_strict_base64url(self):
        """Injected non-base64url chars, padding, impossible length → None."""
        bang_pre_p, bang_suf_p = _insert_edge(CANON_TOKEN, 1, "!")
        bang_pre_c, bang_suf_c = _insert_edge(CANON_TOKEN, 2, "!")
        for label, token in [
            ("payload_bang", _inject(CANON_TOKEN, 1, "!")),
            ("payload_plus", _inject(CANON_TOKEN, 1, "+")),
            ("payload_slash", _inject(CANON_TOKEN, 1, "/")),
            ("payload_pad", _inject(CANON_TOKEN, 1, "=")),
            ("checksum_bang", _inject(CANON_TOKEN, 2, "!")),
            ("checksum_plus", _inject(CANON_TOKEN, 2, "+")),
            ("checksum_slash", _inject(CANON_TOKEN, 2, "/")),
            ("checksum_pad", _inject(CANON_TOKEN, 2, "=")),
            ("payload_bang_prefix", bang_pre_p),
            ("payload_bang_suffix", bang_suf_p),
            ("checksum_bang_prefix", bang_pre_c),
            ("checksum_bang_suffix", bang_suf_c),
            ("payload_len1", "er2:A:" + CANON_TOKEN.split(":")[2]),
            ("checksum_len1", "er2:" + CANON_TOKEN.split(":")[1] + ":A"),
        ]:
            with self.subTest(label):
                self.assertIsNone(location.decode_handle(token))

    def test_decode_handle_legacy_unknown_prefix(self):
        for label, token in [
            ("unknown_prefix", "v1:" + ":".join(CANON_TOKEN.split(":")[1:])),
            ("legacy_v1", "v1:oldformat:somedata"),
            ("extra_part", CANON_TOKEN + ":extra"),
        ]:
            with self.subTest(label):
                self.assertIsNone(location.decode_handle(token))

    def test_decode_handle_str_subclass_returns_none(self):
        """A ``str`` subclass is rejected by ``type(token) is str`` before any method call."""
        self.assertIsNone(location.decode_handle(ExplodingStr(CANON_TOKEN)))

    def test_decode_handle_pad_bit_aliases(self):
        """Nonzero pad-bit aliases decode to the same bytes but are rejected by canonical
        segment roundtrip (``_b64url_nopad(decoded) == original``)."""
        for label, token in [
            ("payload_alias_g10", PAD_ALIAS_PAYLOAD),
            ("checksum_alias_w_to_x", PAD_ALIAS_CHECKSUM),
        ]:
            with self.subTest(label):
                self.assertIsNone(location.decode_handle(token))


if __name__ == "__main__":
    unittest.main(verbosity=2)
