"""Schema-validated, deterministic plain-text author view for Empirica results."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NewType

from application.protocol import (
    audit_launch,
    author_view_labels,
    next_action_surfaces,
    response_schema_defs,
    untrusted_delimiters,
    validate_public_result,
)
from core.confirmation import is_terminal
from core.freshness import valid_claim_id
from core.governance import CEILINGS
from core.projection import safe_text

Trusted = NewType("Trusted", str)
Untrusted = NewType("Untrusted", str)
_LABELS = author_view_labels()
_SURFACES = next_action_surfaces()
_DEFS = response_schema_defs()
_DIGEST_DEFS = frozenset({"digest256", "nullableDigest256"})
_CLAIM_ID_DEF = "claimId"
_RAISE_ACTION = "budget.raise"
_NULL = {"type": "null"}


@dataclass(frozen=True)
class TextSafety:
    """Convert author-controlled values to escaped, delimited text."""

    open: str
    close: str

    def untrusted(self, value: object) -> Untrusted:
        escaped = safe_text(value).replace("<", r"\x3c").replace(">", r"\x3e")
        return Untrusted(f"{self.open}{escaped}{self.close}")

    def claim_id(self, value: object) -> Trusted | Untrusted:
        """Render a claim id raw; it is safe by construction only if it matches the pattern."""
        return Trusted(value) if valid_claim_id(value) else self.untrusted(value)


@dataclass(frozen=True)
class Header:
    """First-line result, run, governance, immutable spike binding, public rationale, and host note."""

    result_type: Trusted
    status: Trusted
    governance: Trusted
    run_id: Trusted
    spike_backend: tuple[Trusted, ...]
    proposal_rationale: Untrusted | None
    host_note: Trusted | None = None


@dataclass(frozen=True)
class Reason:
    """One contract-owned reason with typed author parameters."""

    code: Trusted
    message: Trusted
    affected: Trusted | None
    params: tuple[Trusted, ...]
    next_actions: tuple[Trusted, ...]


@dataclass(frozen=True)
class Obligation:
    """One projected obligation with ownership-aware text."""

    obligation_id: Trusted
    required: Trusted | Untrusted | None
    missing: Trusted | None  # code, plus the claim to discharge first when a descendant claim blocks it
    next_actions: tuple[Trusted, ...]


@dataclass(frozen=True)
class Child:
    """One child summary: the host audit label is trusted, an investigation purpose is not."""

    resource_class: Trusted
    purpose: Trusted | Untrusted
    state: Trusted
    recovery: Trusted | None


@dataclass(frozen=True)
class Freshness:
    """One author-controlled path and trusted freshness state."""

    path: Untrusted
    state: Trusted


@dataclass(frozen=True)
class AuditSummary:
    """The audit's latest verdict, its currency, whether a re-audit is pending, and (once settled) the host-observed
    independence classification: the core's four facts, worded by ``audit_text``."""

    verdict: Trusted
    current: bool | None
    pending: bool
    independence: Trusted | None
    findings: tuple[Untrusted, ...]


@dataclass(frozen=True)
class AuthorView:
    """Parsed author view consumed by the declarative renderer."""

    header: Header
    audit: AuditSummary
    reasons: tuple[Reason, ...]
    obligations: tuple[Obligation, ...]
    satisfied: tuple[Trusted, ...]
    residuals: tuple[Reason, ...]
    children: tuple[Child, ...]
    freshness: tuple[Freshness, ...]
    next_actions: tuple[Trusted, ...]
    waiting: tuple[str, ...] = ()


def _fallback(result: Any) -> str:
    """Return sorted compact JSON for non-view and invalid input."""
    try:
        return json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return "null"


