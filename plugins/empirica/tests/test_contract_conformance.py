#!/usr/bin/env python3
"""ADR-0075 contract conformance: the schema and the core agree on every registered definition, in
both directions, differentially, with per-(node, keyword) coverage accounting.

A definition the core validates declares itself in the contract: the document names its core ingress
roots under ``x-core-ingress`` (and every annotated ``$defs`` definition an ingress reaches registers
too), each carrying ``x-core-validator`` — or, for a rule no schema can express, an ``x-core-only``
entry whose ``case`` names exactly the differential candidate kind it excuses, pinned by the one test
it names. This harness is the ADR-0075 half of the shape testing:

* valid documents are drawn from the definition's keywords by ``scripts/contract_strategies.py``
  (the shared T1 engine) and the named validator must accept each;
* challenge candidates are derived from the same keywords *independently of either verdict* — neither
  verdict is consulted to keep or drop a candidate — and every candidate is adjudicated: Draft 2020-12
  and the named core validator must agree, refuse-refuse or accept-accept, except where the declared
  open-disagreement registry names the pair (it is empty at this head — the run/5 digest anchors it
  once carried are absolutely anchored now, their entries removed) or an ``x-core-only`` rule on the
  challenged node names exactly the candidate kind.
  A ``TypeError`` or any other non-domain exception a validator throws is a harness failure, never a
  refusal: an accidentally-crashing core is caught, not banked as coverage;
* coverage is accounted per (node, keyword): every plan entry needs a both-refuse witness, or an
  explicit unsupported/redundant declaration — one firing mutation is never taken for coverage;
* acceptance directions are derived too: exact-boundary documents (sizes, lengths, numeric extrema)
  and a pattern's own grammar witnesses — one per top-level alternative and per present/absent optional
  group, the spike-image lexeme's branches included — must be accepted by both sides.

Every Hypothesis run is bounded, derandomized, fixed-seeded and database-free (doc/testing.md, T1);
the deterministic sweeps run on one canonical full-drawn base each. Registration is by declaration:
an ingress declaration with its annotations lands here with no harness change, so later nodes add
theirs in the contract alone. A shapes hand-written test would violate the operator rule; behaviour
lives in the other modules.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
sys.path[:0] = [str(HERE), str(HERE.parent), str(REPOSITORY / "scripts")]

from hypothesis import HealthCheck, find, given, settings  # noqa: E402

import contract_strategies as engine  # noqa: E402
from contract_strategies import KNOWN_OPEN, Contract  # noqa: E402

_CONTRACT = Contract()
if _CONTRACT.findings:
    raise ImportError("contract annotations are malformed: " + "; ".join(_CONTRACT.findings))
_KEYS = sorted(_CONTRACT.registered)


def unrerandomized(maximum: int) -> settings:
    return settings(max_examples=maximum, deadline=None, database=None, derandomize=True)


_FIND = settings(max_examples=128, database=None, deadline=None, derandomize=True,
                 suppress_health_check=list(HealthCheck))
_PROPERTY = unrerandomized(3)
_FUZZ = unrerandomized(2)


#: The generated method names keep the document identity (the state definition's methods and the
#: request definition's never overwrite one another) and refuse a collision: two registered keys that
#: normalize alike raise instead of silently merging - the re-review's finding was the request
#: definition's methods disappearing under the state definition's name.
class _Base(unittest.TestCase):
    _bases: dict[str, object] = {}

    @classmethod
    def canonical(cls, key: str):
        """One deterministic base per key: a fully-drawn document (every optional member present, every
        array non-empty, the null alternative never taken) — the coverage sweeps run on it, not on a
        sample, so every declared node carries a value the plan can reach."""
        if key not in cls._bases:
            strategy = _CONTRACT.drawn(key, full=True)
            cls._bases[key] = find(strategy, lambda document: True, settings=_FIND)
        return cls._bases[key]

    def assertAccepted(self, key: str, value: object, label: str = "") -> None:
        self.assertTrue(_CONTRACT.accepts(key, value),
                        f"a valid {key} document the schema itself refuses ({label})")
        try:
            _CONTRACT.import_validator(key)(value)
        except ValueError as exc:
            raise self.failureException(f"{key}: the named validator refused a valid document "
                                        f"({label}): {exc}") from exc

    def assertRefused(self, key: str, value: object, label: str) -> None:
        try:
            _CONTRACT.import_validator(key)(value)
        except ValueError:
            return
        raise self.failureException(
            f"{key}: the named validator accepted a challenge it must refuse — {label}: {value!r}")

    def sweep(self, key: str, base) -> list[str]:
        """One differential sweep's harness failures: every derived candidate (plan and witness rounds
        alike) adjudicated with no drops, the failures collected."""
        failures = []
        for case in _CONTRACT.cases(key):
            for derived in _CONTRACT.derive(base, key, case):
                status = _CONTRACT.adjudicate(key, derived)
                if status.startswith(("DISAGREEMENT", "NON-DOMAIN")):
                    failures.append(f"{key}: {status}")
        for derived in _CONTRACT.witness_challenges(key, base):
            status = _CONTRACT.adjudicate(key, derived)
            if status.startswith(("DISAGREEMENT", "NON-DOMAIN")):
                failures.append(f"{key}: {status}")
        return failures


class ValidDrawTests(_Base):
    """Every drawn valid document is accepted by both sides: the schema it was drawn from and the core
    validator the definition names — an agreement in the accepting direction."""


class ValidFullDrawTests(_Base):
    """The reachability sweep: every optional member present, every array non-empty, the null alternative
    never taken — one document reaches every declared node."""


class DifferentialSweepTests(_Base):
    """The canonical differential sweep: every derived challenge candidate adjudicated against both
    judges with no drops. The declared open-disagreement registry's entries (when there are any)
    surface as ``open disagreement`` statuses here and stay pinned by the
    KnownOpenTests control below."""


class DifferentialFuzzTests(_Base):
    """The same sweep on freshly drawn bases — a different document shape each example — so the
    agreement is not one document's accident."""


