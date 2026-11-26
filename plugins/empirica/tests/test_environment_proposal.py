#!/usr/bin/env python3
"""Empirica 7.2 environment proposal: pure admission, canonicalization, digest and profile ceiling."""
from __future__ import annotations

import unittest

from core import environment as subject
from core import spike_plan


def _proposal(**changes):
    body = {
        "profile_id": spike_plan.ISOLATED_PROFILE,
        "spike_images": ["python:3.12-slim"],
        "preparation": ["docker pull python:3.12-slim"],
        "rationale": "One local image is enough for the planned single-container spike.",
    }
    body.update(changes)
    return body


def _without(key):
    body = _proposal()
    del body[key]
    return body


def _images(count: int, first: object = "python:3.12-slim"):
    return [first if index == 0 else f"python:3.12.{index}" for index in range(count)]


class EnvironmentProposalSchemaTests(unittest.TestCase):
    def test_schema_constant_is_pinned(self):
        self.assertEqual(subject.PROPOSAL_SCHEMA, "empirica.environment-proposal/1")

    def test_bounds_constants_are_pinned(self):
        self.assertEqual(subject.MAX_SPIKE_IMAGES, 16)
        self.assertEqual(subject.MAX_PREPARATION_STEPS, 8)
        self.assertEqual(subject.MAX_PREPARATION_CHARS, 200)
        self.assertEqual(subject.MAX_RATIONALE_CHARS, 600)

    def test_valid_proposal_passes_as_given(self):
        body = _proposal()
        self.assertEqual(subject.validate_proposal(body), body)

    def test_body_is_closed(self):
        malformed = {
            "empty": {}, "extra key": _proposal(extra=True),
            **{f"missing {key}": _without(key) for key in _proposal()},
            "array": [], "null": None, "text": "environment", "number": 7, "boolean": True,
        }
        for name, bad in malformed.items():
            with self.subTest(name=name), self.assertRaises(subject.EnvironmentProposalError):
                subject.validate_proposal(bad)

    def test_profile_must_be_a_framework_profile(self):
        for bad in ("docker-rust-v1", "", "docker-isolated", None, 1, True, ["docker-isolated-v1"]):
            with self.subTest(bad=bad), self.assertRaises(subject.EnvironmentProposalError):
                subject.validate_proposal(_proposal(profile_id=bad))
        self.assertEqual(
            {subject.validate_proposal(_proposal(profile_id=profile))["profile_id"]
             for profile in spike_plan.NETWORK_PROFILES},
            set(spike_plan.NETWORK_PROFILES))

    def test_error_is_a_value_error_with_a_closed_message(self):
        self.assertTrue(issubclass(subject.EnvironmentProposalError, ValueError))
        for bad in (None, "text", []):
            with self.assertRaises(ValueError) as caught:
                subject.validate_proposal(bad)
            message = str(caught.exception)
            self.assertTrue(message, message)
            self.assertNotIn("Traceback", message)
            self.assertEqual(message, message.rstrip())


