"""Production one-container `/2` Docker harness over exact qualified authority."""
from __future__ import annotations

import secrets
import time
from contextlib import ExitStack
from typing import Callable, Protocol

from application import generic_spike as application
from core import spike_plan
from core.canonical import canonical_digest

from . import common, generic_authority, generic_execution as execution, planner
from .common import runtime_lock


class GenericAuthorityResolver(Protocol):
    def resolve(self, backend: application.GenericQualifiedBackend, *, deadline: float) \
            -> generic_authority.ResolvedGenericAuthority: ...


class GenericEffectsFactory(Protocol):
    def __call__(self, authority: generic_authority.ResolvedGenericAuthority) \
            -> execution.GenericEffects: ...


def _effects(authority: generic_authority.ResolvedGenericAuthority) -> execution.GenericEffects:
    return execution.concrete_effects(
        authority.docker_cli, dict(authority.environment), authority.work_directory)


def _operation_id() -> str:
    return "o-" + secrets.token_hex(16)


def _staging_token(operation_id: str, snapshot_digest: str) -> str:
    return "stg-" + canonical_digest({
        "operation_id": operation_id, "snapshot_digest": snapshot_digest,
    })[7:39]


class GenericDockerHarness(application.GenericSpikeHarness):
    def __init__(
        self,
        store: common.ExecutionStore,
        resolver: GenericAuthorityResolver,
        *,
        effects_factory: GenericEffectsFactory = _effects,
        operation_id_factory: Callable[[], str] = _operation_id,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.resolver = resolver
        self.effects_factory = effects_factory
        self.operation_id_factory = operation_id_factory
        self.clock = clock

    def _resolve(self, backend: application.GenericQualifiedBackend, deadline: float):
        try:
            value = self.resolver.resolve(backend, deadline=deadline)
        except Exception as exc:
            raise execution.GenericExecutionUnavailable("qualified authority is unavailable") from exc
        if (type(value) is not generic_authority.ResolvedGenericAuthority
                or value.backend != backend):
            raise execution.GenericExecutionUnavailable("qualified authority differs")
        return value

    def run(self, request: application.GenericHarnessRequest):
        if type(request) is not application.GenericHarnessRequest:
            return application.GenericHarnessUnavailable(
                application.GenericUnavailableReason.LIFECYCLE_UNAVAILABLE)
        deadline = self.clock() + spike_plan.BACKEND_OPERATION_TIMEOUT_SECONDS
        resolving = True
        try:
            with runtime_lock(self.store, deadline, clock=self.clock), ExitStack() as owners:
                authority = self._resolve(request.backend, deadline)
                facts = generic_authority.authority_facts(authority.runtime)
                effect = self.effects_factory(authority)
                owners.callback(effect.close)
                resolving = False
                # Unfinished journals are recovered with the current effects when their persisted
                # endpoint/platform facts equal these; no historical implementation is re-resolved.
                execution.recover_generic_operations(
                    self.store, effect, authority=facts, deadline=deadline, clock=self.clock)
                operation_id = self.operation_id_factory()
                sealed_request = spike_plan.SealedSpikeRequest(
                    request.plan, spike_plan.FRAMEWORK_SPIKE_POLICY)
                operation = planner.compile_docker_operation(
                    sealed_request, request.backend, request.run_id, operation_id,
                    _staging_token(operation_id, request.snapshot.snapshot_digest))

                def postflight(limit: float) -> str:
                    try:
                        current = self._resolve(request.backend, limit)
                        drifted = generic_authority.authority_facts(current.runtime) != facts
                    except Exception as exc:
                        raise execution.GenericPostflightChanged(
                            "qualified authority is unavailable after the spike") from exc
                    if drifted:
                        raise execution.GenericPostflightChanged("qualified authority drifted")
                    return current.backend.execution_binding_digest

                result = execution.execute_generic_operation(
                    self.store, operation, request.snapshot, effect,
                    backend_deadline=deadline, postflight=postflight, authority=facts,
                    clock=self.clock)
                operation_facts = application.GenericOperationFacts(
                    result.operation_digest, request.backend.qualification_record_digest,
                    request.backend.execution_binding_digest, result.image_digest,
                    result.input_digest, result.process_digest, result.topology_digest,
                    result.timing_digest)
                converted = application.GenericHarnessResult(
                    request.run_id, request.backend, request.plan_digest,
                    request.policy_digest, request.snapshot.snapshot_digest, operation_facts,
                    result.stdout, result.stderr, True, False, result.exit_code,
                    result.verdict, result.cleanup_proven, result.postflight_identity_digest)
                return application.validate_generic_harness_result(request, converted)
        except Exception as exc:
            # The typed exception owns its reason. Before execution (the runtime lock, scaffolding,
            # authority, effects) a failure is `authority-invalid`; any other untyped failure is
            # lifecycle.
            reason = (application.GenericUnavailableReason.AUTHORITY_INVALID if resolving
                      else exc.reason if isinstance(exc, execution.GenericExecutionUnavailable)
                      else application.GenericUnavailableReason.LIFECYCLE_UNAVAILABLE)
            detail = (str(exc) if isinstance(exc, execution.GenericExecutionUnavailable)
                      else "failed without a safely attributable Docker detail")
            return application.GenericHarnessUnavailable(reason, detail)
