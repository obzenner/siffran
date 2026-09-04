"""Immutable domain records for empirica's persistence layer (ADR-31).

These types are the vocabulary the persistence *ports* (see ``ports.py``) speak in. They are
deliberately free of any storage mechanism: nothing here knows about a filesystem path, a Git
object, a database row, or a Claude/Pi run. An adapter translates between these records and a
concrete store; the domain only ever holds these.

Every record is a frozen dataclass or a singleton — persistence code passes state *by value* and
may not mutate a record it was handed, which is what lets the same record be compared, cached, and
migrated without a defensive copy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RunKey:
    """The identity of one run's slice of storage.

    ``generation`` is what makes migration explicit rather than implicit. Two keys that differ
    only in ``generation`` name *different* storage slices: a write under generation 1 is invisible
    to a read at generation 2 (generation isolation). Data crosses a generation boundary only when
    a :class:`~empirica.core.ports.MigrationPort` copies it there on purpose — never as a silent
    side effect of a read. Bumping the generation is therefore a safe way to start a run's storage
    from a clean, empty slice while the previous generation stays intact for rollback.
    """

    project_id: str
    run_id: str
    generation: int


@dataclass(frozen=True)
class Revision:
    """An opaque optimistic-concurrency token minted by the store.

    Callers obtain a ``Revision`` from a read and hand the *same value* back to
    ``compare_and_set``; they must never parse, order, or synthesise the ``token``. Its only
    contract is value equality: two revisions are equal iff they denote the same stored version.
    The string form is an implementation detail of whichever adapter minted it (a content hash, a
    monotonic counter, an ETag) and is opaque to the domain.
    """

    token: str


class Absent(Enum):
    """The datum was never written to this key (a normal, non-error outcome).

    A single-member enum is used so ``ABSENT`` is a *typed* singleton usable in a ``Read`` union
    and matched with ``is ABSENT`` / ``match``. Absent is distinct from :class:`Corrupt`: absent
    means "nothing is here", corrupt means "something is here but it cannot be trusted".
    """

    ABSENT = "absent"


ABSENT = Absent.ABSENT


@dataclass(frozen=True)
class Corrupt:
    """The datum exists but could not be decoded or failed validation.

    Kept distinct from :data:`ABSENT` on purpose (ADR-31, mirroring the hooks' ``None`` vs
    ``__corrupt__`` split): an absent run is safe to (re)create, but a corrupt one must fail closed
    and surface ``reason`` rather than be silently overwritten as if it were empty.
    """

    reason: str


@dataclass(frozen=True)
class Present(Generic[T]):
    """A successful read: the decoded ``value`` and the ``revision`` it was read at.

    Feed ``revision`` straight back into ``compare_and_set`` to make the next write conditional on
    nothing having changed underneath.
    """

    value: T
    revision: Revision


# The three possible outcomes of a read. Absent and Corrupt are separated so callers can fail
# closed on corruption while treating absence as an ordinary empty state (ADR-31).
Read = Present[T] | Absent | Corrupt


@dataclass(frozen=True)
class Artifact:
    """One immutable claim-or-evidence artifact in the append-only argument store.

    ``artifact_id`` is a content address: a producer derives it from ``body`` so that equal bodies
    share an id and differing bodies never collide. This is what makes appending *commutative and
    idempotent* — the store holds a set of artifacts, so appending the same artifact twice, or the
    same artifacts in a different order, yields the identical set (union semantics; see
    ``ArtifactRepository.append``). ``body`` is an opaque, already-serialised payload: the GSN
    claim graph and in-toto evidence structure live one layer up and serialise *into* this string,
    keeping the port free of any claim/evidence schema.
    """

    artifact_id: str
    body: str


@dataclass(frozen=True)
class MigrationReport:
    """The outcome of one explicit :class:`~empirica.core.ports.MigrationPort` run.

    Returned so a migration is observable and auditable rather than a silent copy — the counts let
    a caller assert that the expected volume of state actually crossed the generation boundary.
    """

    source: RunKey
    target: RunKey
    runs_migrated: int
    artifacts_migrated: int


class Conflict(Exception):
    """Raised when a write's precondition on the stored revision was not met.

    Two cases, both first-writer-wins: ``create`` on a key that already exists, and
    ``compare_and_set`` whose ``expected`` revision no longer matches (or the key is now absent).
    The loser of the race gets this exception and must re-read before retrying — it must never
    clobber the winner's write.
    """

    def __init__(self, key: RunKey, expected: Revision | None, detail: str) -> None:
        self.key = key
        self.expected = expected
        self.detail = detail
        super().__init__(f"revision conflict on {key}: {detail}")
