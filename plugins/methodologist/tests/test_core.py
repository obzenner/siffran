#!/usr/bin/env python3
"""Committed regression suite for the host-neutral Methodologist core.

Run: python3 plugins/methodologist/tests/test_core.py   (stdlib only, no pytest)
     python3 -m unittest discover -s plugins/methodologist/tests
Exit 0 = all checks pass; nonzero = at least one failed.

Two layers:
  * unit — the pure models, ports, parsing, progression, selection shapes, and
    validation exercised with synthetic data, so a regression is localised.
  * integration — the strengthened validation run against the REAL registry.json
    and methodology files, so the executable checks stay honest about what ships.
"""

import sys
import unittest
from pathlib import Path

# Make the sibling `core` package importable without a package install.
PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))
SKILL_DIR = PLUGIN_DIR / "skills" / "think"
METHODOLOGIES_DIR = SKILL_DIR / "methodologies"
STANCE_FILE = SKILL_DIR / "references" / "evidence-over-recall.md"

from core import (  # noqa: E402
    EXPECTED_PHASE_COUNT,
    Candidate,
    Methodology,
    Phase,
    ReasoningRun,
    RegistryEntry,
    RegistrySchema,
    SelectionDecision,
    check_phase_numbering,
    load_methodology,
    load_registry,
    parse_methodology,
    parse_registry,
    register_phase_tasks,
    validate_methodology_structure,
    validate_output_structure,
    validate_registry_against_files,
    validate_stance,
)
from core.ports import HumanPort, TaskTracker  # noqa: E402


def _phases(n: int) -> tuple[Phase, ...]:
    return tuple(Phase(number=i, title=f"Phase {i}") for i in range(1, n + 1))


def _methodology(name: str = "demo", n: int = EXPECTED_PHASE_COUNT) -> Methodology:
    return Methodology(
        name=name, lineage="Someone (year)", prevents="a failure mode", phases=_phases(n)
    )


# --- test doubles for the ports (host-neutral, no tool names) ----------------


class _FakeTracker(TaskTracker):
    def __init__(self) -> None:
        self.created: list[str] = []
        self.started: list[str] = []
        self.completed: list[str] = []

    def create_task(self, title: str) -> str:
        task_id = f"t{len(self.created)}"
        self.created.append(title)
        return task_id

    def start_task(self, task_id: str) -> None:
        self.started.append(task_id)

    def complete_task(self, task_id: str) -> None:
        self.completed.append(task_id)


class _FakeHuman(HumanPort):
    def __init__(self, chosen: str = "", answer: str = "") -> None:
        self._chosen = chosen
        self._answer = answer

    def choose(self, prompt: str, options: tuple[str, ...]) -> str:
        return self._chosen or options[0]

    def ask(self, prompt: str) -> str:
        return self._answer


# --- models & progression ----------------------------------------------------


class TestReasoningRun(unittest.TestCase):
    def test_progresses_in_order_and_completes(self):
        run = ReasoningRun(_methodology(n=3))
        self.assertEqual(run.current_phase().number, 1)
        self.assertEqual(run.progress(), (0, 3))
        self.assertEqual(run.advance().number, 2)
        self.assertEqual(run.advance().number, 3)
        self.assertFalse(run.is_complete())
        self.assertIsNone(run.advance())
        self.assertTrue(run.is_complete())
        self.assertEqual(run.progress(), (3, 3))

    def test_completed_and_remaining_partition_the_phases(self):
        run = ReasoningRun(_methodology(n=4))
        run.advance()
        self.assertEqual([p.number for p in run.completed_phases()], [1])
        self.assertEqual([p.number for p in run.remaining_phases()], [2, 3, 4])

    def test_advance_past_end_raises(self):
        run = ReasoningRun(_methodology(n=1))
        run.advance()
        with self.assertRaises(RuntimeError):
            run.advance()

    def test_refuses_non_contiguous_phases(self):
        bad = Methodology(
            name="bad",
            lineage="x",
            prevents="y",
            phases=(Phase(1, "a"), Phase(3, "c")),
        )
        with self.assertRaises(ValueError):
            ReasoningRun(bad)


