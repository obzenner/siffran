"""The sole place that knows the future ``empirica/v2`` application composition import and service
method names (D4 spec §3, §8).

``driver.new_driver`` builds the fake ports (observations/transport only) and calls
:func:`bind_live` here. This module imports ``application.v2``, calls its ``compose(...)`` factory,
and wraps the resulting service behind the :class:`driver.ConformanceDriver` protocol. Tests and
``driver.py`` depend only on ``ConformanceDriver``; they never import ``application.v2`` or call
service methods directly. ``LiveDriver.reload`` re-wraps the reloaded service in a fresh
``LiveDriver`` (it never returns the raw service). ``LiveDriver.compact`` returns the compacted
public dict; it never exposes the internal service object.

In the pre-D6/D7 tree ``application.v2`` does not exist, so :func:`compose_application` raises
:class:`driver.V2SeamAbsent` (late-imported to avoid a load-time cycle), which
``ConformanceCase.bind_driver`` turns into a per-case FAILURE.
"""
from __future__ import annotations

import importlib

import jsonschema

# The v2 application composition seam D6/D7 introduce. Only this module knows these names.
_V2_SEAM_MODULE = "application.v2"
_V2_COMPOSE_FACTORY = "compose"
# The pure D9 context selector module/factory (D4 spec §8). Only this module knows these names.
_D9_SELECTOR_MODULE = "core.context_selector"
_D9_SELECTOR_FACTORY = "select_sections"


def bind_selector(operation_context: str, reason_codes: list[str],
                  terminal_status: str | None) -> list[str]:
    """Import the real pure D9 context selector and call it with ONLY canonical inputs.

    Raises :class:`driver.V2SeamAbsent` when the D9 selector module is absent (pre-D9 tree) so the
    context cases (37–40) fail at the seam rather than aborting collection. The selector must not
    be given domain state — only operation_context, ordered reason codes, and terminal status.
    """
    try:
        mod = importlib.import_module(_D9_SELECTOR_MODULE)
    except ModuleNotFoundError as exc:
        from driver import V2SeamAbsent
        raise V2SeamAbsent(
            "empirica/v2 D9 context-selector seam absent: no "
            f"{_D9_SELECTOR_MODULE!r} module with a {_D9_SELECTOR_FACTORY!r} factory. D9 must "
            "introduce the pure context selector before these conformance cases can execute."
        ) from exc
    select = getattr(mod, _D9_SELECTOR_FACTORY, None)
    if select is None:
        from driver import V2SeamAbsent
        raise V2SeamAbsent(
            f"{_D9_SELECTOR_MODULE!r} has no {_D9_SELECTOR_FACTORY!r} factory; the D9 selector "
            "seam is not yet implemented."
        )
    return select(operation_context=operation_context, reason_codes=list(reason_codes),
                  terminal_status=terminal_status)


def compose_application(workspace, harness, runs, artifacts, host,
                        profile_id, limits, clock):
    """Import ``application.v2`` and call its ``compose()`` factory — the only composition entry.

    Raises :class:`driver.V2SeamAbsent` (late import, no load-time cycle) when the seam is absent
    so the suite collects/executes fully and reports each absent behavior by name.
    """
    try:
        seam = importlib.import_module(_V2_SEAM_MODULE)
    except ModuleNotFoundError as exc:
        from driver import V2SeamAbsent
        raise V2SeamAbsent(
            "empirica/v2 application composition seam absent: no "
            f"{_V2_SEAM_MODULE!r} module with a {_V2_COMPOSE_FACTORY!r} factory. The current "
            "service speaks empirica/v1 only (application.wire.API_VERSION == 'empirica/v1'). "
            "D6/D7 must introduce the v2 dispatch/composition seam before these conformance "
            "cases can execute against the real SUT."
        ) from exc

    compose = getattr(seam, _V2_COMPOSE_FACTORY, None)
    if compose is None:
        from driver import V2SeamAbsent
        raise V2SeamAbsent(
            f"{_V2_SEAM_MODULE!r} has no {_V2_COMPOSE_FACTORY!r} factory; the v2 composition seam "
            "is not yet implemented."
        )
    return compose(
        workspace=workspace, harness=harness, runs=runs, artifacts=artifacts,
        host=host, profile_id=profile_id, limits=limits, clock=clock,
    )