def render_surface(action_id: str, launch: str | None = None, audit: bool = False) -> Trusted:
    """Render one contract-owned action surface; an audit's child action is the host's exact auditor launch."""
    if launch and (action_id == "child.spawn_auditor" or (audit and action_id == "child.retry")):
        return Trusted(launch)
    surface = _SURFACES[action_id]
    tool = surface.get("tool")
    if tool == "empirica_observe":
        # a raise is one call among the ones a configuration takes: the contract's note says which this is
        note = f" {_LABELS['budget_raise_note']}" if action_id == _RAISE_ACTION else ""
        return Trusted(f"empirica_observe kind={surface['action']}{note}")
    if tool == "empirica_read":
        return Trusted(f"empirica_read {surface['operation']}")
    if tool == "report_convergence":
        return Trusted(f"report_convergence intent={surface['intent']}")
    return Trusted(f"{surface['owner']}: {surface['operation']}")


class _Launch:
    """Where a view prints the host's auditor launch: once, at its ``home`` heading (the line GetRun shows too: the audit
    obligation, else the stopped audit child's recovery row, else the first place that needs it). Elsewhere it points there."""

    def __init__(self, text: str | None = None, home: str | None = None) -> None:
        self.text, self.home, self.printed = text, home, False

    def at(self, site: str) -> str | None:
        """The launch, or a pointer to it, for a place under the ``site`` heading; ``None`` when the host has no launch."""
        if self.text is None:
            return None
        if self.home is None:  # no home found: print it where it is needed
            return self.text
        if site == self.home and not self.printed:
            self.printed = True
            return self.text
        return f"the auditor launch under {_LABELS[self.home].rstrip(':')}"


def _launches(action: str, audit: bool) -> bool:
    """A spawn of the auditor always; a retry only when it concerns the audit."""
    return action == "child.spawn_auditor" or (audit and action == "child.retry")


def _actions(actions: list[str], launch: _Launch, site: str, audit: bool = False) -> tuple[Trusted, ...]:
    """Render next actions; an audit launch is printed once per view (see ``_Launch``)."""
    return tuple(render_surface(action, launch.at(site) if _launches(action, audit) else None, audit) for action in actions)


def _home(run: dict[str, Any], reasons: list[dict[str, Any]]) -> str | None:
    """The one heading that prints the launch, in the order the view reads: obligation, children, reasons, residuals, Next."""
    def audit_reason(row: dict[str, Any]) -> bool:
        # A `child.terminal` reason is never a home: an audit child that stopped has a Children row, which is.
        return row["code"].startswith("audit.")
    if any(row["id"] == "obligation.audit" and row["status"] != "satisfied"
           and any(_launches(action, True) for action in row["next"]) for row in run["obligations"]["active"]):
        return "open_obligations"
    if any(row["resource_class"] == "audit" and row.get("recovery_action") == "child.retry" for row in run["children"]):
        return "children"
    if any(_launches(action, audit_reason(row)) for row in reasons for action in row["next_actions"]):
        return "reasons"
    if any(_launches(action, audit_reason(row)) for row in run["residuals"] for action in row["next_actions"]):
        return "residuals"
    return "next" if "child.spawn_auditor" in run["next_actions"] else None


def _obligation_id(value: str, safety: TextSafety) -> Trusted:
    """Render a contract id; an embedded claim id is raw only when its suffix is a claim id."""
    if value.startswith("claim:"):
        return Trusted("claim:" + safety.claim_id(value.removeprefix("claim:")))
    return Trusted(value)


ValueRenderer = Callable[[Any, "TextSafety"], str]


