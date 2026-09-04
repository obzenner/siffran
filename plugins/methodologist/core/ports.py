"""Capability ports the Methodologist core depends on.

A port is an abstract seam the host fills. The core never names a host tool
(no TaskCreate / TaskUpdate / Agent / Read / Glob / …) and never hard-codes a
runtime path; it talks to these interfaces and lets Claude Code, Pi, a test
double, or any other host supply the concrete behaviour.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TaskTracker(ABC):
    """Records phase progress as trackable tasks (SKILL.md Step 2 / Step 3)."""

    @abstractmethod
    def create_task(self, title: str) -> str:
        """Create a task and return an opaque id the host understands."""

    @abstractmethod
    def start_task(self, task_id: str) -> None:
        """Mark a task in progress."""

    @abstractmethod
    def complete_task(self, task_id: str) -> None:
        """Mark a task complete."""


class HumanPort(ABC):
    """Asks the human when the methodology cannot proceed on its own.

    Covers the two SKILL.md moments that require a person: an ambiguous
    selection where the human must pick, and an open question the run cannot
    resolve from evidence and must not fabricate.
    """

    @abstractmethod
    def choose(self, prompt: str, options: tuple[str, ...]) -> str:
        """Ask the human to pick one option; return the chosen one."""

    @abstractmethod
    def ask(self, prompt: str) -> str:
        """Ask the human an open question; return their answer."""
