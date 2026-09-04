"""Host-neutral Methodologist core.

Models, pure validation, ordered phase progression, selection-decision shapes,
and capability ports for the `think` skill — with no dependency on any host
tool or runtime path. A host (Claude Code, Pi, a test) supplies the ports and
the filesystem; the core supplies the shapes and the rules.
"""

from .models import (
    Candidate,
    Methodology,
    Phase,
    ReasoningRun,
    Registry,
    RegistryEntry,
    RegistrySchema,
    SelectionDecision,
    check_phase_numbering,
)
from .parsing import (
    load_methodology,
    load_registry,
    parse_methodology,
    parse_registry,
)
from .ports import HumanPort, TaskTracker
from .runner import register_phase_tasks
from .validation import (
    EXPECTED_PHASE_COUNT,
    validate_methodology_structure,
    validate_output_structure,
    validate_registry_against_files,
    validate_stance,
)

__all__ = [
    "Candidate",
    "Methodology",
    "Phase",
    "ReasoningRun",
    "Registry",
    "RegistryEntry",
    "RegistrySchema",
    "SelectionDecision",
    "check_phase_numbering",
    "load_methodology",
    "load_registry",
    "parse_methodology",
    "parse_registry",
    "HumanPort",
    "TaskTracker",
    "register_phase_tasks",
    "EXPECTED_PHASE_COUNT",
    "validate_methodology_structure",
    "validate_output_structure",
    "validate_registry_against_files",
    "validate_stance",
]
