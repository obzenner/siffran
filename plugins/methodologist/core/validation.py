"""Pure validation over Methodologist core models.

`validate_registry_against_files` reproduces the registry/file sync check as a
pure function over parsed data (no filesystem access — it takes the set of file
stems). The rest are the strengthened structural checks: exactly six numbered
phases, a lineage and a prevents line, a well-formed output block wherever a
phase declares one, and the shared stance declaration. Every function returns a
list of human-readable error strings and mutates nothing.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Methodology, Registry, check_phase_numbering

EXPECTED_PHASE_COUNT = 6
STANCE_HEADER = "## The mandatory stance declaration"
STANCE_LINE_PREFIX = "> **Stance:**"


def validate_registry_against_files(
    registry: Registry, file_stems: Iterable[str]
) -> list[str]:
    """Registry and files must be in one-to-one sync (pure; no I/O)."""

    stems = set(file_stems)
    files_dir = registry.schema.files_dir
    required = set(registry.schema.required_fields)
    errors: list[str] = []
    registry_names: set[str] = set()

    for i, entry in enumerate(registry.entries):
        missing = required - set(entry.fields.keys())
        if missing:
            errors.append(f"Entry {i}: missing fields {missing}")
        if not entry.name:
            errors.append(f"Entry {i}: missing 'name'")
            continue
        registry_names.add(entry.name)
        if entry.name not in stems:
            errors.append(
                f"Registry has '{entry.name}' but {entry.name}.md not found in {files_dir}/"
            )

    for stem in sorted(stems - registry_names):
        errors.append(
            f"File '{stem}.md' exists in {files_dir}/ but not in registry"
        )

    return errors


def validate_output_structure(methodology: Methodology) -> list[str]:
    """Where a phase declares an output format, the fenced block must be present.

    "Where supported by current methodology files": phases that declare no
    output format are left alone — only a declared-but-empty block is an error.
    """

    errors: list[str] = []
    for phase in methodology.phases:
        if phase.output_format is not None and not phase.output_format.strip():
            errors.append(
                f"{methodology.name}: Phase {phase.number} declares "
                f"'**Output format:**' but has no fenced block"
            )
    return errors


def validate_methodology_structure(
    methodology: Methodology, *, expected_phase_count: int = EXPECTED_PHASE_COUNT
) -> list[str]:
    """Structural contract every methodology file must satisfy."""

    errors: list[str] = []
    if not methodology.lineage:
        errors.append(f"{methodology.name}: missing **Lineage:** line")
    if not methodology.prevents:
        errors.append(f"{methodology.name}: missing **Prevents:** line")

    for error in check_phase_numbering(
        methodology.phases, expected_count=expected_phase_count
    ):
        errors.append(f"{methodology.name}: {error}")

    for phase in methodology.phases:
        if not phase.title:
            errors.append(f"{methodology.name}: Phase {phase.number} has no title")

    errors.extend(validate_output_structure(methodology))
    return errors


def validate_stance(stance_text: str) -> list[str]:
    """The shared spine must carry its mandatory stance declaration."""

    errors: list[str] = []
    if STANCE_HEADER not in stance_text:
        errors.append(f"stance reference missing header '{STANCE_HEADER}'")
    if STANCE_LINE_PREFIX not in stance_text:
        errors.append(
            f"stance reference missing declaration line starting '{STANCE_LINE_PREFIX}'"
        )
    return errors
