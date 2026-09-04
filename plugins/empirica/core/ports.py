"""Implementation-agnostic persistence ports for empirica (ADR-31).

Two repositories with deliberately different concurrency models, plus one explicit migration port:

* :class:`RunRepository` — a run's *operational state* (lifecycle status, counters, the mutable
  bookkeeping a run updates as it progresses). Single logical document per key, guarded by
  optimistic concurrency (compare-and-set on an opaque :class:`~empirica.core.records.Revision`).
* :class:`ArtifactRepository` — a run's *claims and evidence*: an append-only set of immutable
  artifacts. No revisions and no overwrite; appends form a commutative, idempotent union, which is
  what lets concurrent writers add evidence without coordinating.
* :class:`MigrationPort` — the *only* sanctioned way data crosses a generation boundary.

These are ``typing.Protocol`` interfaces: an adapter (filesystem, database, object store) satisfies
a port by shape, without importing it. Nothing here references a concrete store, a path, a Git
command, or a Claude/Pi concept — that separation is the whole point of the port (ADR-31).
"""
from __future__ import annotations

from typing import Protocol, TypeVar

from .records import Artifact, MigrationReport, Read, Revision, RunKey

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


class MigrationPort(Protocol):
    """The single explicit path for moving a run's storage across a generation boundary.

    Reads never migrate implicitly (generation isolation, see
    :class:`~empirica.core.records.RunKey`); a schema/layout change is enacted by invoking this
    port, which copies ``source``'s operational state and artifacts into ``target`` — a different
    generation of the same run — and reports what moved. Leaving ``source`` intact makes the
    migration reversible: rollback is just pointing back at the older generation.
    """

    def migrate(self, source: RunKey, target: RunKey) -> MigrationReport:
        """Copy state and artifacts from ``source`` to ``target`` and return a
        :class:`~empirica.core.records.MigrationReport`. ``target`` must be absent beforehand
        (first-writer-wins still holds); ``source`` is not modified."""
        ...
