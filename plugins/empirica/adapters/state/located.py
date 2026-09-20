"""Located v2 run facade over the hardened filesystem repository."""
from __future__ import annotations

from core.records import RunKey
from .repository import FilesystemRunRepository, GenerationAllocator


class LocatedRunRepository:
    """Expose storage operations plus generation discovery without a selector index."""

    def __init__(self, repository: FilesystemRunRepository | None = None):
        self.repository = repository or FilesystemRunRepository()
        self.allocator = GenerationAllocator(self.repository)

    def generations(self, project_id: str, run_id: str) -> list[int]:
        return self.repository.generations(project_id, run_id)

    def read(self, key: RunKey):
        return self.repository.read(key)

    def create(self, key: RunKey, value):
        return self.repository.create(key, value)

    def compare_and_set(self, key: RunKey, value, expected):
        return self.repository.compare_and_set(key, value, expected)
