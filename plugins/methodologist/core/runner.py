"""Host-neutral glue binding a `ReasoningRun` to a `TaskTracker`.

This is the smallest useful thing the ports make possible: it turns a run's
phases into tracked tasks and starts the first one — SKILL.md Step 2 — without
ever naming the host tool that does the tracking.
"""

from __future__ import annotations

from .models import ReasoningRun
from .ports import TaskTracker


def register_phase_tasks(run: ReasoningRun, tracker: TaskTracker) -> tuple[str, ...]:
    """Create one task per phase, start the first, and return the task ids."""

    ids = tuple(
        tracker.create_task(
            f"{run.methodology.name}: Phase {phase.number} — {phase.title}"
        )
        for phase in run.methodology.phases
    )
    if ids:
        tracker.start_task(ids[0])
    return ids