class LiveDriver:
    """Wraps a composed v2 service behind ``ConformanceDriver``. Holds no policy and has no branch
    on an expected reason/status.

    ``reload`` calls ``service.reload()`` and re-wraps the returned service in a NEW ``LiveDriver``
    that shares the same fake ports (so transport telemetry such as ``workspace.observe_calls``
    persists across reload). It never returns the raw service. ``compact`` returns the compacted
    public dict; it never returns the service.
    """

    __slots__ = (
        "_service", "_workspace", "_harness", "_runs", "_artifacts", "_host",
        "_profile_id", "_limits", "_clock",
    )

    def __init__(self, service, workspace, harness, runs, artifacts, host,
                 profile_id, limits, clock):
        self._service = service
        self._workspace = workspace
        self._harness = harness
        self._runs = runs
        self._artifacts = artifacts
        self._host = host
        self._profile_id = profile_id
        self._limits = limits
        self._clock = clock

    def request(self, envelope: dict) -> dict:
        return self._service.dispatch(envelope)

    def workspace_write(self, path: str, content: bytes) -> None:
        self._workspace.write(path, content)

    def workspace_delete(self, path: str) -> None:
        self._workspace.delete(path)

    def workspace_error(self, path: str, code: str) -> None:
        self._workspace.set_error(path, code)

    def harness_complete(self, command: str, exit_code: int) -> None:
        self._harness.complete(command, exit_code)

    def reload(self) -> "LiveDriver":
        reloaded = self._service.reload()
        return LiveDriver(
            reloaded, self._workspace, self._harness, self._runs, self._artifacts,
            self._host, self._profile_id, self._limits, self._clock,
        )

    def compact(self) -> dict:
        return self._service.compact()

    def artifacts(self) -> tuple[dict, ...]:
        return self._artifacts.all()

    def operational_state(self) -> dict:
        return self._service.operational_state()

    def workspace_observe_calls(self) -> int:
        # Transport telemetry for the pure-core boundary case (D4 case 15): proves workspace reads
        # are requested through the fake Workspace port, never hidden behind adapter behavior.
        return self._workspace.observe_calls

    def workspace_observe_history(self) -> tuple:
        # D4-S2 transport telemetry: tuples of (exact normalized path batch, result batch) per
        # observe call. Transport facts only; never derives claim/audit/run policy.
        return self._workspace.observe_history()

    def harness_invocations(self) -> tuple[dict, ...]:
        # D4-S2 transport telemetry: harness invocation history as returned attestations.
        return self._harness.invocations()

    # ---- D4-S2 trusted application ingress (D2C correction) -------------
    # These invoke the real private composition ingress, not fake-only telemetry. The private
    # capability is supplied inside the application adapter, never in a public request or test
    # value. ``LiveDriver`` calls the dedicated private service methods of the same names directly
    # (never public ``dispatch`` or redacted envelopes) and validates/returns the standard public
    # response; it does not only append to a fake list.

    def _validate_trusted_response(self, resp: dict) -> None:
        """Validate a trusted-ingress response against the v2 response schema."""
        import assertions
        jsonschema.validate(instance=resp, schema=assertions._RESPONSE_SCHEMA)

    def trusted_child_event(self, run_id: str, child_id: str, event: dict) -> dict:
        """Deliver a trusted child event through the private composition ingress.

        Calls the dedicated private service method ``trusted_child_event`` directly (never
        public ``dispatch`` or a redacted envelope) and validates the returned response.
        """
        resp = self._service.trusted_child_event(
            run_id=run_id, child_id=child_id, event=event)
        self._validate_trusted_response(resp)
        return resp

    def trusted_evidence_leaf(self, run_id: str, payload: dict) -> dict:
        """Deliver a trusted evidence_leaf through the private composition ingress.

        Calls the dedicated private service method ``trusted_evidence_leaf`` directly (never
        public ``dispatch`` or a redacted envelope) and validates the returned response.
        """
        resp = self._service.trusted_evidence_leaf(run_id=run_id, payload=payload)
        self._validate_trusted_response(resp)
        return resp

    def trusted_audit_verdict(self, run_id: str, child_id: str, payload: dict) -> dict:
        """Deliver a trusted audit_verdict through the private composition ingress.

        Calls the dedicated private service method ``trusted_audit_verdict`` directly (never
        public ``dispatch`` or a redacted envelope) and validates the returned response.
        """
        resp = self._service.trusted_audit_verdict(
            run_id=run_id, child_id=child_id, payload=payload)
        self._validate_trusted_response(resp)
        return resp

    def trusted_attribution(self, run_id: str, payload: dict) -> dict:
        """Deliver a trusted attribution through the private composition ingress.

        Calls the dedicated private service method ``trusted_attribution`` directly (never
        public ``dispatch`` or a redacted envelope) and validates the returned response.
        Independence is derived from this ingress; tests never possess capability material.
        """
        resp = self._service.trusted_attribution(run_id=run_id, payload=payload)
        self._validate_trusted_response(resp)
        return resp

    # ---- D4 spec §8 test seams (NOT public commands) ----------------------

    def select_sections(self, operation_context: str, reason_codes: list[str],
                        terminal_status: str | None) -> list[str]:
        """Expose the real pure D9 context selector through this test seam (D4 spec §8).

        Only canonical ``operation_context``, ordered ``reason_codes``, and ``terminal_status``
        inputs are passed; no domain state is manufactured. Raises :class:`driver.V2SeamAbsent`
        when the D9 selector module is absent (pre-D9 tree) so cases 37–40 fail at the seam.
        """
        return bind_selector(operation_context, list(reason_codes), terminal_status)

    def inject_run_state(self, key, state: dict) -> None:
        """Install raw persisted state through the fake run repository before RestoreRun (D4 spec
        §8). This is test INPUT for the strict-decoder negatives (45–47), not a public command."""
        self._service.inject_run_state(key, dict(state))


def bind_live(workspace, harness, runs, artifacts, host, profile_id, limits, clock):
    """Compose the real v2 service through the fake ports and wrap it behind ``ConformanceDriver``.

    The only SUT composition entry point. ``driver.new_driver`` calls this; tests never do.
    """
    service = compose_application(
        workspace, harness, runs, artifacts, host, profile_id, limits, clock,
    )
    return LiveDriver(
        service, workspace, harness, runs, artifacts, host, profile_id, limits, clock,
    )
