#!/usr/bin/env python3
"""The one parsed conditional for the engine's coupled branches (ADR-0075).

Both engines — the drawing side (`ui_view_strategies`) and the derivation side
(`contract_strategies`' challenges and flips) — consume this module's parse, so the conditional
forms they support cannot drift apart. The parse is strict: a form the engine does not fully
model refuses at parse, never guesses silently, and a refusal on one side is a refusal on both.

The modelled forms, exactly (Empirica 7.2 §5.1); a ``then``-less or one-side guard, a
boolean ``if`` (``true``/``false`` — always/never applies, so no member is guarded) at the node
level or inside ``allOf``, and any other ``if``-carrying shape refuse at parse; a composition entry with no ``if`` key at all is not conditional and stays ignored:

* the two-way value pairing — an ``allOf`` entry ``{if, then, else}`` whose ``if`` names one
  guarded member with a ``{const}`` (its ``required``, when present, names the guarded member
  *alone* — a multi-key demand is unmodelled), and whose ``then``/``else`` each name the same one
  paired member with its value schema (the run/5 attempt row's probe presentation);
* the one-way value guard — an ``allOf`` entry ``{if, then}`` whose ``if`` names one member as
  ``{const}`` (or ``{not: {const}}``) and requires it, and whose ``then`` names one paired member
  with its schema (PR 15, A7's status/body coupling);
* the node-level presence pairing — an ``{if, then, else}`` on the object itself whose ``if``
  names one guarded member with a ``{const}`` (its ``required``, when present, names the guarded
  member alone), and whose two sides each either demand the one paired member
  (``required: [<member>]``) or forbid it (``not: {required: [<member>]``) — opposite sides, one
  member, no other shape (the run view's last failure and the public fault; a side that only
  re-declares the member with its schema is unmodelled: jsonschema never makes it required, so a
  drawing cannot be derived from it); a presence pairing's paired member also differs from its
  own driver — a member guarding itself is unmodelled and refuses
  at parse exactly as the two-way form's same shape refusal does.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Guard:
    """The one-way guard: the driver's const (or not-const) fixing one paired member's schema."""
    driver: str
    mode: str                 # "const" | "not"
    value: object
    target: str
    schema: dict


@dataclass(frozen=True)
class Pairing:
    """One two-branch conditional: the guarded word against the paired member.

    ``then_side``/``else_side`` carry the member's shape on each branch: ``("value", schema)`` for
    the two-way value pairing, ``("present", None)``/``("absent", None)`` for the presence pairing.
    ``demanded`` is whether the ``if`` itself requires the guarded member (an absent driver falls
    per that demand); ``at_allOf`` distinguishes the two-way value form from the node-level
    presence form.
    """
    driver: str
    word: object
    target: str
    demanded: bool
    then_side: tuple
    else_side: tuple
    at_allOf: bool


def parse(node: object, doc_id: str, pointer: str) -> tuple[list[Guard], list[Pairing]]:
    """The modelled conditional forms one node carries: (its one-way guards, its pairings).

    A node without ``allOf`` and without a node-level ``if`` parses to nothing. Anything else
    conditional-shaped that is not exactly a modelled form raises ``ValueError`` — the caller's
    document identity and pointer ride along in the message.
    """
    guards: list[Guard] = []
    pairings: list[Pairing] = []
    if not isinstance(node, dict):
        return guards, pairings
    for entry in node.get("allOf", ()):
        if not isinstance(entry, dict) or "if" not in entry:
            # ``allOf`` also composes pattern conjuncts and ``$ref`` shapes — other engines' kinds,
            # none conditional. This parse claims the conditional forms alone; a composition entry
            # that carries no ``if`` key is not conditional-shaped, so skipping it is scope.
            continue
        if not isinstance(entry["if"], dict):
            # a boolean (or otherwise non-object) ``if`` is conditional-shaped: JSON Schema reads
            # ``if: true`` as always applying and ``if: false`` as never, so its ``then``/``else``
            # are not a pairing this engine can draw or derive — refusing it here keeps both
            # sides honest instead of silently guessing.
            raise ValueError(f"{doc_id}#{pointer}: an allOf condition with a boolean (non-object) "
                             "if is not this engine's conditional")
        if set(entry) == {"if", "then", "else"}:
            pairings.append(_two_way(entry, doc_id, pointer))
            continue
        if set(entry) == {"if", "then"}:
            guards.append(_one_way(entry, doc_id, pointer))
            continue
        raise ValueError(f"{doc_id}#{pointer}: an allOf condition with keys "
                         f"{sorted(set(entry) - {'if'})} is not this engine's conditional")
    if "if" in node:
        if not isinstance(node["if"], dict):
            # the node-level twin of the allOf refusal above: a boolean ``if`` is not a presence
            # guard on one member, and ignoring it drew the guarded branch without its paired
            # member (a document the schema itself refused).
            raise ValueError(f"{doc_id}#{pointer}: a node-level boolean (non-object) if is not "
                             "this engine's conditional")
        pairings.append(_presence(node, doc_id, pointer))
    return guards, pairings


def _two_way(entry: dict, doc_id: str, pointer: str) -> Pairing:
    cond, then, otherwise = entry["if"], entry["then"], entry["else"]
    if set(cond) not in ({"properties"}, {"properties", "required"}):
        raise ValueError(f"{doc_id}#{pointer}: a two-way guard names exactly one member, with at "
                         "most its own requirement")
    if len(cond.get("properties", {})) != 1:
        raise ValueError(f"{doc_id}#{pointer}: a two-way guard names exactly one guarded member")
    (driver, guard), = cond["properties"].items()
    if "required" in cond and cond["required"] != [driver]:
        raise ValueError(f"{doc_id}#{pointer}: a two-way guard's requirement names its own member "
                         "alone — a multi-key if.required is unmodelled")
    if set(guard) != {"const"}:
        raise ValueError(f"{doc_id}#{pointer}: a two-way guard is a const on one member, exactly")
    if (set(then) != {"properties"} or set(otherwise) != {"properties"}
            or len(then["properties"]) != 1 or len(otherwise["properties"]) != 1
            or set(then["properties"]) != set(otherwise["properties"])):
        raise ValueError(f"{doc_id}#{pointer}: a two-way conditional names one guarded member and "
                         "one paired member per side, exactly")
    (target, then_schema), = then["properties"].items()
    (_, else_schema), = otherwise["properties"].items()
    if target == driver:
        raise ValueError(f"{doc_id}#{pointer}: a two-way guard is a const on one member naming a "
                         "different paired member")
    return Pairing(driver, guard["const"], target, "required" in cond,
                  ("value", then_schema), ("value", else_schema), True)


def _one_way(entry: dict, doc_id: str, pointer: str) -> Guard:
    cond, then = entry["if"], entry["then"]
    if set(cond) != {"properties", "required"} or set(then) != {"properties"}:
        raise ValueError(f"{doc_id}#{pointer}: an allOf guard must name its own member, then one "
                         "member")
    (member, guard), = cond["properties"].items()
    if cond["required"] != [member]:
        raise ValueError(f"{doc_id}#{pointer}: an allOf guard without its own member required")
    if set(guard) == {"const"}:
        mode, value = "const", guard["const"]
    elif set(guard) == {"not"} and set(guard["not"]) == {"const"}:
        mode, value = "not", guard["not"]["const"]
    else:
        raise ValueError(f"{doc_id}#{pointer}: an allOf guard is a const or a not-const only")
    (target, schema), = then["properties"].items()
    return Guard(member, mode, value, target, schema)


def _presence(node: dict, doc_id: str, pointer: str) -> Pairing:
    cond, then, otherwise = node["if"], node.get("then"), node.get("else")
    if set(cond) not in ({"properties"}, {"properties", "required"}):
        raise ValueError(f"{doc_id}#{pointer}: a presence guard must name exactly one member")
    if len(cond.get("properties", {})) != 1:
        raise ValueError(f"{doc_id}#{pointer}: a presence guard names exactly one member")
    (driver, guard), = cond["properties"].items()
    if set(guard) != {"const"}:
        raise ValueError(f"{doc_id}#{pointer}: a presence guard is a const, exactly")
    if "required" in cond and cond["required"] != [driver]:
        raise ValueError(f"{doc_id}#{pointer}: a presence guard requires only its own member")

    def _side(branch: object, where: str) -> tuple[str, str]:
        if set(branch) == {"required"} and len(branch["required"]) == 1:
            return ("present", branch["required"][0])
        if (set(branch) == {"not"} and set(branch["not"]) == {"required"}
                and len(branch["not"]["required"]) == 1):
            return ("absent", branch["not"]["required"][0])
        raise ValueError(f"{doc_id}#{pointer}: a presence side ({where}) demands or forbids one "
                         "member exactly — a side that only re-declares its properties demands "
                         "nothing to jsonschema and is unmodelled")

    if not isinstance(then, dict) or not isinstance(otherwise, dict):
        raise ValueError(f"{doc_id}#{pointer}: a presence pairing must name both its sides")
    then_side, else_side = _side(then, "then"), _side(otherwise, "else")
    if then_side[1] == driver:
        raise ValueError(f"{doc_id}#{pointer}: a presence pairing names a paired member other than "
                         "its own driver — a member guarding itself is unmodelled, exactly as the "
                         "two-way value form refuses it")
    if then_side[1] != else_side[1] or then_side[0] == else_side[0]:
        raise ValueError(f"{doc_id}#{pointer}: a presence pairing holds one paired member present "
                         "on exactly one of its two sides")
    return Pairing(driver, guard["const"], then_side[1], "required" in cond,
                   (then_side[0], None), (else_side[0], None), False)
