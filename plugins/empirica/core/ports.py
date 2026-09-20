"""Implementation-agnostic persistence ports for empirica (ADR-31).

Operational state uses compare-and-set; immutable claim/evidence artifacts use append-only union.
Adapters satisfy these ``typing.Protocol`` interfaces without depending on concrete storage or host
concepts.
"""
from __future__ import annotations

from typing import Protocol, TypeVar

from .records import Artifact, Read, Revision, RunKey

T = TypeVar("T")


class RunRepository(Protocol[T]):
    """CAS-guarded store for a run's single operational-state document.

    The value type ``T`` is left to the caller so the port stays free of any concrete state
    schema; an adapter serialises ``T`` however it likes. Reads and writes are keyed on the full
    :class:`~empirica.core.records.RunKey`, so a different ``generation`` is a different, isolated
    document.
    """

    def read(self, key: RunKey) -> Read[T]:
        """Return ``Present(value, revision)``, ``ABSENT`` (never written / other generation), or
        ``Corrupt(reason)`` (stored but undecodable). Must not raise for absence or corruption —
        those are values, so callers can fail closed on corruption without a try/except."""
        ...

    def create(self, key: RunKey, value: T) -> Revision:
        """Create the document for a previously-absent key and return its first revision.

        First-writer-wins: if a document already exists at ``key`` (even a corrupt one), raise
        :class:`~empirica.core.records.Conflict` rather than overwrite it. This is the safe
        initialiser — the winner of a creation race is whoever's ``create`` returned."""
        ...

    def compare_and_set(self, key: RunKey, value: T, expected: Revision) -> Revision:
        """Atomically replace the value iff the stored revision equals ``expected``; return the new
        revision. If the stored revision differs, the key is absent, or it is corrupt, raise
        :class:`~empirica.core.records.Conflict` and leave storage untouched — the caller must
        re-read and retry rather than clobber a concurrent write (ADR-31)."""
        ...


class ArtifactRepository(Protocol):
    """Append-only, content-addressed store for a run's claims and evidence.

    The stored value is the *set* of artifacts appended under a key. Because it is a set keyed on
    each artifact's content address, the store needs no revisions and no locking: two writers that
    append different evidence converge to the union regardless of order or retries.
    """

    def append(self, key: RunKey, artifact: Artifact) -> None:
        """Add ``artifact`` to the set under ``key``. The operation is **commutative** (order of
        appends does not affect the resulting set) and **idempotent** (appending an artifact
        already present is a no-op). Never overwrites an existing artifact — a content address
        binds to exactly one body, so a colliding id with a different body is a producer bug, not
        an update path."""
        ...

    def read(self, key: RunKey) -> Read[frozenset[Artifact]]:
        """Return ``Present(union, revision)`` with every artifact appended under ``key``, ``ABSENT``
        if nothing was ever appended (or it belongs to another generation), or ``Corrupt(reason)``
        if the stored set cannot be decoded. The ``revision`` is advisory (an opaque digest of the
        set) — ``append`` is unconditional and does not consume it."""
        ...