class ClosedMessageTests(unittest.TestCase):
    """Every refusal names its one short closed message; nothing else escapes the module."""

    def test_every_refusal_carries_its_closed_message(self):
        cases = {
            "body is not closed": ("not a proposal", None, "environment proposal shape is not closed"),
            "profile is unknown": (_proposal, {"profile_id": "docker-open-v1"},
                                    "environment proposal profile is unknown"),
            "images out of bounds": (_proposal, {"spike_images": []},
                                     "environment proposal spike images must be a bounded nonempty array"),
            "image name is malformed": (_proposal, {"spike_images": ["python:"]},
                                          "environment proposal spike image is malformed"),
            "images are duplicated": (_proposal, {"spike_images": ["a:1", "a:1"]},
                                      "environment proposal spike images are duplicated"),
            "preparation out of bounds": (_proposal, {"preparation": []},
                                           "environment proposal preparation must be a bounded nonempty array"),
            "text is not a string": (_proposal, {"rationale": 7},
                                    "environment proposal text must be a string"),
            "text carries controls": (_proposal, {"preparation": ["\x1b[31m"]},
                                      "environment proposal text carries control characters"),
            "text exceeds its bound": (_proposal, {"rationale": "r" * 601},
                                       "environment proposal text exceeds its character bound"),
            "preparation step is blank": (_proposal, {"preparation": [" "]},
                                          "environment proposal preparation step is blank"),
            "rationale is blank": (_proposal, {"rationale": " "},
                                  "environment proposal rationale is blank"),
            "text is not UTF-8": (_proposal, {"rationale": "\ud800"},
                                  "environment proposal text must be UTF-8"),
        }
        for name, (base, changes, message) in cases.items():
            with self.subTest(name=name), self.assertRaises(subject.EnvironmentProposalError) as caught:
                subject.validate_proposal(base(**changes) if changes else base)  # type: ignore[operator]
            self.assertEqual(str(caught.exception), message)
        for name, bad, message in (
            ("history is not a sequence", None, "environment proposal history must be a sequence of revisions"),
            ("history is malformed", [True], "environment proposal history is malformed"),
        ):
            with self.subTest(name=name), self.assertRaises(subject.EnvironmentProposalError) as caught:
                subject.next_revision(bad)
            self.assertEqual(str(caught.exception), message)


class SpikeImagesTests(unittest.TestCase):
    def test_bounds_at_and_beyond_the_limit(self):
        self.assertEqual(len(subject.validate_proposal(_proposal(spike_images=_images(1)))["spike_images"]), 1)
        self.assertEqual(len(subject.validate_proposal(_proposal(spike_images=_images(16)))["spike_images"]), 16)
        malformed = {
            "empty": [], "over the limit": _images(17), "wholly copied": ["a:1", "a:1"],
            "not an array": "python:3.12-slim", "null": None, "integer": 3,
        }
        for name, bad in malformed.items():
            with self.subTest(name=name), self.assertRaises(subject.EnvironmentProposalError):
                subject.validate_proposal(_proposal(spike_images=bad))

    def test_image_syntax_is_the_spike_plan_grammar(self):
        with self.assertRaises(subject.EnvironmentProposalError) as caught:
            subject.validate_proposal(_proposal(spike_images=["Python:3.12"]))
        self.assertIs(type(caught.exception), subject.EnvironmentProposalError)
        self.assertNotIn("Python", str(caught.exception))
        for bad in ("", "a b", "python:", "%", "/", 7, True, None):
            with self.subTest(bad=bad), self.assertRaises(subject.EnvironmentProposalError):
                subject.validate_proposal(_proposal(spike_images=[bad]))
        for form in ("python:3.12-slim", "sha256:" + "0" * 64,
                     "example/probe@sha256:" + "0" * 64, "myregistry:5000/app/web"):
            self.assertEqual(subject.validate_proposal(_proposal(spike_images=[form]))["spike_images"], [form])

    def test_image_reference_byte_bound_is_the_spike_plan_limit(self):
        at_limit = "a" * 253 + ":t"  # 255 ASCII characters, the spike draft image byte bound
        self.assertEqual(subject.validate_proposal(_proposal(spike_images=[at_limit]))["spike_images"], [at_limit])
        with self.assertRaises(subject.EnvironmentProposalError):
            subject.validate_proposal(_proposal(spike_images=["a" * 254 + ":t"]))  # 256 characters

    def test_duplicate_images_are_refused_after_syntax(self):
        with self.assertRaises(subject.EnvironmentProposalError) as caught:
            subject.validate_proposal(_proposal(spike_images=["python:3.12-slim", "python:3.12-slim"]))
        self.assertEqual(str(caught.exception), "environment proposal spike images are duplicated")



