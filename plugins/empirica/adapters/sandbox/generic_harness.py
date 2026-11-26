"""Production one-container `/2` Docker harness over exact qualified authority."""
from __future__ import annotations

import secrets
import time
from contextlib import ExitStack, nullcontext
from typing import Callable, Protocol

from application import generic_spike as application
from core import spike_plan
from core.canonical import canonical_digest
from core.records import RunKey

from . import common, generic_authority, generic_execution as execution, planner
from .common import runtime_lock
from .run_qualification import RuntimeLease


class GenericAuthorityResolver(Protocol):
    def resolve(self, backend: application.GenericQualifiedBackend, *, deadline: float,
                run: RunKey | None = None) -> generic_authority.ResolvedGenericAuthority: ...


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

    def _resolve(self, backend: application.GenericQualifiedBackend, deadline: float,
                 run: RunKey | None):
        try:
            value = self.resolver.resolve(backend, deadline=deadline, run=run)
        except Exception as exc:
            failure = execution.GenericExecutionUnavailable("qualified authority is unavailable")
            failure.reason = generic_authority.stale_reason(
                exc, application.GenericUnavailableReason.AUTHORITY_INVALID)
            raise failure from exc
        if (type(value) is not generic_authority.ResolvedGenericAuthority
                or value.backend != backend):
            raise execution.GenericExecutionUnavailable("qualified authority differs")
        return value

    def run(self, request: application.GenericHarnessRequest, lease: object | None = None):
        """Run one sealed request under the shared runtime lock: the caller's held ``lease`` (from
        ``run_qualification.hold_runtime_lock`` over this harness's store), whose deadline also
        bounds the run, or the lock this call takes itself."""
        if type(request) is not application.GenericHarnessRequest:
            return application.GenericHarnessUnavailable(
                application.GenericUnavailableReason.LIFECYCLE_UNAVAILABLE)
        deadline = self.clock() + spike_plan.BACKEND_OPERATION_TIMEOUT_SECONDS
        if lease is not None:
            if (type(lease) is not RuntimeLease or not lease.held
                    or lease.root != self.store.home):
                return application.GenericHarnessUnavailable(
                    application.GenericUnavailableReason.AUTHORITY_INVALID,
                    "the runtime lease is not held over this harness's store")
            deadline = min(deadline, lease.deadline)
        resolving = True
        try:
            with (runtime_lock(self.store, deadline, clock=self.clock) if lease is None
                  else nullcontext()), ExitStack() as owners:
                authority = self._resolve(request.backend, deadline, request.run)
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
                        current = self._resolve(request.backend, limit, request.run)
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
            # authority, effects) a failure is `authority-invalid` unless the authority was
            # re-observed stale; any other untyped failure is lifecycle.
            typed = isinstance(exc, execution.GenericExecutionUnavailable)
            reason = (exc.reason if typed and (
                          not resolving or exc.reason in application.AUTHORITY_STALE_REASONS)
                      else application.GenericUnavailableReason.AUTHORITY_INVALID if resolving
                      else application.GenericUnavailableReason.LIFECYCLE_UNAVAILABLE)
            detail = (str(exc) if isinstance(exc, execution.GenericExecutionUnavailable)
                      else "failed without a safely attributable Docker detail")
            return application.GenericHarnessUnavailable(reason, detail)
