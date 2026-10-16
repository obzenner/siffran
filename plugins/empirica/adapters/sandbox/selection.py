"""Passive selection of already-installed Docker qualification authority.

This adapter has no writer or repair path. It observes the current host/environment/runtime and
asks :class:`QualificationStore` to verify its existing current pointer and immutable record.
Every ordinary failure closes to ``spikes_unavailable``; process-control BaseException subclasses
remain visible to the caller.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from application.ports import QualifiedSpikeBackend, SpikesUnavailable

from . import docker, environment
from .record import QualificationStore


def inspect_runtime(
    current_environment: environment.EnvironmentIdentity, *,
    runner: docker.Runner | None = None, cli_path: str | None = None,
    ambient_environment: Mapping[str, str] | None = None,
) -> docker.DockerRuntimeIdentity:
    """Single passive five-read runtime inspection used by selection and execution.

    Docker context/host precedence is evaluated from the same ambient environment as
    qualification. The closed endpoint-only environment begins only after discovery.
    """
    identity = docker.discover_docker(
        runner if runner is not None else docker.BoundedRunner(),
        cli_path=cli_path, env=ambient_environment)
    docker.validate_runtime_identity(identity)
    return identity


class QualificationAuthoritySelector:
    """Read and verify current authority once, without installing or mutating anything."""

    def __init__(
        self, store: QualificationStore | None = None, *,
        environment_inspector: Callable[[], environment.EnvironmentIdentity] = environment.inspect_environment,
        runtime_inspector: Callable[[environment.EnvironmentIdentity], Mapping[str, Any]] = inspect_runtime,
    ) -> None:
        self._environment_inspector = environment_inspector
        self._runtime_inspector = runtime_inspector
        self._store = (store if store is not None else QualificationStore(
            environment_inspector=environment_inspector))

    def select(self) -> QualifiedSpikeBackend | SpikesUnavailable:
        try:
            current_environment = self._environment_inspector()
            checked_environment = environment.validate_environment_identity(
                current_environment, require_available=True)
            current_runtime = self._runtime_inspector(current_environment)
            checked_runtime = docker.validate_runtime_identity(current_runtime)
            authority = self._store.load_current(
                expected_environment=checked_environment,
                expected_runtime=checked_runtime,
            )
            return QualifiedSpikeBackend(
                authority["qualification_record_digest"],
                authority["execution_binding_digest"],
            )
        except Exception as exc:  # fail closed; cancellation/termination must still propagate
            return SpikesUnavailable(f"{type(exc).__name__}: {exc}")
