"""One-container `/2` Docker lifecycle, exact-ID cleanup, and crash recovery."""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from application import generic_spike as application
from core import spike_plan
from core.canonical import canonical_digest

from . import common, generic_admission as admission, generic_journal, generic_staging, planner

CONTROL_SECONDS = 10.0
CLEANUP_RESERVE_SECONDS = 30.0


class GenericExecutionUnavailable(RuntimeError):
    """`reason` is the closed agent-visible cause; the message is private host detail."""
    reason = application.GenericUnavailableReason.LIFECYCLE_UNAVAILABLE


class GenericCleanupUnavailable(GenericExecutionUnavailable):
    reason = application.GenericUnavailableReason.CLEANUP_UNAVAILABLE


class GenericPostflightChanged(GenericExecutionUnavailable):
    reason = application.GenericUnavailableReason.POSTFLIGHT_CHANGED


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
    realization_digest: str


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
    # Bound the tail by encoded UTF-8 bytes: replacement characters expand invalid input.
    tail = raw.stderr.data[-4096:].decode("utf-8", errors="replace").encode("utf-8")[-4096:]
    tail = tail.decode("utf-8", errors="ignore")
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


def _present_document(effects: GenericEffects, resource: str, full_id: str, inspect_format: str,
                      deadline: float, clock: Callable[[], float]) -> dict | None:
    """Probe one exact ID with the bare absence grammar; read its projection only when present.

    `None` means Docker named that exact ID absent. Any other failure is ambiguous and raises, so a
    later well-formed answer can resolve it.
    """
    bare = (resource, "inspect", full_id)
    raw = _control(effects, bare, deadline, clock)
    if raw.returncode != 0:
        try:
            admission.admit_exact_absence(raw, bare, resource)
        except admission.DockerObservationError as exc:
            raise GenericExecutionUnavailable(resource + " exact-ID state is ambiguous") from exc
        return None
    _, document = _json_control(
        effects, (resource, "inspect", "--format", inspect_format, full_id), deadline, clock)
    return document


def _name_candidates(effects: GenericEffects, resource: str, name: str, deadline: float,
                     clock: Callable[[], float]) -> tuple[str, ...]:
    """Exact-name correlation only; each candidate still needs ownership before it is recorded."""
    if resource == "container":
        argv = ("container", "ls", "--all", "--no-trunc", "--filter",
                "name=^/?" + name + "$", "--format", "{{.ID}}")
    else:
        argv = ("network", "ls", "--no-trunc", "--filter", "name=^" + name + "$",
                "--format", "{{.ID}}")
    raw = _control(effects, argv, deadline, clock)
    try:
        return admission.admit_exact_name_candidates(raw, argv)
    except admission.DockerObservationError as exc:
        raise GenericExecutionUnavailable(resource + " exact-name candidate discovery was ambiguous") from exc


def _owned_container(row: generic_journal.GenericJournal, document: object, full_id: str) -> None:
    try:
        admission.admit_owned_container(
            document, container_full_id=full_id, name=row.operation.container.name,
            labels=row.operation.container.labels)
    except admission.DockerObservationError as exc:
        raise GenericExecutionUnavailable("container candidate ownership was refused") from exc


def _owned_network(row: generic_journal.GenericJournal, document: object, full_id: str) -> None:
    try:
        admission.admit_owned_network(
            row.operation, document, network_full_id=full_id, container_full_id=row.container_id)
    except admission.DockerObservationError as exc:
        raise GenericExecutionUnavailable("network candidate ownership was refused") from exc


def _adopt_network_intent(
    store: generic_journal.GenericJournalStore,
    row: generic_journal.GenericJournal,
    effects: GenericEffects,
    deadline: float,
    clock: Callable[[], float],
) -> generic_journal.GenericJournal:
    """Resolve a network create whose ID was lost: zero candidates is absence, one is adopted."""
    network = row.operation.network
    if (network is None or not row.has_event("network_intent") or row.network_id is not None
            or row.has_event("network_intent_absence_proved")):
        return row
    candidates = _name_candidates(effects, "network", network.name, deadline, clock)
    if len(candidates) > 1:
        raise GenericExecutionUnavailable("network intent has ambiguous exact-name candidates")
    document = (None if not candidates else _present_document(
        effects, "network", candidates[0], planner.NETWORK_INSPECT_FORMAT, deadline, clock))
    if document is None:
        return store.transition(
            row.operation.operation_id, "network_intent", "network_intent_absence_proved")
    _owned_network(row, document, candidates[0])
    return store.record_full_id(row.operation.operation_id, "network", candidates[0])