class PreparationTests(unittest.TestCase):
    def test_bounds_at_and_beyond_the_limit(self):
        self.assertEqual(subject.validate_proposal(_proposal(preparation=["x"]))["preparation"], ["x"])
        self.assertEqual(len(subject.validate_proposal(_proposal(preparation=["x"] * 8))["preparation"]), 8)
        malformed = {
            "empty": [], "over the limit": ["x"] * 9, "not an array": "x",
            "null": None, "integer": 3, "with a hole": ["x", None],
        }
        for name, bad in malformed.items():
            with self.subTest(name=name), self.assertRaises(subject.EnvironmentProposalError):
                subject.validate_proposal(_proposal(preparation=bad))

    def test_steps_must_be_nonblank_bounded_text(self):
        at_limit = "x" * 200
        self.assertEqual(subject.validate_proposal(_proposal(preparation=[at_limit]))["preparation"], [at_limit])
        malformed = {
            "empty string": "", "whitespace only": "  \t ", "null": None, "integer": 7, "boolean": True,
            "newline": "line\nbreak", "escape": "color\x1b[31m", "delete": "del\x7f", "nul": "nul\x00", "c1": "\x85",
            "over the limit": "x" * 201, "lone surrogate": "\ud800",
        }
        for name, bad in malformed.items():
            with self.subTest(name=name), self.assertRaises(subject.EnvironmentProposalError):
                subject.validate_proposal(_proposal(preparation=[bad]))

    def test_plain_text_passes_through_unchanged(self):
        for good in ("docker pull", "a plain sentence", "  padded  ", "unicode libre: café ok"):
            self.assertEqual(subject.validate_proposal(_proposal(preparation=[good]))["preparation"], [good])


class RationaleTests(unittest.TestCase):
    def test_bounds_at_and_beyond_the_limit(self):
        at_limit = "r" * 600
        self.assertEqual(subject.validate_proposal(_proposal(rationale=at_limit))["rationale"], at_limit)
        malformed = {
            "empty": "", "whitespace only": "   \t  ",
            "over the limit": "r" * 601,
            "null": None, "integer": 7, "boolean": True, "array": ["reason"],
            "newline": "multi\nline", "tab": "tab\tchar", "c1": "\u0085 then a space",
            "lone surrogate": "\ud800 reason",
        }
        for name, bad in malformed.items():
            with self.subTest(name=name), self.assertRaises(subject.EnvironmentProposalError):
                subject.validate_proposal(_proposal(rationale=bad))