def _value_renderer(schema: dict[str, Any]) -> ValueRenderer | None:
    """Derive a value renderer from its response-schema shape; ``None`` drops digests.

    Ownership follows the schema: enums/consts are contract-owned (trusted), claim ids are safe by
    construction (raw), free strings are author-supplied (untrusted), a nullable ``anyOf`` renders
    its one non-null branch, and arrays and objects compose their item/field renderers.
    """
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name == _CLAIM_ID_DEF:
            return lambda value, safety: safety.claim_id(value)
        return None if name in _DIGEST_DEFS else _value_renderer(_DEFS[name])
    if "anyOf" in schema:
        (branch,) = (row for row in schema["anyOf"] if row != _NULL)
        inner = _value_renderer(branch)
        if inner is None:
            return None
        return lambda value, safety: "null" if value is None else inner(value, safety)
    if "enum" in schema or "const" in schema:
        return lambda value, _safety: str(value)
    if schema.get("type") == "string":
        return lambda value, safety: safety.untrusted(value)
    if schema.get("type") == "array":
        item = _value_renderer(schema["items"])
        if item is None:  # elements are intentionally omitted (digests), so the whole array is
            return None
        separator = ", " if _is_string(schema["items"]) else "; "
        return lambda value, safety: separator.join(item(row, safety) for row in value)
    if schema.get("type") == "object":
        fields = tuple((name, _value_renderer(sub))
                       for name, sub in schema["properties"].items())
        return lambda value, safety: ": ".join(
            render(value[name], safety) for name, render in fields
            if render is not None and name in value)
    raise ValueError(f"author view cannot render parameter schema: {schema!r}")


def _is_string(schema: dict[str, Any]) -> bool:
    """Whether a schema (following refs) is a string, which joins with ``", "`` in arrays."""
    if "$ref" in schema:
        return _is_string(_DEFS[schema["$ref"].rsplit("/", 1)[-1]])
    return schema.get("type") == "string"


def _parameter_renderers() -> dict[str, ValueRenderer | None]:
    """One renderer per reason/residual parameter key, derived from every ``*Params`` def.

    A key declared by two parameter shapes must have one schema, so its ownership is
    unambiguous; a new contract parameter is rendered without a code change.
    """
    shapes: dict[str, dict[str, Any]] = {}
    for name, definition in sorted(_DEFS.items()):
        if not name.endswith("Params"):
            continue
        for key, schema in definition.get("properties", {}).items():
            if shapes.setdefault(key, schema) != schema:
                raise ValueError(f"parameter {key!r} has conflicting schemas")
    return {key: _value_renderer(schema) for key, schema in shapes.items()}


_PARAMETERS = _parameter_renderers()


def _parameters(parameters: dict[str, Any], safety: TextSafety) -> tuple[Trusted, ...]:
    """Render reason/residual parameters with schema-derived ownership (digests dropped)."""
    return tuple(
        Trusted(f"{key}={render(parameters[key], safety)}")
        for key in sorted(parameters)
        if (render := _PARAMETERS[key]) is not None)


def _affected(value: dict[str, Any] | None, safety: TextSafety) -> Trusted | None:
    """Render a reason's affected obligation (the schema's only author-relevant field)."""
    if value is None or "obligation_id" not in value:
        return None
    return _obligation_id(value["obligation_id"], safety)


def _residual_claims(row: dict[str, Any], safety: TextSafety) -> Trusted | None:
    """Render the claim a residual blocks and, when redirected, the claim to discharge."""
    if "claim_id" not in row:
        return None
    claim = "claim:" + safety.claim_id(row["claim_id"])
    target = row.get("target_claim_id", row["claim_id"])
    if target == row["claim_id"]:
        return Trusted(claim)
    return Trusted(f"{claim}; {_LABELS['via_claim']}{safety.claim_id(target)}")


def _missing(row: dict[str, Any], safety: TextSafety) -> Trusted:
    """Render an obligation's missing code and, when another claim blocks it, the claim to discharge.

    ``target_claim_id`` is ``None`` for non-claim obligations and equals the obligation's own claim
    id when it is not redirected; only a different target is named (``code; <via_claim label><id>``).
    """
    missing = row["missing"]
    target = missing["target_claim_id"]
    if target is None or target == row["id"].removeprefix("claim:"):
        return Trusted(missing["code"])
    return Trusted(f"{missing['code']}; {_LABELS['via_claim']}{safety.claim_id(target)}")


def _reason(row: dict[str, Any], safety: TextSafety, launch: _Launch = _Launch(), audit: bool = False,
            site: str = "reasons") -> Reason:
    """Parse one schema-valid reason row; ``audit`` when its child action concerns the audit."""
    return Reason(
        Trusted(row["code"]), Trusted(row["message"]),
        _affected(row.get("affected"), safety),
        _parameters(row["parameters"], safety),
        _actions(row["next_actions"], launch, site, audit),
    )