def _adopt_container_intent(
    store: generic_journal.GenericJournalStore,
    row: generic_journal.GenericJournal,
    effects: GenericEffects,
    deadline: float,
    clock: Callable[[], float],
) -> generic_journal.GenericJournal:
    """Recovery-time resolution of a create without a recorded ID: cidfile first, then exact name.

    The cidfile is Docker-written; when it is missing or empty the exact-name candidate set decides:
    zero proves absence, one is ownership-verified and recorded, more than one fails closed.
    """
    if (row.container_id is not None or not row.has_event("container_intent")
            or row.has_event("container_intent_absence_proved")):
        return row
    operation_id = row.operation.operation_id
    candidate = store.read_cidfile(operation_id)
    if candidate is None:
        candidates = _name_candidates(
            effects, "container", row.operation.container.name, deadline, clock)
        if len(candidates) > 1:
            raise GenericExecutionUnavailable("container intent has ambiguous exact-name candidates")
        candidate = candidates[0] if candidates else None
    document = (None if candidate is None else _present_document(
        effects, "container", candidate, planner.CONTAINER_INSPECT_FORMAT, deadline, clock))
    if document is None:
        return store.transition(operation_id, "container_intent", "container_intent_absence_proved")
    _owned_container(row, document, candidate)
    return store.record_full_id(operation_id, "container", candidate)


