#!/usr/bin/env python3
"""Generated operational-state projection conformance."""
from __future__ import annotations

import itertools
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema  # noqa: E402
from adapters.author_view import render_author_view  # noqa: E402
from application import protocol  # noqa: E402
from application.run_state import classify_and_decode, encode_state  # noqa: E402
from core.projection import project_argument, project_runview  # noqa: E402
from test_d7_transactions import projection_snapshot  # noqa: E402

_STATE = protocol.state_schema()
_CHILD = _STATE["$defs"]["childRecord"]["properties"]
STATUSES = tuple(protocol.public_contract()["statuses"])
CHILD_STATES = tuple(_CHILD["state"]["enum"])
RESOURCE_CLASSES = tuple(_CHILD["resource_class"]["enum"])
GOVERNANCE_STATES = tuple(_STATE["properties"]["governance"]["properties"]["state"]["enum"])
CONTROL_MODES = tuple(_STATE["properties"]["governance"]["properties"]["control_mode"]["enum"])
DEADLINES = (None, 1735689600.0)
BUDGET_MODES = ("available", "exhausted")
SCOPE_MODES = ("open", "frozen", "deferred")
DIGEST = "sha256:" + "a" * 64

RUN_VIEW = jsonschema.validators.validator_for(protocol.response_schema())(
    {key: protocol.response_schema()[key] for key in ("$schema", "$id", "$defs")}
    | {"$ref": "#/$defs/runView"}, registry=protocol.schema_registry())
RESPONSE = jsonschema.validators.validator_for(protocol.response_schema())(
    protocol.response_schema(), registry=protocol.schema_registry())


def _child(state: str, resource_class: str, deadline: float | None,
           argument: dict, index: int) -> dict:
    """Construct the schema-coherent record for one enum-derived child state."""
    launch_rejected = state == "launch_rejected"
    started = state != "reserved" and not launch_rejected
    terminal = state in {"completed", "failed", "cancelled", "timed_out", "orphaned"}
    audit = resource_class == "audit"
    return {
        "child_id": f"child-{index}", "purpose": "audit" if audit else "investigation",
        "resource_class": resource_class, "state": state,
        "spent": started, "refunded": launch_rejected, "deadline": deadline,
        "native_id": f"native-{index}" if started else None,
        "first_terminal_fingerprint": DIGEST if terminal or launch_rejected else None,
        "capability_ref": f"capability-{index}",
        "audit_operation_id": argument["argument_digest"] if audit else None,
        "audit_argument": argument if audit else None,
        "audit_role_profile": "empirica:empirica-auditor" if audit else None,
    }


def _child_variants(argument: dict) -> tuple[tuple[str, tuple[dict, ...]], ...]:
    """Derive empty, every singleton enum combination, and one all-state mixed list."""
    singles = tuple(
        (f"one:{state}:{resource}:{'deadline' if deadline else 'no-deadline'}",
         (_child(state, resource, deadline, argument, index),))
        for index, (state, resource, deadline) in enumerate(
            itertools.product(CHILD_STATES, RESOURCE_CLASSES, DEADLINES), start=1)
    )
    mixed = tuple(
        _child(state, "audit" if state == "completed" else "investigation",
               DEADLINES[index % len(DEADLINES)], argument, 100 + index)
        for index, state in enumerate(CHILD_STATES)
    )
    return (("empty", ()), *singles, ("mixed:all-states", mixed))


def _pairwise(dimensions: tuple[tuple[object, ...], ...]) -> tuple[tuple[object, ...], ...]:
    """Greedily select a deterministic pairwise cover from the finite Cartesian product."""
    candidates = tuple(itertools.product(*(range(len(dimension)) for dimension in dimensions)))
    uncovered = {
        (left, row[left], right, row[right])
        for row in candidates
        for left in range(len(dimensions))
        for right in range(left + 1, len(dimensions))
    }
    selected = []
    while uncovered:
        row = max(candidates, key=lambda candidate: sum(
            (left, candidate[left], right, candidate[right]) in uncovered
            for left in range(len(dimensions)) for right in range(left + 1, len(dimensions))))
        selected.append(tuple(dimensions[index][value] for index, value in enumerate(row)))
        uncovered.difference_update(
            (left, row[left], right, row[right])
            for left in range(len(dimensions)) for right in range(left + 1, len(dimensions)))
    return tuple(selected)


