from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    workspace_id: str
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id must not be empty")

    def require_user(self) -> str:
        if self.user_id is None:
            raise ValueError("user_id is required for this operation")
        return self.user_id

    def require_agent(self) -> str:
        if self.agent_id is None:
            raise ValueError("agent_id is required for this operation")
        return self.agent_id

    def require_session(self) -> str:
        if self.session_id is None:
            raise ValueError("session_id is required for this operation")
        return self.session_id
