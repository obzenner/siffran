"""Host-neutral data models for the Methodologist core.

These types describe a methodology registry, an individual methodology and its
phases, an in-flight reasoning run, and the shape of a methodology-selection
decision. Nothing here reads the filesystem, names a host tool, or assumes a
particular agent runtime — the models are plain data plus the small amount of
behaviour (ordered phase progression) that genuinely belongs on the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Phase:
    """One numbered phase of a methodology."""

    number: int
    title: str
    # The fenced block a phase declares under "**Output format:**", if any.
    # None  -> the phase declares no output format.
    # ""    -> it declares one but the fenced block is missing/empty (malformed).
    output_format: str | None = None

    @property
    def has_output_format(self) -> bool:
        return bool(self.output_format)


@dataclass(frozen=True)
class Methodology:
    """A parsed methodology file: its metadata and ordered phases."""

    name: str
    lineage: str
    prevents: str
    phases: tuple[Phase, ...]
    title: str | None = None
    core_principle: str | None = None

    @property
    def phase_count(self) -> int:
        return len(self.phases)


@dataclass(frozen=True)
class RegistrySchema:
    """The self-describing schema block a registry declares about itself."""

    entries_key: str
    files_dir: str
    required_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegistryEntry:
    """One registry row. `fields` is the raw entry; `name` is its identity."""

    name: str
    fields: Mapping[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)


@dataclass(frozen=True)
class Registry:
    """A methodology registry: its schema plus its entries."""

    schema: RegistrySchema
    entries: tuple[RegistryEntry, ...]

    @property
    def names(self) -> set[str]:
        return {e.name for e in self.entries if e.name}


@dataclass(frozen=True)
class Candidate:
    """A methodology proposed for selection, with the rationale for it."""

    name: str
    rationale: str


@dataclass(frozen=True)
class SelectionDecision:
    """The outcome of matching a task against the registry.

    Exactly one of two shapes: `resolved` (a single methodology chosen, with the
    one-line reason the skill announces) or `ambiguous` (two or more candidates
    the human must choose between). The core owns the shape; the judgement that
    fills it belongs to the host.
    """

    chosen: str | None = None
    reason: str | None = None
    candidates: tuple[Candidate, ...] = ()

    @classmethod
    def resolved(cls, name: str, reason: str) -> "SelectionDecision":
        if not name:
            raise ValueError("a resolved decision needs a methodology name")
        if not reason:
            raise ValueError("a resolved decision needs a one-line reason")
        return cls(chosen=name, reason=reason)

    @classmethod
    def ambiguous(cls, candidates: "list[Candidate] | tuple[Candidate, ...]") -> "SelectionDecision":
        cand = tuple(candidates)
        if len(cand) < 2:
            raise ValueError("an ambiguous decision needs at least two candidates")
        return cls(candidates=cand)

    @property
    def is_resolved(self) -> bool:
        return self.chosen is not None

    @property
    def needs_human(self) -> bool:
        return not self.is_resolved


def check_phase_numbering(
    phases: "tuple[Phase, ...]", *, expected_count: int | None = None
) -> list[str]:
    """Return errors if phases are not numbered 1..N contiguously and ascending.

    Pure and shared: `validate_methodology_structure` reports these, and
    `ReasoningRun` refuses to start a run whose phases fail them.
    """

    errors: list[str] = []
    if not phases:
        errors.append("no phases found")
        return errors
    for position, phase in enumerate(phases, start=1):
        if phase.number != position:
            errors.append(
                f"phase in position {position} is numbered {phase.number} (expected {position})"
            )
    if expected_count is not None and len(phases) != expected_count:
        errors.append(f"expected {expected_count} phases, found {len(phases)}")
    return errors


class ReasoningRun:
    """Ordered, non-skipping progression through a methodology's phases.

    Encodes the SKILL.md execution rule that phases run sequentially and none is
    skipped: `advance()` completes the current phase and moves to the next, and
    there is deliberately no API to jump to an arbitrary phase.
    """

    def __init__(self, methodology: Methodology) -> None:
        numbering = check_phase_numbering(methodology.phases)
        if numbering:
            raise ValueError(
                f"cannot run '{methodology.name}': " + "; ".join(numbering)
            )
        self.methodology = methodology
        self._index = 0

    def current_phase(self) -> Phase | None:
        if self.is_complete():
            return None
        return self.methodology.phases[self._index]

    def advance(self) -> Phase | None:
        """Complete the current phase and move to the next; return the new one."""
        if self.is_complete():
            raise RuntimeError("run is already complete")
        self._index += 1
        return self.current_phase()

    def is_complete(self) -> bool:
        return self._index >= len(self.methodology.phases)

    def completed_phases(self) -> tuple[Phase, ...]:
        return self.methodology.phases[: self._index]

    def remaining_phases(self) -> tuple[Phase, ...]:
        return self.methodology.phases[self._index :]

    def progress(self) -> tuple[int, int]:
        """(completed, total)."""
        return (self._index, len(self.methodology.phases))