def _governance(base: dict, phase: str, mode: str, budgets: dict) -> dict:
    """Make each schema-enumerated governance phase coherent with its control mode and budgets."""
    value = {**base, "state": phase, "control_mode": mode,
             "proposal": {"budgets": {key: budgets[key] for key in (
                 "max_passes", "max_spawns", "max_audit_spawns")}}, "receipts": []}
    if phase != "approved":
        return {**value, "approved_digest": None, "approval_kind": None}
    receipt = {
        "id": "projection-conformance", "fingerprint": DIGEST,
        "presentation_fingerprint": None if mode == "auto" else "sha256:" + "b" * 64,
        "outcome": "approve", "plan_revision": value["plan_revision"],
    }
    return {**value, "approved_digest": value["proposal_digest"],
            "approval_kind": "auto" if mode == "auto" else "host_ui",
            "receipts": [receipt]}


class ProjectionConformanceTests(unittest.TestCase):
    """Validate 190 pairwise cases (including every status × child-variant pair)."""

    def test_contract_declared_audit_obligation_conforms(self):
        """The generated projection suite covers the contract-owned audit row."""
        declaration = protocol.public_contract()["bootstrap"]["audit_obligation"]
        _, snapshot = projection_snapshot(approved=True)
        run = project_runview(snapshot)
        RUN_VIEW.validate(run)
        rows = [row for row in run["obligations"]["active"]
                if row["id"] == declaration["obligation_id"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["required"], declaration["must"])
        self.assertEqual(rows[0]["missing"]["code"], "audit.required")

    def test_generated_reachable_states_conform(self):
        fixtures = {
            "open": projection_snapshot(),
            "frozen": projection_snapshot(frozen=True),
            "deferred": projection_snapshot(frozen=True, deferred=True),
        }
        argument = project_argument(fixtures["open"][1])
        child_variants = _child_variants(argument)
        cases = _pairwise((STATUSES, child_variants, GOVERNANCE_STATES, CONTROL_MODES,
                           BUDGET_MODES, SCOPE_MODES))
        self.assertEqual(len(cases), 190)
        rejected = []
        for status, (child_label, children), governance_state, control_mode, budget_mode, scope in cases:
            label = (f"status={status},children={child_label},governance={governance_state},"
                     f"control={control_mode},budget={budget_mode},scope={scope}")
            with self.subTest(case=label):
                coordinator, base = fixtures[scope]
                charged = {
                    resource: sum(child["resource_class"] == resource and not child["refunded"]
                                  for child in children)
                    for resource in RESOURCE_CLASSES
                }
                budgets = {
                    "max_passes": 8, "passes_used": 8 if budget_mode == "exhausted" else 0,
                    "max_spawns": max(1, charged["investigation"]),
                    "spawns_used": charged["investigation"],
                    "max_audit_spawns": max(1, charged["audit"]),
                    "audit_spawns_used": charged["audit"],
                }
                state = replace(
                    base.state, status=status, children=children, budgets=budgets,
                    governance=_governance(base.state.governance, governance_state,
                                           control_mode, budgets))
                encoded = encode_state(state)
                classified = classify_and_decode(encoded)
                if classified.kind != "valid":
                    rejected.append(label)
                    continue
                snapshot = replace(base, state=classified.state)
                run = project_runview(snapshot)
                RUN_VIEW.validate(run)
                envelope = coordinator._allow("projection-conformance", snapshot)
                RESPONSE.validate(envelope)
                rendered = render_author_view(envelope["result"], strict=True)
                self.assertIsInstance(rendered, str)
                self.assertNotEqual(
                    rendered,
                    json.dumps(envelope["result"], sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False))
        self.assertEqual(rejected, [])

    def test_safe_block_views_conform(self):
        """The unreadable-run view is built outside the projection, so validate it separately."""
        coordinator, snapshot = projection_snapshot()
        for code in ("run.corrupt",):
            with self.subTest(code=code):
                envelope = coordinator._safe_block(snapshot.run_id, "safe-block", code)
                RESPONSE.validate(envelope)
                self.assertIn("\nAudit: required\n", render_author_view(envelope["result"], strict=True))


if __name__ == "__main__":
    unittest.main()