def _spike_backend(run: dict[str, Any]) -> tuple[Trusted, ...]:
    """Render only the closed public StartRun binding; never infer current backend health."""
    backend = run["spike_backend"]
    if backend["kind"] != "qualified":
        # unavailable and unqualified both hold no spike authority — no requests until a binding
        # is qualified — so both render the same closed capability line.
        return (Trusted(f"{_LABELS['spike_backend']} {_LABELS['spike_backend_unavailable']}"),
                *_environment(run))
    return (
        Trusted(f"{_LABELS['spike_backend']} {_LABELS['spike_backend_qualified']}"),
        Trusted(f"  {_LABELS['spike_backend_record']} {backend['qualification_record_digest']}"),
        Trusted(f"  {_LABELS['spike_backend_execution']} {backend['execution_binding_digest']}"),
        *_environment(run),
    )


def _environment(run: dict[str, Any]) -> tuple[Trusted, ...]:
    """The run's environment step in closed words (spec §7): the current revision and its state,
    the attempts used, the installed binding as a stored reference, and the latest failure since
    it with its closed reason — never the agent's preparation text or image references."""
    member, qualification = run.get("environment"), run.get("qualification")
    if member is None or qualification is None:
        return ()
    active, failure = qualification["active"], qualification["last_failure"]
    return (
        Trusted(f"  {_LABELS['environment']} {member['revision']} {member['state']} {member['profile_id']}; "
                f"{_LABELS['environment_images']} {member['spike_image_count']}"),
        Trusted(f"  {_LABELS['qualification']} {qualification['attempts_used']}/{qualification['attempts_max']}; "
                + (f"{_LABELS['qualification_bound']} {active['seq']} "
                   f"{_LABELS['qualification_bound_note']}" if active is not None
                   else _LABELS["qualification_unbound"])),
        *((Trusted(f"  {_LABELS['qualification_failure']} {failure['reason']}"
                   + (f" {_LABELS['qualification_probe']} {failure['probe']}" if "probe" in failure else "")),)
          if failure is not None else ()),
    )


def _governance(run: dict[str, Any]) -> Trusted:
    """Render the compact governance header summary."""
    governance = run["governance"]
    if governance is None:
        return Trusted(_LABELS["no_governance"])
    # The mode is the public run view's own word; a delegated auto run has no human to ask, which the author must know.
    # The rule is the core's ``core.run.delegated_auto``: interactive authority outranks delegation.
    context = governance["context"]
    delegated = governance["control_mode"] == "auto" and context["delegation"] is True and context["interactive"] is not True
    parts = [f"{_LABELS['governance']} {governance['state']}",
             f"{_LABELS['mode']} {governance['control_mode']}{', ' + _LABELS['mode_delegated'] if delegated else ''}"]
    usage = " ".join(
        f"{governance['budgets'][used]}/{governance['proposal']['budgets'][ceiling]}"
        for ceiling, used in CEILINGS.items())
    parts.append(f"{_LABELS['budget_usage']} {usage}")
    return Trusted("; ".join(parts))


def _child_label(row: dict[str, Any], safety: TextSafety) -> Trusted | Untrusted:
    """The host audit child's label is the contract literal ``audit``; any other purpose is fenced."""
    if row["resource_class"] == "audit" and row["purpose"] == "audit":
        return Trusted(row["purpose"])
    return safety.untrusted(row["purpose"])


_WAIT_STATE = {None: "waiting_none_error", "unavailable": "waiting_unavailable", "unobserved": "waiting_unobserved",
               "already_presented": "waiting_already_presented", "interaction_limit": "waiting_interaction_limit",
               "surface_unrecorded": "waiting_surface_unrecorded", "presentation_failed": "waiting_presentation_failed"}