class CoverageAccountingTests(_Base):
    """Coverage is per (node, keyword), never a firing count: every plan entry needs a candidate both
    sides refuse, or the entry is explicitly declared unsupported or redundant — and the ledger records
    a declared (node, keyword) pair without ever crediting it with coverage, while a plan entry whose
    candidates both sides accept is an acceptance, reported, never silently dropped."""


class BoundaryAcceptanceTests(_Base):
    """The boundary documents of the definitions' own extrema — arrays grown to maxItems and cut to
    minItems, strings padded to maxLength and trimmed to minLength, numbers set to minimum/maximum —
    accepted by both sides: sampling never promises to reach a bound, so a validator that narrowed one
    only held through nobody visiting. A definition with no such extremum yields no boundary document,
    which is a fact, not a failure."""


class RegressionWitnessTests(_Base):
    """The contract's named regression-witness corpus - known-defect values (the exact spellings a
    deleted hand-written test once held: the review's `Python:3.12` seed died only here), each placed
    in a drawn base and refused by BOTH judges. A spelling defect that normalizes one input in the core
    is invisible to the generic probes and lives on only as a named case; the corpus is contract-owned
    (`x-regression-witnesses`), never a test-side restatement."""


class GrammarWitnessTests(_Base):
    """The branch witnesses a pattern's own grammar synthesizes — one per top-level alternative and per
    present/absent optional group, with no hand-written form — accepted by both sides, so every accepted
    branch of a grammar (the spike-image lexeme included) is the core's word too."""


_group_NAMED: dict[type, set[str]] = {}


