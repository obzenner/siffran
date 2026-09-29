"""Schema-validated, deterministic plain-text author view for Empirica results."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NewType

from application.protocol import (
    next_action_surfaces,
    response_schema_defs,
    untrusted_delimiters,
    validate_public_result,
)
from core.governance import CEILINGS
from core.projection import safe_text

Trusted = NewType("Trusted", str)
Untrusted = NewType("Untrusted", str)
_SURFACES = next_action_surfaces()
_DEFS = response_schema_defs()
_DIGEST_DEFS = frozenset({"digest256", "nullableDigest256"})


@dataclass(frozen=True)
class TextSafety:
    """Convert author-controlled values to escaped, delimited text."""

    open: str
    close: str

    def untrusted(self, value: object) -> Untrusted:
        escaped = safe_text(value).replace("<", r"\x3c").replace(">", r"\x3e")
        return Untrusted(f"{self.open}{escaped}{self.close}")


@dataclass(frozen=True)
class Header:
    """First-line result, run, and governance summary."""

    result_type: Trusted
    status: Trusted
    governance: Trusted
    run_id: Trusted


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
    missing: Trusted | None
    next_actions: tuple[Trusted, ...]


@dataclass(frozen=True)
class Child:
    """One child summary with an author-controlled purpose."""

    purpose: Untrusted
    state: Trusted
    recovery: Trusted | None


@dataclass(frozen=True)
class Freshness:
    """One author-controlled path and trusted freshness state."""

    path: Untrusted
    state: Trusted


@dataclass(frozen=True)
class AuthorView:
    """Parsed author view consumed by the declarative renderer."""

    header: Header
    reasons: tuple[Reason, ...]
    obligations: tuple[Obligation, ...]
    satisfied: tuple[Trusted, ...]
    residuals: tuple[Reason, ...]
    children: tuple[Child, ...]
    freshness: tuple[Freshness, ...]
    next_actions: tuple[Trusted, ...]


def _fallback(result: Any) -> str:
    """Return sorted compact JSON for non-view and invalid input."""
    try:
        return json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return "null"


def _surface(action_id: str) -> Trusted:
    """Render one contract-owned action surface."""
    surface = _SURFACES[action_id]
    tool = surface.get("tool")
    if tool == "empirica_observe":
        return Trusted(f"empirica_observe kind={surface['action']}")
    if tool == "empirica_read":
        return Trusted(f"empirica_read {surface['operation']}")
    if tool == "report_convergence":
        return Trusted(f"report_convergence intent={surface['intent']}")
    return Trusted(f"{surface['owner']}: {surface['operation']}")


def _obligation_id(value: str, safety: TextSafety) -> Trusted:
    """Render a contract id while fencing an embedded claim id."""
    if value.startswith("claim:"):
        return Trusted("claim:" + safety.untrusted(value.removeprefix("claim:")))
    return Trusted(value)


ValueRenderer = Callable[[Any, "TextSafety"], str]


def _value_renderer(schema: dict[str, Any]) -> ValueRenderer | None:
    """Derive a value renderer from its response-schema shape; ``None`` drops digests.

    Ownership follows the schema: enums/consts are contract-owned (trusted), free strings are
    author-supplied (untrusted), arrays and objects compose their item/field renderers.
    """
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return None if name in _DIGEST_DEFS else _value_renderer(_DEFS[name])
    if "enum" in schema or "const" in schema:
        return lambda value, _safety: str(value)
    if schema.get("type") == "string":
        return lambda value, safety: safety.untrusted(value)
    if schema.get("type") == "array":
        item = _value_renderer(schema["items"])
        separator = ", " if schema["items"].get("type") == "string" else "; "
        return lambda value, safety: separator.join(item(row, safety) for row in value)
    if schema.get("type") == "object":
        fields = tuple((name, _value_renderer(sub))
                       for name, sub in schema["properties"].items())
        return lambda value, safety: ": ".join(
            render(value[name], safety) for name, render in fields
            if render is not None and name in value)
    raise ValueError(f"author view cannot render parameter schema: {schema!r}")


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
    claim = "claim:" + safety.untrusted(row["claim_id"])
    target = row.get("target_claim_id", row["claim_id"])
    if target == row["claim_id"]:
        return Trusted(claim)
    return Trusted(f"{claim} via claim:{safety.untrusted(target)}")


def _reason(row: dict[str, Any], safety: TextSafety) -> Reason:
    """Parse one schema-valid reason row."""
    return Reason(
        Trusted(row["code"]), Trusted(row["message"]),
        _affected(row.get("affected"), safety),
        _parameters(row["parameters"], safety),
        tuple(_surface(action) for action in row["next_actions"]),
    )


def _governance(run: dict[str, Any]) -> Trusted:
    """Render the compact governance header summary."""
    governance = run["governance"]
    if governance is None:
        return Trusted("no governance")
    parts = [f"governance: {governance['state']}"]
    proposal = governance.get("proposal")
    budgets = governance.get("budgets")
    if proposal is not None and budgets is not None:
        usage = " ".join(
            f"{budgets[used]}/{proposal['budgets'][ceiling]}"
            for ceiling, used in CEILINGS.items())
        parts.append(f"passes/spawns/audits: {usage}")
    return Trusted("; ".join(parts))


def _parse(result: dict[str, Any]) -> AuthorView:
    """Parse one schema-valid run-bearing Allow or Block result."""
    run = result["run"]
    delimiters = run["untrusted_delimiters"]
    safety = TextSafety(delimiters["open"], delimiters["close"])
    result_type = result["type"]
    if result_type == "Allow":
        result_type += f" (converged={'true' if result['converged'] else 'false'})"
    header = Header(Trusted(result_type), Trusted(run["status"]), _governance(run),
                    Trusted(run["id"]))
    terminal_next = tuple(_surface(action) for action in run["next_actions"])
    terminal = run["status"] != "active"
    obligations = tuple(
        Obligation(
            _obligation_id(row["id"], safety),
            (safety.untrusted(row["required"])
             if row["id"].startswith("claim:") else Trusted(row["required"]))
            if row["required"] else None,
            Trusted(row["missing"]["code"]) if row["missing"] is not None else None,
            tuple(_surface(action) for action in row["next"])
            or (terminal_next if terminal else ()),
        )
        for row in run["obligations"]["active"] if row["status"] != "satisfied")
    satisfied = tuple(
        _obligation_id(row["id"], safety)
        for row in run["obligations"]["active"] if row["status"] == "satisfied")
    residuals = tuple(
        Reason(Trusted(row["code"]), Trusted(""), _residual_claims(row, safety),
               _parameters(row["parameters"], safety),
               tuple(_surface(action) for action in row["next_actions"]))
        for row in run["residuals"])
    children = tuple(
        Child(safety.untrusted(row["purpose"]), Trusted(row["state"]),
              _surface(row["recovery_action"]) if row.get("recovery_action") else None)
        for row in run["children"])
    freshness = tuple(
        Freshness(safety.untrusted(row["path"]), Trusted(row["state"]))
        for row in run["freshness"]["changes"])
    return AuthorView(
        header, tuple(_reason(row, safety) for row in result.get("reasons", [])),
        obligations, satisfied, residuals, children, freshness,
        () if terminal else terminal_next,
    )


def _reason_lines(rows: tuple[Reason, ...]) -> tuple[str, ...]:
    """Render reasons or residuals without section framing."""
    return tuple(
        line
        for row in rows
        for line in (
            f"  {row.code}" + (f": {row.message}" if row.message else "")
            + (f" — affected: {row.affected}" if row.affected else ""),
            *((f"    params: {'; '.join(row.params)}",) if row.params else ()),
            *((f"    next: {'; '.join(row.next_actions)}",) if row.next_actions else ()),
        )
    )


def _obligation_lines(rows: tuple[Obligation, ...]) -> tuple[str, ...]:
    """Render open obligations without section framing."""
    return tuple(
        line
        for row in rows
        for line in (
            f"  {row.obligation_id}" + (f": {row.required}" if row.required else ""),
            *((f"    missing: {row.missing}",) if row.missing else ()),
            *((f"    next: {'; '.join(row.next_actions)}",) if row.next_actions else ()),
        )
    )


def _child_lines(rows: tuple[Child, ...]) -> tuple[str, ...]:
    """Render child summaries without section framing."""
    return tuple(
        f"  {row.purpose}: {row.state}" +
        (f" — recovery: {row.recovery}" if row.recovery else "") for row in rows)


def _freshness_lines(rows: tuple[Freshness, ...]) -> tuple[str, ...]:
    """Render freshness summaries without section framing."""
    return tuple(f"  {row.path}: {row.state}" for row in rows)


def _render(view: AuthorView) -> str:
    """Render a parsed view through one ordered, empty-dropping section table."""
    base = (f"{view.header.result_type} {view.header.status} — {view.header.governance}",
            f"run_id: {view.header.run_id}")
    sections = (
        ("Reasons:", _reason_lines(view.reasons)),
        ("Open obligations:", _obligation_lines(view.obligations)),
        ("Satisfied obligations:", tuple(f"  {item}" for item in view.satisfied)),
        ("Residuals:", _reason_lines(view.residuals)),
        ("Children:", _child_lines(view.children)),
        ("Freshness:", _freshness_lines(view.freshness)),
        ("Next:", tuple(f"  {item}" for item in view.next_actions)),
    )
    blocks = ("\n".join((title, *lines)) for title, lines in sections if lines)
    return "\n\n".join(("\n".join(base), *blocks))


def _argument_lines(argument: dict[str, Any], safety: TextSafety) -> tuple[str, ...]:
    """Render the graph projection without private identity or artifact fields."""
    claims = tuple(
        f"  {safety.untrusted(row['claim_id'])} kind={row['kind']} "
        f"gating={'true' if row['gating'] else 'false'} state={row['state']} "
        f"evidence={'present' if row['active_evidence_ids'] else 'none'}: "
        f"{safety.untrusted(row['text'])}"
        for row in argument["claims"]
    )
    edges = tuple(
        f"  {safety.untrusted(row['from'])} -{row['type']}-> {safety.untrusted(row['to'])}"
        for row in argument["edges"]
    )
    citations = tuple(
        f"  {safety.untrusted(row['citation'])}"
        for row in argument["artifacts"] if row.get("citation")
    )
    audit = argument["audit"]
    return (
        f"goal: {safety.untrusted(argument['goal'])}",
        f"root_claim_id: {safety.untrusted(argument['root_claim_id'])}",
        "Claims:", *claims,
        *(("Edges:", *edges) if edges else ()),
        *(("Citations:", *citations) if citations else ()),
        f"Audit status: {audit['state']} ({audit['independence']})",
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
        return "Argument\n" + "\n".join(
            _argument_lines(argument, TextSafety(**argument["untrusted_delimiters"]))
        )
    if "contract_result" in result:
        return "Contract\n" + "\n".join(_contract_lines(result["contract_result"]))
    if result["type"] in {"Allow", "Block"} and "run" in result:
        return _render(_parse(result))
    if result["type"] == "Block":
        safety = TextSafety(**untrusted_delimiters())
        return "Block\n" + "\n".join(_reason_lines(
            tuple(_reason(row, safety) for row in result["reasons"])))
    if result["type"] == "Fault":
        message = f": {result['message']}" if "message" in result else ""
        return f"Fault {result['code']} ({result['fail_direction']}){message}"
    return "Inert"


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