class TestPhaseNumbering(unittest.TestCase):
    def test_clean_sequence_has_no_errors(self):
        self.assertEqual(check_phase_numbering(_phases(6)), [])

    def test_gap_is_reported(self):
        errors = check_phase_numbering((Phase(1, "a"), Phase(3, "c")))
        self.assertTrue(errors)

    def test_empty_is_reported(self):
        self.assertEqual(check_phase_numbering(()), ["no phases found"])

    def test_expected_count_mismatch_is_reported(self):
        errors = check_phase_numbering(_phases(5), expected_count=6)
        self.assertTrue(any("expected 6" in e for e in errors))


class TestSelectionDecision(unittest.TestCase):
    def test_resolved_shape(self):
        d = SelectionDecision.resolved("invariant-analysis", "state must stay true")
        self.assertTrue(d.is_resolved)
        self.assertFalse(d.needs_human)
        self.assertEqual(d.chosen, "invariant-analysis")

    def test_resolved_requires_a_reason(self):
        with self.assertRaises(ValueError):
            SelectionDecision.resolved("x", "")

    def test_ambiguous_shape(self):
        d = SelectionDecision.ambiguous(
            [Candidate("a", "r1"), Candidate("b", "r2")]
        )
        self.assertFalse(d.is_resolved)
        self.assertTrue(d.needs_human)
        self.assertEqual(len(d.candidates), 2)

    def test_ambiguous_needs_two_candidates(self):
        with self.assertRaises(ValueError):
            SelectionDecision.ambiguous([Candidate("a", "r1")])


# --- ports --------------------------------------------------------------------


class TestPorts(unittest.TestCase):
    def test_register_phase_tasks_creates_and_starts_first(self):
        run = ReasoningRun(_methodology(n=3))
        tracker = _FakeTracker()
        ids = register_phase_tasks(run, tracker)
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(tracker.created), 3)
        self.assertIn("demo: Phase 1", tracker.created[0])
        self.assertEqual(tracker.started, [ids[0]])

    def test_human_port_double_answers(self):
        human = _FakeHuman(chosen="b", answer="42")
        self.assertEqual(human.choose("pick", ("a", "b")), "b")
        self.assertEqual(human.ask("how many?"), "42")


# --- parsing ------------------------------------------------------------------

_SAMPLE = """# Sample Methodology

**Lineage:** Someone (1970)
**Prevents:** a specific failure mode

## Core principle

The one idea underneath it all.

### Phase 1: First phase

Do the first thing.

**Output format:**
```
Result: <value>
```

### Phase 2: Second phase

Do the second thing. No output format here.
"""


class TestParsing(unittest.TestCase):
    def test_parses_metadata_and_phases(self):
        m = parse_methodology(_SAMPLE, name="sample")
        self.assertEqual(m.name, "sample")
        self.assertEqual(m.title, "Sample Methodology")
        self.assertEqual(m.lineage, "Someone (1970)")
        self.assertEqual(m.prevents, "a specific failure mode")
        self.assertEqual(m.core_principle, "The one idea underneath it all.")
        self.assertEqual([p.number for p in m.phases], [1, 2])
        self.assertEqual(m.phases[0].title, "First phase")

    def test_output_format_present_absent_and_none(self):
        m = parse_methodology(_SAMPLE, name="sample")
        self.assertEqual(m.phases[0].output_format, "Result: <value>")
        self.assertTrue(m.phases[0].has_output_format)
        self.assertIsNone(m.phases[1].output_format)

    def test_declared_but_empty_output_format_is_empty_string(self):
        text = "### Phase 1: X\n\n**Output format:**\n\nno fence follows\n"
        m = parse_methodology(text, name="x")
        self.assertEqual(m.phases[0].output_format, "")

    def test_parse_registry_builds_schema_and_entries(self):
        data = {
            "schema": {
                "entries_key": "methodologies",
                "files_dir": "methodologies",
                "required_fields": ["name", "lineage"],
            },
            "methodologies": [{"name": "a", "lineage": "x"}],
        }
        reg = parse_registry(data)
        self.assertEqual(reg.schema.entries_key, "methodologies")
        self.assertEqual(reg.names, {"a"})
        self.assertIsInstance(reg.entries[0], RegistryEntry)