def _waiting(run: dict[str, Any], safety: TextSafety) -> tuple[str, ...]:
    """What waits on a human, where each record is cited from and for which claim, and whether a human will be asked."""
    batch = run.get("confirmation_batch")
    if not batch:
        return ()
    return (
        *(f"  {_LABELS['waiting_record']} {row['source_kind']} {safety.untrusted(row['source_ref'])} "
          f"{_LABELS['waiting_for_claim']} {safety.claim_id(row['claim_id'])}" for row in batch["records"]),
        *(f"  {_LABELS['waiting_claim']} {safety.claim_id(claim_id)}" for claim_id in batch["claim_ids"]),
        # The terminal step of a deliberative run asks about the run, not about a claim; the status is the core's own word.
        *((f"  {_LABELS['waiting_terminal']} {batch['terminal']['status']}",) if is_terminal(batch) else ()),
        f"  {_LABELS[_WAIT_STATE[batch['prompt_error']]]}",
    )


def _parse(result: dict[str, Any]) -> AuthorView:
    """Parse one schema-valid run-bearing Allow or Block result."""
    run = result["run"]
    delimiters = run["untrusted_delimiters"]
    safety = TextSafety(delimiters["open"], delimiters["close"])
    result_type = result["type"]
    if result_type == "Allow":
        result_type += f" ({_LABELS['converged']}{'true' if result['converged'] else 'false'})"
    rationale = run["governance"]["proposal"]["rationale"] if run["governance"] is not None else None
    unreviewed = run["host"].get("unreviewed_runtime")
    host_note = (Trusted(f"{_LABELS['host_unreviewed']} {unreviewed['package']} {unreviewed['version']} "
                         f"{_LABELS['host_unreviewed_note']}") if unreviewed is not None else None)
    header = Header(Trusted(result_type), Trusted(run["status"]), _governance(run),
                    Trusted(run["id"]), _spike_backend(run),
                    safety.untrusted(rationale) if rationale is not None else None, host_note)
    reasons = result["reasons"] if result["type"] == "Block" else []  # Only Block carries reasons.
    stopped = [row for row in run["children"] if row.get("recovery_action")]
    # A `child.terminal` reason does not name its child, so it is about the audit only when every stopped child is the audit's.
    audit_child = bool(stopped) and all(row["resource_class"] == "audit" for row in stopped)
    text = audit_launch(run["host"]["profile_id"])
    launch = _Launch(text, _home(run, reasons) if text else None)

    def about_audit(row: dict[str, Any]) -> bool:
        return row["code"].startswith("audit.") or (row["code"] == "child.terminal" and audit_child)
    terminal_next = _actions(run["next_actions"], launch, "next")
    terminal = run["status"] != "active"
    obligations = tuple(
        Obligation(
            _obligation_id(row["id"], safety),
            (safety.untrusted(row["required"])
             if row["id"].startswith("claim:") else Trusted(row["required"]))
            if row["required"] else None,
            _missing(row, safety) if row["missing"] is not None else None,
            _actions(row["next"], launch, "open_obligations", row["id"] == "obligation.audit")
            or (terminal_next if terminal else ()),
        )
        for row in run["obligations"]["active"] if row["status"] != "satisfied")
    satisfied = tuple(
        _obligation_id(row["id"], safety)
        for row in run["obligations"]["active"] if row["status"] == "satisfied")
    residuals = tuple(
        Reason(Trusted(row["code"]), Trusted(""), _residual_claims(row, safety),
               _parameters(row["parameters"], safety),
               _actions(row["next_actions"], launch, "residuals", about_audit(row)))
        for row in run["residuals"])
    children = tuple(
        Child(Trusted(row["resource_class"]), _child_label(row, safety), Trusted(row["state"]),
              _actions([row["recovery_action"]], launch, "children", row["resource_class"] == "audit")[0]
              if row.get("recovery_action") else None)
        for row in run["children"])
    freshness = tuple(
        Freshness(safety.untrusted(row["path"]), Trusted(row["state"]))
        for row in run["freshness"]["changes"])
    return AuthorView(
        header, _audit_summary(run["audit"], safety),
        # Only Block carries reasons; a run-bearing Allow has none.
        tuple(_reason(row, safety, launch, about_audit(row)) for row in reasons),
        obligations, satisfied, residuals, children, freshness,
        () if terminal else terminal_next,
        _waiting(run, safety),
    )


