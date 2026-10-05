from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .task import TaskRecord


@dataclass(frozen=True, slots=True)
class PlannerContext:
    workspace_id: str | None = None
    project_context: str = ""
    code_context: str = ""

    def __post_init__(self) -> None:
        if (
            self.workspace_id is not None
            and not self.workspace_id.strip()
        ):
            raise ValueError(
                "workspace_id must not be empty"
            )


@dataclass(frozen=True, slots=True)
class PlanStepProposal:
    title: str
    instruction: str

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("plan step title must not be empty")

        if not self.instruction.strip():
            raise ValueError(
                "plan step instruction must not be empty"
            )


@dataclass(frozen=True, slots=True)
class PlanProposal:
    steps: Sequence[PlanStepProposal]

    def __post_init__(self) -> None:
        normalized = tuple(self.steps)

        if not normalized:
            raise ValueError(
                "plan proposal requires at least one step"
            )

        object.__setattr__(self, "steps", normalized)


class Planner(Protocol):
    async def propose(
        self,
        task: TaskRecord,
        *,
        context: PlannerContext,
    ) -> PlanProposal:
        ...