class CanonicalAndDigestTests(unittest.TestCase):
    def test_canonical_form_sorts_images_and_keeps_strings_as_given(self):
        body = _proposal(spike_images=["b/alpine:3.22", "a/python:3.12"], preparation=[" obey  spaces "],
                         rationale=" why ")
        canonical = subject.canonical_proposal(body)
        self.assertEqual(canonical["spike_images"], ["a/python:3.12", "b/alpine:3.22"])
        self.assertEqual(canonical["preparation"], [" obey  spaces "])
        self.assertEqual(canonical["rationale"], " why ")
        self.assertEqual(set(canonical), set(_proposal()))
        self.assertEqual(subject.canonical_proposal(canonical), canonical)

    def test_digest_is_stable_under_list_and_key_reordering(self):
        first = subject.proposal_digest(_proposal(spike_images=["z/alpine:3.22", "a/python:3.12"]))
        second = subject.proposal_digest(_proposal(spike_images=["a/python:3.12", "z/alpine:3.22"]))
        self.assertEqual(first, second)
        reordered_keys = {key: value for key, value in reversed(list(_proposal().items()))}
        self.assertEqual(subject.proposal_digest(reordered_keys), subject.proposal_digest(_proposal()))

    def test_digest_shape_is_sha256(self):
        self.assertRegex(subject.proposal_digest(_proposal()), r"^sha256:[0-9a-f]{64}$")

    def test_digest_content_is_pinned(self):
        # The exact content address of one fixed canonical proposal: body fields and sorted images are all part
        # of it. Spec §4.1 (as amended for E1) specifies the digest preimage as the canonical body carrying the
        # schema member for domain separation — `{'schema': PROPOSAL_SCHEMA, **canonical}` — so the address is
        # versioned by format identity and none of its members can drift silently.
        self.assertEqual(
            subject.proposal_digest(_proposal()),
            "sha256:32ed6670191a3545b136775da8226e39c46cc6b5652112fd15eea35e599f26b8")

    def test_digest_changes_on_every_field_change(self):
        base = subject.proposal_digest(_proposal())
        changed = {
            "profile": _proposal(profile_id=spike_plan.NETWORKED_PROFILE),
            "image": _proposal(spike_images=["python:3.12-alpine"]),
            "image added": _proposal(spike_images=["python:3.12-slim", "alpine:3.22"]),
            "image removed": _proposal(spike_images=["alpine:3.22"]),
            "preparation": _proposal(preparation=["docker pull python:3.13-slim"]),
            "preparation added": _proposal(
                preparation=["docker pull python:3.12-slim", "also prepare the other image"]),
            "rationale": _proposal(rationale="A different rationale text."),
        }
        for name, body in changed.items():
            with self.subTest(name=name):
                self.assertNotEqual(subject.proposal_digest(body), base)
        self.assertEqual(subject.proposal_digest(_proposal()), base)

    def test_every_admitted_body_is_digestible(self):
        for good in (_proposal(), _proposal(rationale="unicode libre : café ok"),
                     _proposal(preparation=["tirer l'image de la plateforme"],
                               spike_images=["a" * 253 + ":t"])):
            with self.subTest(good=good):
                self.assertRegex(subject.proposal_digest(good), r"^sha256:[0-9a-f]{64}$")

    def test_validation_is_idempotent(self):
        body = _proposal()
        once = subject.validate_proposal(body)
        self.assertEqual(subject.validate_proposal(once), once)
        self.assertEqual(subject.canonical_proposal(once), subject.canonical_proposal(body))
        self.assertEqual(subject.proposal_digest(once), subject.proposal_digest(body))


class ProfileCeilingTests(unittest.TestCase):
    def test_ceiling_table(self):
        isolated = spike_plan.ISOLATED_PROFILE
        networked = spike_plan.NETWORKED_PROFILE
        cases = [
            (isolated, None, True), (networked, None, False),
            (isolated, isolated, True), (networked, isolated, False),
            (isolated, networked, True), (networked, networked, True),
            ("docker-rust-v1", None, False), ("docker-rust-v1", networked, False),
            (isolated, "docker-rust-v1", False), (networked, "docker-rust-v1", False),
            (None, None, False), (True, networked, False), (1, networked, False),
            (isolated, True, False), (isolated, 1, False), (isolated, ["docker-networked-v1"], False),
        ]
        for profile_id, ceiling, expected in cases:
            with self.subTest(profile_id=profile_id, ceiling=ceiling):
                self.assertIs(subject.profile_allowed(profile_id, ceiling), expected)


class NextRevisionTests(unittest.TestCase):
    def test_monotonic_from_one(self):
        self.assertEqual(subject.next_revision([]), 1)
        self.assertEqual(subject.next_revision(()), 1)
        self.assertEqual(subject.next_revision([1]), 2)
        self.assertEqual(subject.next_revision([1, 2, 3, 7]), 8)
        self.assertEqual(subject.next_revision([3, 1]), 4)
        self.assertEqual(subject.next_revision((9,)), 10)
        self.assertEqual(subject.next_revision([2, 2]), 3)

    def test_history_must_be_positive_integer_revisions(self):
        malformed = (None, 3, "1", [0], [-1], [1.0], [True], [1, "2"], [None], [[1]])
        for bad in malformed:
            with self.subTest(bad=bad), self.assertRaises(subject.EnvironmentProposalError):
                subject.next_revision(bad)

    def test_history_is_not_a_mapped_body(self):
        with self.assertRaises(subject.EnvironmentProposalError):
            subject.next_revision({"1": 1})


if __name__ == "__main__":
    unittest.main()