def _reason_lines(rows: tuple[Reason, ...]) -> tuple[str, ...]:
    """Render reasons or residuals without section framing."""
    return tuple(
        line
        for row in rows
        for line in (
            f"  {row.code}" + (f": {row.message}" if row.message else "")
            + (f" — {_LABELS['affected']} {row.affected}" if row.affected else ""),
            *((f"    {_LABELS['params']} {'; '.join(row.params)}",) if row.params else ()),
            *((f"    {_LABELS['next_inline']} {'; '.join(row.next_actions)}",)
              if row.next_actions else ()),
        )
    )


def _obligation_lines(rows: tuple[Obligation, ...]) -> tuple[str, ...]:
    """Render open obligations without section framing."""
    return tuple(
        line
        for row in rows
        for line in (
            f"  {row.obligation_id}" + (f": {row.required}" if row.required else ""),
            *((f"    {_LABELS['missing']} {row.missing}",) if row.missing else ()),
            *((f"    {_LABELS['next_inline']} {'; '.join(row.next_actions)}",)
              if row.next_actions else ()),
        )
    )


def _child_lines(rows: tuple[Child, ...]) -> tuple[str, ...]:
    """Render child summaries without section framing."""
    return tuple(
        f"  {row.purpose}: {row.state}" +
        (f" — {_LABELS['recovery']} {row.recovery}" if row.recovery else "") for row in rows)


def _freshness_lines(rows: tuple[Freshness, ...]) -> tuple[str, ...]:
    """Render freshness summaries without section framing."""
    return tuple(f"  {row.path}: {row.state}" for row in rows)


def _audit_summary(audit: dict[str, Any], safety: TextSafety) -> AuditSummary:
    return AuditSummary(Trusted(audit["verdict"]), audit["current"], audit["pending"],
                        None if audit["independence"] is None else Trusted(audit["independence"]),
                        tuple(safety.untrusted(item) for item in audit["findings"]))


def audit_text(audit: AuditSummary | dict[str, Any]) -> str:
    """The audit in words, from the core's four facts: the verdict (or that one is owed or pending), no longer current
    when the verdict does not cover the argument, a pending re-audit, and the independence of a settled verdict. Only
    contract labels are used."""
    get = (lambda key: getattr(audit, key)) if isinstance(audit, AuditSummary) else audit.__getitem__
    verdict, current, pending, independence = get("verdict"), get("current"), get("pending"), get("independence")
    if verdict == "none":
        parts = [_LABELS["audit_pending" if pending else "audit_verdict_none"]]
    else:
        parts = [_LABELS[f"audit_verdict_{verdict}"]]
        if current is False:
            parts.append(_LABELS["audit_not_current"])
        if pending:
            parts.append(_LABELS["audit_reaudit_pending"])
    suffix = f" ({independence})" if independence is not None else ""
    return ", ".join(parts) + suffix


def _audit_lines(audit: AuditSummary) -> tuple[str, ...]:
    """Render the audit in words, then the findings a failed audit reported."""
    return (f"{_LABELS['audit']} {audit_text(audit)}",
            *(f"  {_LABELS['finding']} {item}" for item in audit.findings))


