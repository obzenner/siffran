"""Host-neutral Methodologist application service for ``methodologist/v1``.

The service owns protocol dispatch and resource validation while the existing
``core`` package remains authoritative for registry parsing, methodology
parsing, six-phase validation, and ordered reasoning-run construction. Hosts
supply only a resource directory; no host state or repository state is written.

Semantic auto-selection deliberately does not live here. A bare host command
must ask its model to apply the shared skill and registry, then submit the
selected *name* through this same service. That keeps selection semantic rather
than turning the bridge into a keyword router.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import (
    EXPECTED_PHASE_COUNT,
    ReasoningRun,
    parse_methodology,
    parse_registry,
    validate_methodology_structure,
    validate_registry_against_files,
)

PROTOCOL = "methodologist/v1"


class InvalidRequest(ValueError):
    """The wire envelope is malformed."""


def _fault(request_id: str, code: str) -> dict:
    return {
        "protocol": PROTOCOL,
        "request_id": request_id,
        "result": {"type": "Fault", "code": code},
    }


class MethodologistService:
    """Dispatch validated named selections against the shipped resources."""

    def __init__(self, skill_dir: Path) -> None:
        self._skill_dir = Path(skill_dir)

    def handle(self, request: object) -> dict:
        request_id = "unknown"
        try:
            request_id, command = self._parse_envelope(request)
            if command["type"] != "SelectMethodology":
                return _fault(request_id, "invalid_request")
            return self._select(request_id, command)
        except InvalidRequest:
            return _fault(request_id, "invalid_request")
        except (OSError, ValueError, json.JSONDecodeError):
            # Broken shipped resources are an invalid core response, never a
            # reason for a host adapter to fabricate phases.
            return _fault(request_id, "invalid_request")

    @staticmethod
    def _parse_envelope(request: object) -> tuple[str, dict]:
        if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
            raise InvalidRequest("invalid protocol")
        request_id = request.get("request_id")
        command = request.get("command")
        if not isinstance(request_id, str) or not request_id:
            raise InvalidRequest("request_id must be non-empty")
        if not isinstance(command, dict) or not isinstance(command.get("type"), str):
            raise InvalidRequest("command must have a type")
        return request_id, command

    def _select(self, request_id: str, command: dict) -> dict:
        intent = command.get("intent")
        requested = command.get("requested_methodology")
        if not isinstance(intent, str) or not intent.strip():
            raise InvalidRequest("intent must be non-empty")
        # A model, not this deterministic service, owns semantic selection.
        if not isinstance(requested, str) or not requested.strip():
            raise InvalidRequest("a named methodology is required")

        registry_data = json.loads((self._skill_dir / "registry.json").read_text())
        registry = parse_registry(registry_data)
        methodologies_dir = self._skill_dir / registry.schema.files_dir
        file_stems = {path.stem for path in methodologies_dir.glob("*.md")}
        if validate_registry_against_files(registry, file_stems):
            raise InvalidRequest("registry and methodology files are inconsistent")

        canonical = next(
            (name for name in registry.names if name.casefold() == requested.casefold()),
            None,
        )
        if canonical is None:
            return _fault(request_id, "unknown_methodology")

        methodology = parse_methodology(
            (methodologies_dir / f"{canonical}.md").read_text(), canonical
        )
        errors = validate_methodology_structure(
            methodology, expected_phase_count=EXPECTED_PHASE_COUNT
        )
        if errors:
            raise InvalidRequest("; ".join(errors))

        # Construction applies the core's ordered/non-skipping invariant too.
        ReasoningRun(methodology)
        phases = [
            {
                "id": f"phase-{phase.number}",
                "number": phase.number,
                "title": phase.title,
                **(
                    {"output_format": phase.output_format}
                    if phase.output_format is not None
                    else {}
                ),
            }
            for phase in methodology.phases
        ]
        return {
            "protocol": PROTOCOL,
            "request_id": request_id,
            "result": {
                "type": "MethodologySelected",
                "methodology": canonical,
                "reason": intent.strip(),
                "phases": phases,
            },
        }
