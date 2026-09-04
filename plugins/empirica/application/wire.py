"""The ``empirica/v1`` wire boundary: envelope validation, run handles, typed responses.

This module owns the *shape* of the versioned protocol defined in ``contracts/empirica/v1``
(ADR-30) and nothing else — it names no hook, exit code, path, or Git command. A request is a
transport-neutral envelope ``{protocol, request_id, command}``; a response is
``{protocol, request_id, result}`` where ``result`` is one of Allow / Block / Inert / Fault. The
service (``service.py``) turns a decision into one of these; a host adapter maps native events onto
the request and the result onto native enforcement.

Two design points worth stating once:

* ``request_id`` round-trips verbatim so a caller can correlate a reply with its request; a
  malformed envelope still gets a Fault carrying whatever ``request_id`` was legible.
* A run is addressed on the wire by an opaque ``run.id`` *handle*. StartRun mints it from the
  ``(project, run, generation)`` :class:`~core.records.RunKey`; every later command passes it back.
  It is deliberately opaque (base64url of a small JSON object) so a caller treats it as a token, not
  a parseable path — the whole point of ADR-31's generation isolation is that the generation is part
  of a run's identity, not a detail a caller reconstructs.
"""
from __future__ import annotations

import base64
import binascii
import json

from core.records import RunKey

API_VERSION = "empirica/v1"

# Command discriminators (request.command.type) and result discriminators (response.result.type).
CMD_START_RUN = "StartRun"
CMD_OBSERVE_ACTION = "ObserveAction"
CMD_EVALUATE_RUN = "EvaluateRun"
CMD_GET_RUN = "GetRun"
_COMMANDS = frozenset({CMD_START_RUN, CMD_OBSERVE_ACTION, CMD_EVALUATE_RUN, CMD_GET_RUN})

# EvaluateRun intents (response.result oneOf drives behaviour; see service._evaluate).
INTENT_CONTINUE = "continue"
INTENT_REPORT_CONVERGENCE = "report_convergence"
INTENT_STOP = "stop"
_INTENTS = frozenset({INTENT_CONTINUE, INTENT_REPORT_CONVERGENCE, INTENT_STOP})

# Fault codes, per the response schema. fail_direction is always "closed" here: the service refuses
# to bless a stop it cannot justify, so an ambiguous state keeps the run from finishing (ADR-19).
FAULT_INVALID_REQUEST = "invalid_request"
FAULT_UNSUPPORTED = "unsupported"
FAULT_CONFLICT = "conflict"
FAULT_CORRUPT_RUN = "corrupt_run"
FAULT_CORRUPT_ARTIFACTS = "corrupt_artifacts"
FAULT_UNAVAILABLE = "unavailable"

# Terminal statuses an active run may transition into (response.run.status enum minus "active").
STATUS_ACTIVE = "active"
STATUS_CONVERGED = "converged"
STATUS_STOPPED_RESIDUAL = "stopped_residual"
STATUS_STOPPED_FROZEN = "stopped_frozen"
STATUS_STOPPED_BUDGET = "stopped_budget"


class InvalidRequest(Exception):
    """The wire envelope or a command field is malformed. Carries the wire ``message`` for a Fault.

    Raised while parsing, caught at the dispatch boundary and turned into
    ``Fault(invalid_request, closed)`` — a caller must never treat a rejected request as a stop it
    may act on (ADR-30: an unknown/malformed command is a fault, never success).
    """


# --- run handles -------------------------------------------------------------


def encode_handle(key: RunKey) -> str:
    """An opaque, URL-safe run handle for a :class:`RunKey`. Injective and self-describing so the
    service can recover the exact ``(project, run, generation)`` slice a later command names — a
    resumed session and a fresh generation are different runs and must not alias (ADR-31)."""
    raw = json.dumps(
        {"p": key.project_id, "r": key.run_id, "g": key.generation},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_handle(handle: object) -> RunKey:
    """Recover the :class:`RunKey` a handle names, or raise :class:`InvalidRequest` if it is not a
    handle this service minted. Validating rather than trusting keeps a hostile ``run_id`` from
    steering a read at an arbitrary key."""
    if not isinstance(handle, str) or not handle:
        raise InvalidRequest("run_id must be a non-empty run handle")
    try:
        raw = base64.urlsafe_b64decode(handle.encode("ascii"))
        obj = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise InvalidRequest(f"run_id is not a valid run handle: {exc}") from exc
    if not isinstance(obj, dict) or {"p", "r", "g"} - obj.keys():
        raise InvalidRequest("run_id handle is missing project/run/generation")
    project, run, gen = obj["p"], obj["r"], obj["g"]
    if not isinstance(project, str) or not isinstance(run, str) or not isinstance(gen, int):
        raise InvalidRequest("run_id handle has malformed project/run/generation")
    return RunKey(project, run, gen)


# --- envelope validation -----------------------------------------------------


def parse_envelope(envelope: object) -> tuple[str, dict]:
    """Validate the outer request envelope and return ``(request_id, command)``.

    Raises :class:`InvalidRequest` for anything the ``empirica/v1`` request schema would reject at
    the envelope level (wrong protocol, missing id, missing/unknown command). Per-command field
    checks live in the individual handlers so each names exactly what it needs.
    """
    if not isinstance(envelope, dict):
        raise InvalidRequest("request must be an object")
    if envelope.get("protocol") != API_VERSION:
        raise InvalidRequest(f"unsupported protocol: {envelope.get('protocol')!r}")
    request_id = envelope.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise InvalidRequest("request_id is required")
    command = envelope.get("command")
    if not isinstance(command, dict):
        raise InvalidRequest("command must be an object")
    ctype = command.get("type")
    if ctype not in _COMMANDS:
        raise InvalidRequest(f"unknown command type: {ctype!r}")
    return request_id, command


def require(command: dict, field: str, types: type | tuple[type, ...]) -> object:
    """Fetch a required command field of an expected type, or raise :class:`InvalidRequest`.
    ``bool`` is excluded when an ``int`` is expected (a bool is an int in Python, and a budget of
    ``True`` is a bug, not a cap of 1)."""
    if field not in command:
        raise InvalidRequest(f"missing field: {field}")
    value = command[field]
    if not isinstance(value, types) or (types is int and isinstance(value, bool)):
        raise InvalidRequest(f"field {field} has the wrong type")
    return value


# --- response builders -------------------------------------------------------


def run_obj(handle: str, status: str, revision: int, **extra: object) -> dict:
    """The ``run`` sub-object shared by Allow/Block results. ``extra`` carries advisory reporting
    fields (note, deferred, blocked, audit, …); the schema allows additional properties there."""
    run = {"id": handle, "status": status, "revision": revision}
    run.update({k: v for k, v in extra.items() if v is not None and v != () and v != ""})
    return run


def allow(converged: bool, run: dict) -> dict:
    return {"type": "Allow", "converged": converged, "run": run}


def block(reason: str, run: dict) -> dict:
    return {"type": "Block", "reason": reason, "run": run}


def inert(reason: str) -> dict:
    return {"type": "Inert", "reason": reason}


def fault(code: str, message: str = "", fail_direction: str = "closed") -> dict:
    result: dict = {"type": "Fault", "code": code, "fail_direction": fail_direction}
    if message:
        result["message"] = message
    return result


def envelope(request_id: str, result: dict) -> dict:
    """Wrap a result in the outer response envelope, echoing the request id (ADR-30)."""
    return {"protocol": API_VERSION, "request_id": request_id, "result": result}