def _render(view: AuthorView) -> str:
    """Render a parsed view through one ordered, empty-dropping section table."""
    base = (f"{view.header.result_type} {view.header.status} — {view.header.governance}",
            f"{_LABELS['run_id']} {view.header.run_id}",
            *view.header.spike_backend,
            *((f"{_LABELS['proposal_rationale']} {view.header.proposal_rationale}",)
              if view.header.proposal_rationale is not None else ()),
            *((view.header.host_note,) if view.header.host_note is not None else ()),
            *_audit_lines(view.audit))
    sections = (
        (_LABELS["reasons"], _reason_lines(view.reasons)),
        (_LABELS["open_obligations"], _obligation_lines(view.obligations)),
        (_LABELS["waiting"], view.waiting),
        (_LABELS["satisfied_obligations"], tuple(f"  {item}" for item in view.satisfied)),
        (_LABELS["residuals"], _reason_lines(view.residuals)),
        (_LABELS["children"], _child_lines(view.children)),
        (_LABELS["freshness"], _freshness_lines(view.freshness)),
        (_LABELS["next"], tuple(f"  {item}" for item in view.next_actions)),
    )
    blocks = ("\n".join((title, *lines)) for title, lines in sections if lines)
    return "\n\n".join(("\n".join(base), *blocks))


_VERIFICATION = {"verified_by_service": "verified_by_service", "confirmed_by_human": "confirmed_by_human",
                 "rejected_by_human": "rejected_by_human"}


def _verification_label(verification: dict[str, Any]) -> str:
    """Plain words for one record's verification: what the service did, what a human did, or who retrieves it."""
    if verification["status"] == "pending":
        return _LABELS["pending_audit" if verification["retrieve"] else "pending_human"]
    return _LABELS[_VERIFICATION[verification["status"]]]


def _redacted_environment_plan(value: object) -> object:
    if not isinstance(value, dict):
        return value
    environment = value.get("environment")
    if not isinstance(environment, (dict, list)):
        return value
    plan = dict(value)
    plan["environment"] = sorted(environment if isinstance(environment, dict) else environment)
    return plan