def _remove_exact(
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
    else:
        remove = ("network", "rm", full_id)
    absent = (resource, "inspect", full_id)
    # Removal success is diagnostic only; the exact-ID inspect below is the cleanup proof.
    _control(effects, remove, deadline, clock)
    raw_absent = _control(effects, absent, deadline, clock)
    try:
        admission.admit_exact_absence(raw_absent, absent, resource)
    except admission.DockerObservationError as exc:
        raise GenericExecutionUnavailable(resource + " exact-ID absence was not proved") from exc


def _require_resolved_intents(row: generic_journal.GenericJournal) -> None:
    if (row.has_event("container_intent") and row.container_id is None
            and not row.has_event("container_intent_absence_proved")):
        raise GenericExecutionUnavailable(
            "container intent lacks durable full-ID authority; the next recovery resolves it")
    if (row.has_event("network_intent") and row.network_id is None
            and not row.has_event("network_intent_absence_proved")):
        raise GenericExecutionUnavailable("network intent absence is not proved")


def _recover_row(
    execution_store: common.ExecutionStore,
    journals: generic_journal.GenericJournalStore,
    row: generic_journal.GenericJournal,
    effects: GenericEffects,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    """Total cleanup from persisted operation data: every state ends removed and finished."""
    operation_id = row.operation.operation_id
    if row.document["state"] != "removed":
        row = _adopt_network_intent(journals, row, effects, deadline, clock)
        row = _adopt_container_intent(journals, row, effects, deadline, clock)
        _require_resolved_intents(row)
        present = {}
        if row.container_id is not None:
            document = _present_document(effects, "container", row.container_id,
                                         planner.CONTAINER_INSPECT_FORMAT, deadline, clock)
            if document is not None:
                _owned_container(row, document, row.container_id)
            present["container"] = document is not None
        if row.network_id is not None:
            document = _present_document(effects, "network", row.network_id,
                                         planner.NETWORK_INSPECT_FORMAT, deadline, clock)
            if document is not None:
                _owned_network(row, document, row.network_id)
            present["network"] = document is not None
        if row.document["state"] != "cleanup":
            row = journals.transition(operation_id, "cleanup", "recovery_cleanup")
        for resource in ("container", "network"):
            if present.get(resource):
                _remove_exact(row, effects, resource, deadline, clock)
        try:
            generic_staging.cleanup_generic_staging(execution_store, operation_id)
        except generic_staging.GenericStagingError as exc:
            raise GenericExecutionUnavailable("recovery staging cleanup was refused") from exc
        journals.transition(operation_id, "removed", "recovery_absence_proved")
    else:
        try:
            generic_staging.cleanup_generic_staging(execution_store, operation_id)
        except generic_staging.GenericStagingError as exc:
            raise GenericExecutionUnavailable("recovery staging cleanup was refused") from exc
    journals.finish(operation_id)


def recover_generic_operations(
    execution_store: common.ExecutionStore,
    effects: GenericEffects,
    *, authority: Mapping[str, object],
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, ...]:
    """Finish every unfinished journal before a new mutation, on the daemon that owns its IDs.

    A journal is recovered only when its persisted daemon identity (endpoint, server OS and
    architecture) equals the current one. Capability, implementation, and planner facts are not
    compared, so an engine or plugin upgrade never strands a journal; a different endpoint or
    platform fails closed with the operation ID.
    """
    recovered = []
    try:
        journals = generic_journal.GenericJournalStore(execution_store)
        operation_ids = journals.operation_ids()
    except Exception as exc:
        raise GenericCleanupUnavailable(f"recovery journal root is unreadable: {exc}") from exc
    for operation_id in operation_ids:
        try:
            row = journals.load(operation_id)
            if row.authority != generic_journal.daemon_identity(authority):
                raise GenericExecutionUnavailable(
                    "journal belongs to a different Docker endpoint or platform")
            _recover_row(execution_store, journals, row, effects, deadline, clock)
        except Exception as exc:
            raise GenericCleanupUnavailable(f"stuck operation {operation_id}: {exc}") from exc
        recovered.append(operation_id)
    return tuple(recovered)


def execute_generic_operation(
    execution_store: common.ExecutionStore,
    operation: planner.ResolvedDockerOperation,
    snapshot: application.GenericExecutionSnapshot,
    effects: GenericEffects,
    *,
    backend_deadline: float,
    postflight: Callable[[float], str],
    authority: Mapping[str, object],
    clock: Callable[[], float] = time.monotonic,
    qualification_controls: QualificationExecutionControls | None = None,
) -> GenericExecutionResult:
    """Execute one already-CAS-committed request and admit a result only after cleanup."""
    if qualification_controls is not None and type(qualification_controls) is not QualificationExecutionControls:
        raise GenericExecutionUnavailable("qualification execution controls are malformed")
    journals = generic_journal.GenericJournalStore(execution_store)
    row = journals.begin(operation, authority=authority)
    realization: str | None = None
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
        raw_create = None
        try:
            raw_create = _control(effects, create_argv, effect_deadline, clock)
        except GenericExecutionUnavailable as exc:
            if getattr(exc.__cause__, "before_launch", False) is True:
                # Refused by the closed vocabulary before any process ran: nothing can exist.
                row = journals.transition(operation.operation_id, "container_intent",
                                          "container_intent_absence_proved")
                raise
        candidate = journals.read_cidfile(operation.operation_id)
        if candidate is None:
            # Whatever the CLI exit, the daemon may still be finishing the create, so absence is
            # never concluded here; the retained journal makes the next recovery do the exact-name
            # lookup (`_adopt_container_intent`). The bounded private create diagnostic stays in
            # the journal, so a persistent rejection is operator-readable.
            if raw_create is not None and type(raw_create.returncode) is int:
                journals.transition(operation.operation_id, "container_intent",
                                    "container_create_unresolved",
                                    _diagnostic("container_create", raw_create))
            raise GenericExecutionUnavailable(
                "container create outcome is unresolved without a cidfile ID")
        # The one semantic static inspection: ownership records the ID, then policy is admitted.
        argv = ("container", "inspect", "--format", planner.CONTAINER_INSPECT_FORMAT, candidate)
        raw_static, document = _json_control(effects, argv, effect_deadline, clock)
        _owned_container(row, document, candidate)
        row = journals.record_full_id(operation.operation_id, "container", candidate)
        diagnostic = None if raw_create is None else _diagnostic("container_create", raw_create)
        row = journals.transition(operation.operation_id, "container_created", "container_created",
                                  diagnostic)
        try:
            static = admission.admit_static_container(
                operation, document, container_full_id=row.container_id,
                network_full_id=row.network_id, staging_path=staging)
        except admission.DockerObservationError as exc:
            raise GenericExecutionUnavailable("container static admission was refused: " + str(exc)) from exc
        realization = static.realization_digest
        row = journals.transition(operation.operation_id, "static_verified", "static_verified",
                                  {**_diagnostic("container_static_inspect", raw_static),
                                   "realization_digest": realization})
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
        _require_resolved_intents(row)
        if row.document["state"] != "cleanup":
            row = journals.transition(operation.operation_id, "cleanup", "cleanup_started")
        _remove_exact(row, effects, "container", backend_deadline, clock)
        _remove_exact(row, effects, "network", backend_deadline, clock)
        generic_staging.cleanup_generic_staging(execution_store, operation.operation_id)
        identity = postflight(backend_deadline)
        if identity != operation.backend.execution_binding_digest:
            raise GenericPostflightChanged("postflight authority identity drifted")
        journals.transition(operation.operation_id, "removed", "cleanup_absence_proved")
        journals.finish(operation.operation_id)
    except BaseException as exc:
        cleanup_failure = exc
    if cleanup_failure is not None:
        detail = "generic cleanup was unavailable: " + str(cleanup_failure)
        if failure is not None:
            detail += "; lifecycle: " + str(failure)
        kind = (GenericPostflightChanged if isinstance(cleanup_failure, GenericPostflightChanged)
                else GenericCleanupUnavailable)
        raise kind(detail) from cleanup_failure
    if failure is not None:
        raise GenericExecutionUnavailable("generic lifecycle was unavailable: " + str(failure)) from failure
    if attach is None or terminal is None or manifest is None or realization is None:
        raise GenericExecutionUnavailable("generic lifecycle ended without its required observations")
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
        True, operation.backend.execution_binding_digest, realization)