# --- pure validation ----------------------------------------------------------


class TestRegistryValidation(unittest.TestCase):
    def _registry(self, entries):
        schema = RegistrySchema(
            entries_key="methodologies",
            files_dir="methodologies",
            required_fields=("name", "lineage"),
        )
        from core import Registry

        return Registry(
            schema=schema,
            entries=tuple(RegistryEntry(name=e.get("name", ""), fields=e) for e in entries),
        )

    def test_in_sync_has_no_errors(self):
        reg = self._registry([{"name": "a", "lineage": "x"}])
        self.assertEqual(validate_registry_against_files(reg, {"a"}), [])

    def test_registry_entry_without_file(self):
        reg = self._registry([{"name": "a", "lineage": "x"}])
        errors = validate_registry_against_files(reg, set())
        self.assertTrue(any("a.md not found" in e for e in errors))

    def test_file_without_registry_entry(self):
        reg = self._registry([{"name": "a", "lineage": "x"}])
        errors = validate_registry_against_files(reg, {"a", "orphan"})
        self.assertTrue(any("orphan.md" in e and "not in registry" in e for e in errors))

    def test_missing_required_field(self):
        reg = self._registry([{"name": "a"}])
        errors = validate_registry_against_files(reg, {"a"})
        self.assertTrue(any("missing fields" in e for e in errors))


class TestStructureValidation(unittest.TestCase):
    def test_well_formed_methodology_passes(self):
        self.assertEqual(validate_methodology_structure(_methodology()), [])

    def test_wrong_phase_count_flagged(self):
        errors = validate_methodology_structure(_methodology(n=5))
        self.assertTrue(any("expected 6" in e for e in errors))

    def test_missing_lineage_and_prevents_flagged(self):
        m = Methodology(name="m", lineage="", prevents="", phases=_phases(6))
        errors = validate_methodology_structure(m)
        self.assertTrue(any("Lineage" in e for e in errors))
        self.assertTrue(any("Prevents" in e for e in errors))

    def test_declared_empty_output_block_flagged(self):
        phases = list(_phases(6))
        phases[0] = Phase(1, "first", output_format="")
        m = Methodology(name="m", lineage="l", prevents="p", phases=tuple(phases))
        errors = validate_output_structure(m)
        self.assertTrue(any("no fenced block" in e for e in errors))

    def test_present_output_block_not_flagged(self):
        phases = list(_phases(6))
        phases[0] = Phase(1, "first", output_format="Result: x")
        m = Methodology(name="m", lineage="l", prevents="p", phases=tuple(phases))
        self.assertEqual(validate_output_structure(m), [])


class TestStanceValidation(unittest.TestCase):
    def test_valid_stance_passes(self):
        text = "## The mandatory stance declaration\n\n> **Stance:** ...\n"
        self.assertEqual(validate_stance(text), [])

    def test_missing_declaration_flagged(self):
        self.assertTrue(validate_stance("nothing here"))


# --- integration: the real shipped files --------------------------------------


class TestRealFiles(unittest.TestCase):
    def test_registry_and_files_in_sync(self):
        registry = load_registry(SKILL_DIR / "registry.json")
        file_stems = {p.stem for p in METHODOLOGIES_DIR.glob("*.md")}
        self.assertEqual(validate_registry_against_files(registry, file_stems), [])

    def test_every_methodology_has_six_numbered_phases_and_structure(self):
        md_files = sorted(METHODOLOGIES_DIR.glob("*.md"))
        self.assertTrue(md_files, "no methodology files found")
        for md in md_files:
            m = load_methodology(md)
            with self.subTest(methodology=m.name):
                self.assertEqual(
                    m.phase_count, EXPECTED_PHASE_COUNT, f"{m.name} phase count"
                )
                self.assertEqual(validate_methodology_structure(m), [])

    def test_real_stance_reference_declares_the_stance(self):
        self.assertTrue(STANCE_FILE.exists(), STANCE_FILE)
        self.assertEqual(validate_stance(STANCE_FILE.read_text()), [])


if __name__ == "__main__":
    unittest.main()
