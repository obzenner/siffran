"""One-container `/2` Docker lifecycle, exact-ID cleanup, and crash recovery."""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from application import generic_spike as application
from core import spike_plan
from core.canonical import canonical_digest

from . import common, generic_admission as admission, generic_journal, generic_staging, planner
from .common import runtime_lock

CONTROL_SECONDS = 10.0
CLEANUP_RESERVE_SECONDS = 30.0


class GenericExecutionUnavailable(RuntimeError):
    pass


class GenericEffects(Protocol):
    def run(self, argv: tuple[str, ...], *, timeout_seconds: float,
            output_limit_bytes: int = ...) -> admission.RawProcessObservation: ...
    def attach(self, argv: tuple[str, ...], *, timeout_seconds: float,
               stdout_limit: int, stderr_limit: int) -> admission.RawAttachObservation: ...
    def close(self) -> None: ...


def concrete_effects(docker_cli: Path, environment: dict[str, str], cwd: Path,
                     **kwargs) -> GenericEffects:
    from .generic_effects import ConcreteGenericEffects
    return ConcreteGenericEffects(docker_cli, environment, cwd, **kwargs)


@dataclass(frozen=True, slots=True)
class QualificationExecutionControls:
    """Host-only qualification controls; never constructed from an author request."""
    workload_timeout_seconds: float
    cleanup_reserve_seconds: float = 10.0
    during_attach: Callable[[GenericEffects, generic_journal.GenericJournal, Path, float,
                             Callable[[], bool]], None] | None = None

    def __post_init__(self) -> None:
        for value in (self.workload_timeout_seconds, self.cleanup_reserve_seconds):
            if (type(value) not in {int, float} or isinstance(value, bool)
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("qualification execution controls require positive finite bounds")
        if self.workload_timeout_seconds > spike_plan.WORKLOAD_TIMEOUT_SECONDS:
            raise ValueError("qualification cannot extend the production workload bound")
        if self.during_attach is not None and not callable(self.during_attach):
            raise ValueError("qualification during-attach probe must be callable")


@dataclass(frozen=True, slots=True)
class GenericExecutionResult:
    operation_digest: str
    stdout: application.GenericStreamFact
    stderr: application.GenericStreamFact
    exit_code: int
    verdict: application.GenericVerdict
    image_digest: str
    input_digest: str
    process_digest: str
    topology_digest: str
    timing_digest: str
    cleanup_proven: bool
    postflight_identity_digest: str


def _remaining(deadline: float, clock: Callable[[], float], label: str) -> float:
    if type(deadline) not in {int, float} or isinstance(deadline, bool) or not math.isfinite(deadline):
        raise GenericExecutionUnavailable(label + " deadline is malformed")
    remaining = deadline - clock()
    if remaining <= 0:
        raise GenericExecutionUnavailable(label + " deadline expired")
    return remaining


def _resolve(operation: planner.ResolvedDockerOperation, journal: generic_journal.GenericJournal,
             staging: Path, argv: tuple[str, ...]) -> tuple[str, ...]:
    result = []
    for item in argv:
        if item == operation.container.full_id_token:
            if journal.container_id is None:
                raise GenericExecutionUnavailable("container destructive ID is not durable")
            item = journal.container_id
        elif operation.network is not None and item == operation.network.full_id_token:
            if journal.network_id is None:
                raise GenericExecutionUnavailable("network destructive ID is not durable")
            item = journal.network_id
        elif item == operation.container.cidfile_token:
            item = str(journal.cidfile)
        marker = "staging-token:" + operation.staging_token
        if marker in item:
            item = item.replace(marker, str(staging))
        result.append(item)
    return tuple(result)


def _diagnostic(kind: str, raw: admission.RawProcessObservation) -> dict[str, object]:
    tail = raw.stderr.data[-4096:].decode("utf-8", errors="replace")
    return {"kind": kind, "returncode": raw.returncode,
            "stderr_bytes": raw.stderr.bytes_seen, "stderr_sha256": raw.stderr.sha256,
            "stderr_tail": tail}


def _control(effects: GenericEffects, argv: tuple[str, ...], deadline: float,
             clock: Callable[[], float]) -> admission.RawProcessObservation:
    timeout = min(CONTROL_SECONDS, _remaining(deadline, clock, "Docker control"))
    try:
        return effects.run(argv, timeout_seconds=timeout, output_limit_bytes=256 * 1024)
    except Exception as exc:
        raise GenericExecutionUnavailable("Docker control process launch or capture failed") from exc


def _json_control(effects: GenericEffects, argv: tuple[str, ...], deadline: float,
                  clock: Callable[[], float]) -> tuple[admission.RawProcessObservation, dict]:
    raw = _control(effects, argv, deadline, clock)
    try:
        admission.admit_process(raw, argv, stdout_ceiling=256 * 1024,
                                stderr_ceiling=256 * 1024)
        return raw, admission.json_object(raw.stdout.data)
    except admission.DockerObservationError as exc:
        raise GenericExecutionUnavailable("required Docker JSON was unavailable") from exc


def _candidate_argv(operation: planner.ResolvedDockerOperation) -> tuple[str, ...]:
    assert operation.network is not None
    return ("network", "ls", "--no-trunc", "--filter",
            "name=^" + operation.network.name + "$", "--format", "{{.ID}}")


def _adopt_network_intent(
    store: generic_journal.GenericJournalStore,
    row: generic_journal.GenericJournal,
    effects: GenericEffects,
    deadline: float,
    clock: Callable[[], float],
) -> generic_journal.GenericJournal:
    operation = row.operation
    if operation.network is None or not row.has_event("network_intent"):
        return row
    if row.network_id is None:
        argv = _candidate_argv(operation)
        raw = _control(effects, argv, deadline, clock)
        try:
            candidates = admission.admit_exact_name_candidates(raw, argv)
        except admission.DockerObservationError as exc:
            raise GenericExecutionUnavailable("network intent candidate discovery was ambiguous") from exc
        if not candidates:
            return store.transition(
                operation.operation_id, "network_intent", "network_intent_absence_proved")
        if len(candidates) != 1:
            raise GenericExecutionUnavailable("network intent has ambiguous exact-name candidates")
        candidate = candidates[0]
        argv = ("network", "inspect", "--format", planner.NETWORK_INSPECT_FORMAT, candidate)
        _, document = _json_control(effects, argv, deadline, clock)
        try:
            cid_candidate = store.read_cidfile(operation.operation_id)
        except generic_journal.GenericJournalError:
            raise
        try:
            admission.admit_recovery_network(
                operation, document, network_full_id=candidate,
                container_full_id=row.container_id or cid_candidate)
        except admission.DockerObservationError as exc:
            raise GenericExecutionUnavailable("network intent candidate ownership was refused") from exc
        row = store.record_full_id(operation.operation_id, "network", candidate)
    return row


def _adopt_container_cidfile(
    store: generic_journal.GenericJournalStore,
    row: generic_journal.GenericJournal,
    effects: GenericEffects,
    staging: Path,
    deadline: float,
    clock: Callable[[], float],
) -> generic_journal.GenericJournal:
    if row.container_id is not None or not row.has_event("container_intent"):
        return row
    candidate = store.read_cidfile(row.operation.operation_id)
    if candidate is None:
        return row
    argv = ("container", "inspect", "--format", planner.CONTAINER_INSPECT_FORMAT, candidate)
    _, document = _json_control(effects, argv, deadline, clock)
    try:
        admission.admit_static_container(
            row.operation, document, container_full_id=candidate,
            network_full_id=row.network_id, staging_path=staging)
    except admission.DockerObservationError as exc:
        raise GenericExecutionUnavailable("cidfile container ownership was refused") from exc
    return store.record_full_id(row.operation.operation_id, "container", candidate)


def _remove_exact(
    store: generic_journal.GenericJournalStore,
    row: generic_journal.GenericJournal,
    effects: GenericEffects,
    resource: str,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    full_id = row.container_id if resource == "container" else row.network_id
    if full_id is None:
        return
    if resource == "container":
        remove = ("container", "rm", "--force", "--volumes", full_id)
        absent = ("container", "inspect", full_id)
    else:
        remove = ("network", "rm", full_id)
        absent = ("network", "inspect", full_id)
    raw_remove = _control(effects, remove, deadline, clock)
    # Removal success is diagnostic only; the exact-ID inspect below is the cleanup proof.
    _ = raw_remove
    raw_absent = _control(effects, absent, deadline, clock)
    try:
        admission.admit_exact_absence(raw_absent, absent, resource)
    except admission.DockerObservationError as exc:
        raise GenericExecutionUnavailable(resource + " exact-ID absence was not proved") from exc


def _recover_row(
    execution_store: common.ExecutionStore,
    journals: generic_journal.GenericJournalStore,
    row: generic_journal.GenericJournal,
    effects: GenericEffects,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    operation = row.operation
    staging = execution_store.staging / operation.operation_id
    if row.document["state"] == "removed":
        try:
            generic_staging.cleanup_generic_staging(execution_store, operation.operation_id)
        except generic_staging.GenericStagingError as exc:
            raise GenericExecutionUnavailable("recovery staging cleanup was refused") from exc
        journals.finish(operation.operation_id)
        return
    row = _adopt_network_intent(journals, row, effects, deadline, clock)
    row = _adopt_container_cidfile(journals, row, effects, staging, deadline, clock)
    if (row.has_event("container_intent") and row.container_id is None
            and not row.has_event("container_intent_absence_proved")):
        raise GenericExecutionUnavailable("container intent lacks durable full-ID authority")
    if (row.has_event("network_intent") and row.network_id is None
            and not row.has_event("network_intent_absence_proved")):
        raise GenericExecutionUnavailable("network intent absence is not proved")
    if row.container_id is not None:
        argv = ("container", "inspect", "--format", planner.CONTAINER_INSPECT_FORMAT,
                row.container_id)
        raw = _control(effects, argv, deadline, clock)
        if raw.returncode == 0:
            try:
                admission.admit_process(raw, argv, stdout_ceiling=256 * 1024,
                                        stderr_ceiling=256 * 1024)
                document = admission.json_object(raw.stdout.data)
                admission.admit_static_container(
                    operation, document, container_full_id=row.container_id,
                    network_full_id=row.network_id, staging_path=staging)
            except admission.DockerObservationError as exc:
                raise GenericExecutionUnavailable("recovery container identity was refused") from exc
        else:
            try:
                admission.admit_exact_absence(raw, argv, "container")
            except admission.DockerObservationError as exc:
                raise GenericExecutionUnavailable("recovery container state is ambiguous") from exc
    if row.network_id is not None:
        argv = ("network", "inspect", "--format", planner.NETWORK_INSPECT_FORMAT, row.network_id)
        raw = _control(effects, argv, deadline, clock)
        if raw.returncode == 0:
            try:
                admission.admit_process(raw, argv, stdout_ceiling=256 * 1024,
                                        stderr_ceiling=256 * 1024)
                admission.admit_recovery_network(
                    operation, admission.json_object(raw.stdout.data),
                    network_full_id=row.network_id, container_full_id=row.container_id)
            except admission.DockerObservationError as exc:
                raise GenericExecutionUnavailable("recovery network identity was refused") from exc
        else:
            try:
                admission.admit_exact_absence(raw, argv, "network")
            except admission.DockerObservationError as exc:
                raise GenericExecutionUnavailable("recovery network state is ambiguous") from exc
    if row.document["state"] != "cleanup":
        row = journals.transition(operation.operation_id, "cleanup", "recovery_cleanup")
    _remove_exact(journals, row, effects, "container", deadline, clock)
    _remove_exact(journals, row, effects, "network", deadline, clock)
    try:
        generic_staging.cleanup_generic_staging(execution_store, operation.operation_id)
    except generic_staging.GenericStagingError as exc:
        raise GenericExecutionUnavailable("recovery staging cleanup was refused") from exc
    journals.transition(operation.operation_id, "removed", "recovery_absence_proved")
    journals.finish(operation.operation_id)


def recover_generic_operations(
    execution_store: common.ExecutionStore,
    effects_for: Callable[[planner.ResolvedDockerOperation], GenericEffects],
    *, deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, ...]:
    journals = generic_journal.GenericJournalStore(execution_store)
    recovered = []
    for row in journals.scan():
        effects = effects_for(row.operation)
        try:
            try:
                _recover_row(execution_store, journals, row, effects, deadline, clock)
            except GenericExecutionUnavailable as exc:
                raise GenericExecutionUnavailable(
                    f"stuck operation {row.operation.operation_id}: {exc}") from exc
        finally:
            effects.close()
        recovered.append(row.operation.operation_id)
    return tuple(recovered)


def execute_generic_operation(
    execution_store: common.ExecutionStore,
    operation: planner.ResolvedDockerOperation,
    snapshot: application.GenericExecutionSnapshot,
    effects: GenericEffects,
    *,
    backend_deadline: float,
    postflight: Callable[[float], str],
    clock: Callable[[], float] = time.monotonic,
    qualification_controls: QualificationExecutionControls | None = None,
) -> GenericExecutionResult:
    """Execute one already-CAS-committed request and admit a result only after cleanup."""
    if qualification_controls is not None and type(qualification_controls) is not QualificationExecutionControls:
        raise GenericExecutionUnavailable("qualification execution controls are malformed")
    journals = generic_journal.GenericJournalStore(execution_store)
    row = journals.begin(operation)
    failure: BaseException | None = None
    attach: admission.RawAttachObservation | None = None
    terminal: admission.TerminalContainerObservation | None = None
    manifest: generic_staging.GenericStagingManifest | None = None
    try:
        manifest = generic_staging.materialize_generic_staging(
            execution_store, operation.operation_id, operation.plan, snapshot,
            operation.staging_token)
        row = journals.transition(operation.operation_id, "staged", "staging_published")
        staging = manifest.root
        cleanup_reserve = (CLEANUP_RESERVE_SECONDS if qualification_controls is None
                           else qualification_controls.cleanup_reserve_seconds)
        effect_deadline = backend_deadline - cleanup_reserve
        if operation.network is not None:
            row = journals.transition(operation.operation_id, "network_intent", "network_intent")
            argv = operation.command("network_create").argv
            try:
                raw = _control(effects, argv, effect_deadline, clock)
            except GenericExecutionUnavailable:
                raw = None
            if raw is not None:
                try:
                    network_id = admission.admit_created_full_id(raw, argv)
                    row = journals.record_full_id(operation.operation_id, "network", network_id)
                except admission.DockerObservationError:
                    pass
            if row.network_id is None:
                row = _adopt_network_intent(journals, row, effects, effect_deadline, clock)
            if row.network_id is None:
                raise GenericExecutionUnavailable("network create did not yield durable authority")
            diagnostic = None if raw is None else _diagnostic("network_create", raw)
            row = journals.transition(operation.operation_id, "network_created", "network_created",
                                      diagnostic)
        row = journals.transition(operation.operation_id, "container_intent", "container_intent")
        create_argv = _resolve(operation, row, staging, operation.command("container_create").argv)
        try:
            raw_create = _control(effects, create_argv, effect_deadline, clock)
        except GenericExecutionUnavailable:
            raw_create = None
        if raw_create is not None:
            try:
                admission.admit_process(raw_create, create_argv, stdout_ceiling=256 * 1024,
                                        stderr_ceiling=256 * 1024)
            except admission.DockerObservationError:
                pass
        row = _adopt_container_cidfile(journals, row, effects, staging, effect_deadline, clock)
        if row.container_id is None and raw_create is not None and raw_create.returncode != 0:
            row = journals.transition(
                operation.operation_id, "container_intent", "container_intent_absence_proved",
                _diagnostic("container_create_failed_without_cidfile", raw_create))
        if row.container_id is None:
            raise GenericExecutionUnavailable("container create did not yield a secure cidfile ID")
        diagnostic = None if raw_create is None else _diagnostic("container_create", raw_create)
        row = journals.transition(operation.operation_id, "container_created", "container_created",
                                  diagnostic)
        argv = _resolve(operation, row, staging, operation.command("container_static_inspect").argv)
        raw_static, document = _json_control(effects, argv, effect_deadline, clock)
        admission.admit_static_container(
            operation, document, container_full_id=row.container_id,
            network_full_id=row.network_id, staging_path=staging)
        row = journals.transition(operation.operation_id, "static_verified", "static_verified",
                                  _diagnostic("container_static_inspect", raw_static))
        if operation.network is not None:
            argv = _resolve(operation, row, staging,
                            operation.command("network_prestart_inspect").argv)
            raw_network, document = _json_control(effects, argv, effect_deadline, clock)
            admission.admit_prestart_network(
                operation, document, network_full_id=row.network_id)
            row = journals.transition(operation.operation_id, "static_verified",
                                      "network_prestart_verified",
                                      _diagnostic("network_prestart_inspect", raw_network))
        row = journals.transition(operation.operation_id, "attaching", "attach_started")
        argv = _resolve(operation, row, staging, operation.command("container_start_attach").argv)
        workload_limit = (spike_plan.WORKLOAD_TIMEOUT_SECONDS
                          if qualification_controls is None
                          else qualification_controls.workload_timeout_seconds)
        workload = min(workload_limit, _remaining(effect_deadline, clock, "workload"))
        if qualification_controls is None or qualification_controls.during_attach is None:
            attach = effects.attach(
                argv, timeout_seconds=workload, stdout_limit=spike_plan.STREAM_BYTES,
                stderr_limit=spike_plan.STREAM_BYTES)
        else:
            outcome: dict[str, object] = {}

            done = threading.Event()

            def run_attach() -> None:
                try:
                    outcome["attach"] = effects.attach(
                        argv, timeout_seconds=workload, stdout_limit=spike_plan.STREAM_BYTES,
                        stderr_limit=spike_plan.STREAM_BYTES)
                except BaseException as exc:
                    outcome["error"] = exc
                finally:
                    done.set()

            worker = threading.Thread(target=run_attach, name="empirica-qualification-attach")
            worker.start()
            callback_error: BaseException | None = None
            try:
                qualification_controls.during_attach(
                    effects, row, staging, effect_deadline, done.is_set)
            except BaseException as exc:
                callback_error = exc
            worker.join(timeout=max(0.0, _remaining(backend_deadline, clock, "attach join")))
            if worker.is_alive():
                raise GenericExecutionUnavailable("qualification attach worker did not terminate")
            if callback_error is not None:
                raise callback_error
            error = outcome.get("error")
            if isinstance(error, BaseException):
                raise error
            observed_attach = outcome.get("attach")
            if type(observed_attach) is not admission.RawAttachObservation:
                raise GenericExecutionUnavailable("qualification attach observation is malformed")
            attach = observed_attach
        argv = _resolve(operation, row, staging,
                        operation.command("container_terminal_inspect").argv)
        raw_terminal, document = _json_control(effects, argv, backend_deadline, clock)
        terminal = admission.admit_terminal_container(
            operation, document, container_full_id=row.container_id,
            network_full_id=row.network_id)
        row = journals.transition(operation.operation_id, "terminal_observed", "terminal_observed",
                                  _diagnostic("container_terminal_inspect", raw_terminal))
        admission.admit_natural_verdict(terminal, attach)
    except BaseException as exc:
        failure = exc
    cleanup_failure: BaseException | None = None
    try:
        row = journals.load(operation.operation_id)
        if (row.has_event("container_intent") and row.container_id is None
                and not row.has_event("container_intent_absence_proved")):
            raise GenericExecutionUnavailable("container intent lacks durable full-ID authority")
        if (row.has_event("network_intent") and row.network_id is None
                and not row.has_event("network_intent_absence_proved")):
            raise GenericExecutionUnavailable("network intent absence is not proved")
        if row.document["state"] != "cleanup":
            row = journals.transition(operation.operation_id, "cleanup", "cleanup_started")
        _remove_exact(journals, row, effects, "container", backend_deadline, clock)
        _remove_exact(journals, row, effects, "network", backend_deadline, clock)
        generic_staging.cleanup_generic_staging(execution_store, operation.operation_id)
        identity = postflight(backend_deadline)
        if identity != operation.backend.execution_binding_digest:
            raise GenericExecutionUnavailable("postflight authority identity drifted")
        journals.transition(operation.operation_id, "removed", "cleanup_absence_proved")
        journals.finish(operation.operation_id)
    except BaseException as exc:
        cleanup_failure = exc
    if cleanup_failure is not None:
        detail = "generic cleanup or postflight was unavailable: " + str(cleanup_failure)
        if failure is not None:
            detail += "; lifecycle: " + str(failure)
        raise GenericExecutionUnavailable(detail) from cleanup_failure
    if failure is not None:
        raise GenericExecutionUnavailable("generic lifecycle was unavailable: " + str(failure)) from failure
    assert attach is not None and terminal is not None and manifest is not None
    verdict = admission.admit_natural_verdict(terminal, attach)
    return GenericExecutionResult(
        operation.digest,
        application.GenericStreamFact(attach.process.stdout.bytes_seen, attach.process.stdout.sha256),
        application.GenericStreamFact(attach.process.stderr.bytes_seen, attach.process.stderr.sha256),
        terminal.exit_code,
        application.GenericVerdict.PASS if verdict is spike_plan.NaturalVerdict.PASS
        else application.GenericVerdict.FAIL,
        operation.plan.image.image_id, snapshot.snapshot_digest,
        canonical_digest({"argv": list(operation.plan.argv), "container": operation.container.to_data()}),
        canonical_digest(None if operation.network is None else operation.network.to_data()),
        canonical_digest({"workload_seconds": spike_plan.WORKLOAD_TIMEOUT_SECONDS,
                          "operation_seconds": operation.request.operation_timeout_seconds}),
        True, operation.backend.execution_binding_digest)


def execute_with_lock(*args, clock: Callable[[], float] = time.monotonic, **kwargs):
    store = args[0]
    deadline = kwargs["backend_deadline"]
    with runtime_lock(store, deadline, clock=clock):
        return execute_generic_operation(*args, clock=clock, **kwargs)
