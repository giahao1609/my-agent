from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .task import TaskRecord


class TaskStore(Protocol):
    async def save(self, task: TaskRecord) -> None: ...

    async def get(self, task_id: str) -> TaskRecord | None: ...

    async def list_for_project(
        self,
        project_id: str,
    ) -> Sequence[TaskRecord]: ...