def _generic_spike_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Public artifact with runtime-local identity and authored environment values removed."""
    safe = dict(row)
    if safe["kind"] == "spike_request":
        for key in ("draft", "plan"):
            if key in safe:
                safe[key] = _redacted_environment_plan(safe[key])
    if safe["kind"] == "spike":
        facts = dict(safe["result_facts"])
        facts.pop("run_id", None)
        safe["result_facts"] = facts
    return safe


def _argument_lines(argument: dict[str, Any], safety: TextSafety) -> tuple[str, ...]:
    """Render the graph projection without private identity or artifact fields."""
    claims = tuple(
        line
        for row in argument["claims"]
        for line in (
            f"  {safety.claim_id(row['claim_id'])} {_LABELS['claim_kind']}{row['kind']} "
            f"{_LABELS['claim_gating']}{'true' if row['gating'] else 'false'} "
            f"{_LABELS['claim_state']}{row['state']} "
            f"{_LABELS['claim_evidence']}"
            f"{_LABELS['evidence_present'] if row['active_evidence_ids'] else _LABELS['evidence_none']}: "
            f"{safety.untrusted(row['text'])}",
            *((f"    {_LABELS['decision_' + row['human_decision']['outcome']]}"
               + (f" — {_LABELS['human_answer']} {safety.untrusted(row['human_decision']['answer'])}"
                  if row["human_decision"]["answer"] else ""),) if row.get("human_decision") else ()),
        )
    )
    edges = tuple(
        f"  {safety.claim_id(row['from'])} -{row['type']}-> {safety.claim_id(row['to'])}"
        for row in argument["edges"]
    )
    artifacts = argument["artifacts"]
    citations = tuple(
        line
        for row in artifacts if row.get("citation")
        for line in (
            f"  {safety.untrusted(row['citation'])}"
            + (f" — {_verification_label(row['verification'])}" if row.get("verification") else ""),
            *((f"    {_LABELS['human_note']} {safety.untrusted(row['verification']['note'])}",)
              if row.get("verification", {}).get("note") else ()),
        )
    )
    result_requests = {row["request_artifact_id"] for row in artifacts if row["kind"] == "spike"}
    spikes = tuple(
        line
        for row in artifacts if row["kind"] in {"spike_request", "spike"}
        for line in (
            f"  {_LABELS['generic_spike_request' if row['kind'] == 'spike_request' else 'generic_spike_result']} "
            f"{safety.claim_id(row['claim_id'])} {row['artifact_id']}",
            f"    {safety.untrusted(json.dumps(_generic_spike_evidence(row), separators=(',', ':'), ensure_ascii=False))}",
            *((f"    {_LABELS['generic_spike_no_result']}",)
              if row["kind"] == "spike_request" and row["artifact_id"] not in result_requests else ()),
        )
    )
    audit = argument["audit"]
    # Closed words and digests only: the binding, each revision and each attempt the evidence ran under.
    environment = json.dumps(argument["environment"], separators=(",", ":"), ensure_ascii=False)
    return (
        f"{_LABELS['goal']} {safety.untrusted(argument['goal'])}",
        f"{_LABELS['root_claim_id']} {safety.claim_id(argument['root_claim_id'])}",
        _LABELS["claims"], *claims,
        *((_LABELS["edges"], *edges) if edges else ()),
        *((_LABELS["citations"], *citations) if citations else ()),
        *((_LABELS["generic_spikes"], *spikes) if spikes else ()),
        _LABELS["argument_environment"], f"  {environment}",
        f"{_LABELS['audit_status']} {audit_text(audit)}",
        *(f"  {_LABELS['finding']} {safety.untrusted(item)}" for item in audit["findings"]),
    )


def _trusted_contract_value(value: Any, indent: str = "") -> tuple[str, ...]:
    """Render trusted mappings and lists recursively as deterministic text."""
    if isinstance(value, dict):
        return tuple(
            line
            for key, item in value.items()
            for line in (
                (f"{indent}{key}:", *_trusted_contract_value(item, indent + "  "))
                if isinstance(item, (dict, list)) else (f"{indent}{key}: {item}",)
            )
        )
    if isinstance(value, list):
        return tuple(
            line
            for item in value
            for line in (
                (f"{indent}-", *_trusted_contract_value(item, indent + "  "))
                if isinstance(item, (dict, list)) else (f"{indent}- {item}",)
            )
        )
    return (f"{indent}{value}",)


def _contract_lines(contract: dict[str, Any]) -> tuple[str, ...]:
    """Render an index or section trusted contract projection."""
    if contract["target"] == "index":
        return tuple(f"{row['id']} — {row['title']}" for row in contract["index"]["sections"])
    section = contract["section"]
    return (
        f"{section['id']} — {section['title']}",
        *_trusted_contract_value(
            {"summary": section["summary"], "clauses": section["clauses"]}, "  "
        ),
    )


def _render_valid(result: dict[str, Any]) -> str:
    """Render one schema-valid public result as text."""
    if "argument" in result:
        argument = result["argument"]
        return _LABELS["argument"] + "\n" + "\n".join(
            _argument_lines(argument, TextSafety(**argument["untrusted_delimiters"]))
        )
    if "contract_result" in result:
        return _LABELS["contract"] + "\n" + "\n".join(_contract_lines(result["contract_result"]))
    if result["type"] in {"Allow", "Block"} and "run" in result:
        return _render(_parse(result))
    if result["type"] == "Block":
        safety = TextSafety(**untrusted_delimiters())
        return _LABELS["block"] + "\n" + "\n".join(_reason_lines(
            tuple(_reason(row, safety) for row in result["reasons"])))
    if result["type"] == "Fault":
        reason = (f" reason {result['reason']} (GetContract evidence/spike: evidence/spike/limits)"
                  if "reason" in result else "")
        message = f": {result['message']}" if "message" in result else ""
        return f"{_LABELS['fault']} {result['code']} ({result['fail_direction']}){reason}{message}"
    return _LABELS["inert"]


def render_author_view(result: Any, *, strict: bool = False) -> str:
    """Validate once, then render every valid public result as plain text.

    Hosts call this non-strictly so invalid input or a renderer defect degrades to compact JSON.
    """
    if not validate_public_result(result):
        return _fallback(result)
    try:
        return _render_valid(result)
    except (KeyError, TypeError, ValueError):
        if strict:
            raise
        return _fallback(result)