for _pointer in sorted(_CONTRACT.registered):
    def _valid(key: str = _pointer):  # noqa: F811 — one declared harness method per registered definition
        @settings(_PROPERTY)
        @given(document=_CONTRACT.drawn(key))
        def method(self, document) -> None:
            self.assertAccepted(key, document, "drawn")
        return method

    def _valid_full(key: str = _pointer):
        @settings(_PROPERTY)
        @given(document=_CONTRACT.drawn(key, full=True))
        def method(self, document) -> None:
            self.assertAccepted(key, document, "full draw")
        return method

    def _sweep(key: str = _pointer):
        def method(self) -> None:
            self.assertEqual(self.sweep(key, self.canonical(key)), [])
        method.__doc__ = f"The canonical differential sweep of {key}"
        return method

    def _fuzz(key: str = _pointer):
        @settings(_FUZZ)
        @given(base=_CONTRACT.drawn(key, full=True))
        def method(self, base) -> None:
            self.assertEqual(self.sweep(key, base), [])
        method.__doc__ = f"The differential fuzz sweep of {key} on freshly drawn bases"
        return method

    def _coverage(key: str = _pointer):
        def method(self) -> None:
            base = self.canonical(key)
            witnessed: dict[tuple[str, str], set[str]] = {}
            declared: dict[tuple[str, str], str] = {}
            for case in _CONTRACT.cases(key):
                if case.kind in ("unsupported", "redundant"):
                    # the plan declared it: no challengeable value isolates this pair — an exhausted
                    # family (unsupported) or a sibling keyword that refuses every substitute
                    # (redundant) — and the ledger records the declaration without ever crediting it.
                    declared[(case.node_id, case.keyword)] = case.kind
                    continue
                with self.subTest(case=case.label):
                    # the family gate: a const/enum entry's witness must be of its OWN family — a
                    # wrong-family value (an integer where a string constant is challenged) is a type
                    # refusal and must never be credited with covering the const/enum rules. The
                    # isolating requirement is the plan's own word too: a substitute a sibling keyword
                    # refuses is that sibling's coverage, so the plan never offers it as a witness.
                    self.assertTrue(engine.witness_of_family(case),
                                    f"{key}: {case.label}: a wrong-family candidate must never be "
                                    "credited as a const/enum witness")
                for derived in _CONTRACT.derive(base, key, case):
                    status = _CONTRACT.adjudicate(key, derived)
                    self.assertFalse(status.startswith(("DISAGREEMENT", "NON-DOMAIN")), f"{key}: {status}")
                    witnessed.setdefault((derived.node_id, derived.keyword), set()).add(status)
            for derived in _CONTRACT.witness_challenges(key, base):
                status = _CONTRACT.adjudicate(key, derived)
                self.assertFalse(status.startswith(("DISAGREEMENT", "NON-DOMAIN")), f"{key}: {status}")
                witnessed.setdefault((derived.node_id, derived.keyword), set()).add(status)
            for pair, kind in sorted(declared.items()):
                self.assertNotIn(pair, witnessed,
                                f"{key}: the declared {kind} pair {pair} must never be credited "
                                "as covered")
            planned = {(case.node_id, case.keyword): case.kind
                       for case in _CONTRACT.cases(key)
                       if case.kind not in ("unsupported", "redundant")}
            missing = [f"{node} ({word}): no both-refuse witness — {sorted(statuses)}"
                       for (node, word), statuses in sorted(witnessed.items())
                       if "agree-refused" not in statuses and word != "required"]
            missing += [f"{node} ({word}): planned but no candidate fired"
                        for (node, word), kind in sorted(planned.items())
                        if kind not in ("unsupported", "redundant") and (node, word) not in witnessed]
            self.assertEqual(missing, [],
                             f"{key} coverage is incomplete per (node, keyword); the ledger: "
                             f"{ {entry: sorted(statuses) for entry, statuses in sorted(witnessed.items())} }"
                             f" (declared, never covered: {dict(sorted(declared.items()))})")
        method.__doc__ = f"The per-(node, keyword) coverage accounting of {key}"
        return method

    def _boundary(key: str = _pointer):
        def method(self) -> None:
            for derived in _CONTRACT.at_bounds(key, self.canonical(key)):
                self.assertAccepted(key, derived.value, derived.label)
                self.assertEqual(_CONTRACT.adjudicate(key, derived), "agree-accepted",
                                 f"{key}: a boundary document one side refuses: {derived.label}")
        method.__doc__ = f"The exact-boundary acceptance of {key}"
        return method

    def _regressions(key: str = _pointer):
        def method(self) -> None:
            base = self.canonical(key)
            for derived in _CONTRACT.regression_challenges(key, base):
                with self.subTest(label=derived.label):
                    self.assertEqual(_CONTRACT.adjudicate(key, derived), "agree-refused",
                                     f"{key}: a named regression witness one judge admits: "
                                     f"{derived.label}")
        method.__doc__ = f"The named regression witnesses of {key}, refused by both sides"
        return method

    def _witnesses(key: str = _pointer):
        def method(self) -> None:
            base = self.canonical(key)
            for path, node, node_id in _CONTRACT._targets(key):
                for pattern in _CONTRACT._patterns(node):
                    witnesses = engine.pattern_witnesses(pattern)
                    self.assertGreaterEqual(
                        len(witnesses), 2 * engine.pattern_alternatives(pattern),
                        f"{node_id}: a grammar with {engine.pattern_alternatives(pattern)} top-level "
                        f"alternative(s) must enumerate its reachable branch witnesses")
                    for witness in witnesses:
                        self.assertAccepted(key, _CONTRACT._placed(base, path, witness),
                                            f"the {node_id} grammar witness {witness!r}")
        method.__doc__ = f"The grammar-branch witness acceptance of {key}"
        return method

    # the loop's group name never joins the module's names: unittest would otherwise run one
    # generated group twice under its class name and the loop variable's alias alike.
    for suite_group, builder in ((ValidDrawTests, _valid), (ValidFullDrawTests, _valid_full),
                                 (DifferentialSweepTests, _sweep), (DifferentialFuzzTests, _fuzz),
                                 (CoverageAccountingTests, _coverage),
                                 (BoundaryAcceptanceTests, _boundary), (GrammarWitnessTests, _witnesses),
                                 (RegressionWitnessTests, _regressions)):
        setattr(suite_group, f"test_{engine.method_name(_pointer, _group_NAMED.setdefault(suite_group, set()))}",
                builder(_pointer))
