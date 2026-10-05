from __future__ import annotations

from .project_store import ProjectStore
from .task import TaskRecord, TaskState
from .task_store import TaskStore


class TaskService:
    def __init__(
        self,
        *,
        project_store: ProjectStore,
        task_store: TaskStore,
    ) -> None:
        self._project_store = project_store
        self._task_store = task_store

    async def create_task(
        self,
        *,
        project_id: str,
        task_id: str,
        objective: str,
    ) -> TaskRecord:
        project = await self._project_store.get(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")

        existing = await self._task_store.get(task_id)
        if existing is not None:
            raise ValueError(f"task already exists: {task_id}")

        task = TaskRecord(
            task_id=task_id,
            project_id=project_id,
            objective=objective,
        )
        await self._task_store.save(task)
        return task

    async def get_task(
        self,
        task_id: str,
    ) -> TaskRecord | None:
        return await self._task_store.get(task_id)

    async def list_tasks(
        self,
        project_id: str,
    ):
        project = await self._project_store.get(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")

        return await self._task_store.list_for_project(project_id)

    async def get_active_task(
        self,
        project_id: str,
    ) -> TaskRecord | None:
        project = await self._project_store.get(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")

        if project.active_task_id is None:
            return None

        task = await self._task_store.get(project.active_task_id)
        if task is None:
            raise KeyError(
                f"unknown active task: {project.active_task_id}"
            )

        return task

    async def start_task(
        self,
        task_id: str,
    ) -> TaskRecord:
        task = await self._require_task(task_id)

        if task.terminal:
            raise ValueError(
                f"cannot start terminal task: {task_id}"
            )

        if task.state is TaskState.CREATED:
            task.transition(TaskState.PLANNING)
            await self._task_store.save(task)

        project = await self._require_project(task.project_id)
        project.active_task_id = task.task_id
        await self._project_store.update(project)

        return task

    async def cancel_task(
        self,
        task_id: str,
    ) -> TaskRecord:
        task = await self._require_task(task_id)

        task.transition(TaskState.CANCELLED)
        await self._task_store.save(task)

        await self._clear_active_task_if_matching(task)
        return task

    async def complete_task(
        self,
        task_id: str,
    ) -> TaskRecord:
        task = await self._require_task(task_id)

        task.transition(TaskState.COMPLETED)
        await self._task_store.save(task)

        await self._clear_active_task_if_matching(task)
        return task

    async def _require_task(
        self,
        task_id: str,
    ) -> TaskRecord:
        task = await self._task_store.get(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")
        return task

    async def _require_project(
        self,
        project_id: str,
    ):
        project = await self._project_store.get(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")
        return project

    async def _clear_active_task_if_matching(
        self,
        task: TaskRecord,
    ) -> None:
        project = await self._require_project(task.project_id)

        if project.active_task_id != task.task_id:
            return

        project.active_task_id = None
        await self._project_store.update(project)