del suite_group, builder, _pointer, _valid, _valid_full, _sweep, _fuzz, _coverage, _boundary, \
    _witnesses, _regressions  # the loop's names never leak as a second alias of a generated group


class KnownOpenTests(_Base):
    """The declared open disagreements: at this head there are none. The registry stays the only
    way a schema/core verdict gap may be excused; while the run/5 digest patterns ended in ``$``
    its two entries were pinned to their live mismatch (a trailing line feed passed Draft 2020-12,
    the core refused it), and the pins fired when 07c5f66b re-anchored both digests absolutely —
    the healed anchor is now the control: both judges refuse the trailing line feed together, and
    no verdict gap hides behind an entry."""

    def test_the_registry_is_empty_at_head(self):
        self.assertEqual(KNOWN_OPEN, {}, "every schema/core verdict gap must fail, never be excused; "
                         "a new entry needs its node, its candidate kind, and a pin to its live mismatch")

    def test_the_run5_digest_anchors_refuse_the_trailing_line_feed_together(self):
        """The healed anchor, as a regression: the member digest and the attempt record digest refuse
        a trailing line feed in both judges — the gap that was once the registry's two entries."""
        healed = ("state#/$defs/environmentMember/properties/digest",
                  "state#/$defs/qualificationAttempt/properties/record_digest")
        reached = set()
        for key in _KEYS:
            base = self.canonical(key)
            for case in _CONTRACT.cases(key):
                if case.node_id not in healed or case.kind not in ("lf-suffix", "cr-suffix"):
                    continue
                for derived in _CONTRACT.derive(base, key, case):
                    with self.subTest(node=case.node_id, kind=case.kind, label=derived.label):
                        self.assertEqual(_CONTRACT.adjudicate(key, derived), "agree-refused",
                                         "the digest anchors must refuse the trailing line feed in both "
                                         "judges — or the gap is back and needs a registry entry")
                    reached.add(case.node_id)
        self.assertEqual(reached, set(healed), "both digest anchors were exercised")

    def test_the_registry_names_registered_nodes_only(self):
        targets = {target[2] for key in _KEYS for target in _CONTRACT._targets(key)}
        for node_id in KNOWN_OPEN:
            self.assertIn(node_id, targets, "a registry entry that no registered definition reaches")


class CoreOnlyRulesTests(_Base):
    """The core-only rules the contract's ``x-core-only`` entries name with their one pinning test:
    rules no schema can express, with the differential excusing exactly the candidate kind each
    entry declares (the coverage sweep proves the same pairs excused, never more)."""

    def test_json_floats_with_a_zeroed_fraction_are_not_python_ints(self):
        """The core-only rule the contract declares for its integer members (``x-core-only`` entries
        with ``case: float-for-int``): a JSON number with a zeroed fraction is an integer to draft
        2020-12 and must not be one to the core — the member, the history rows and the attempt rows
        alike, and (E6c) the response projections' own integers: the environment revision and image
        count, the attempts used and the installed binding's sequence."""
        member = self.canonical("state#/$defs/environmentMember")
        attempt = self.canonical("state#/$defs/qualificationAttempt")
        for key, document, terminals in (
                ("state#/$defs/environmentMember", member, ("revision",)),
                ("state#/properties/environment_history", [copy.deepcopy(member)], ("revision",)),
                ("state#/$defs/qualificationAttempt", attempt, ("revision", "started_stamp")),
                ("state#/properties/qualification_attempts", [copy.deepcopy(attempt)],
                 ("revision", "started_stamp")),
                ("response#/$defs/environmentView",
                 self.canonical("response#/$defs/environmentView"), ("revision", "spike_image_count")),
                ("response#/$defs/qualificationView",
                 self.canonical("response#/$defs/qualificationView"), ("attempts_used",)),
                ("response#/$defs/qualificationView",
                 {**self.canonical("response#/$defs/qualificationView")},
                 ("active/seq",))):
            for terminal in terminals:
                candidate = copy.deepcopy(document)
                if terminal == "active/seq":
                    # a full draw never takes the null alternative, so the canonical base always
                    # holds an installed binding whose sequence can carry the zeroed fraction.
                    candidate["active"]["seq"] = float(candidate["active"]["seq"])
                else:
                    row = candidate if isinstance(candidate, dict) else candidate[0]
                    row[terminal] = float(row[terminal])  # 1 becomes 1.0: an integer to JSON only
                with self.subTest(key=key, member=terminal):
                    self.assertTrue(_CONTRACT.accepts(key, candidate),
                                    "draft 2020-12 must admit the float — the schema cannot say else")
                    self.assertRefused(key, candidate, f"a zeroed-fraction {terminal}")


if __name__ == "__main__":
    unittest.main()
