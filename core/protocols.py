from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Protocol

from .context import ExecutionContext
from .events import AgentEvent
from .model import ExecutionTarget, ModelMessage, ModelTurn
from .session import SessionRecord
from .status import CapabilityStatus


class AgentRuntime(Protocol):
    async def start(self, context: ExecutionContext) -> str: ...

    async def resume(self, session_id: str, context: ExecutionContext) -> None: ...

    async def send(
        self,
        session_id: str,
        message: str,
        context: ExecutionContext,
        *,
        retrieval_context: Mapping[str, object] | None = None,
    ) -> None: ...

    async def set_execution_target(
        self,
        session_id: str,
        *,
        runtime_id: str | None,
        model_id: str | None,
        history: tuple[Mapping[str, object], ...] | None = None,
    ) -> None: ...

    async def cancel(self, session_id: str) -> None: ...

    async def submit_tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        result: Mapping[str, object],
        context: ExecutionContext,
    ) -> None: ...

    def stream_events(self, session_id: str) -> AsyncIterator[AgentEvent]: ...

    async def capabilities(self) -> Sequence[CapabilityStatus]: ...


class ModelBackend(Protocol):
    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn: ...


class ModelBackendProvider(Protocol):

    async def get(self) -> ModelBackend: ...


class MemoryBackend(Protocol):
    async def recall(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[Mapping[str, object]]: ...

    async def capture(
        self,
        context: ExecutionContext,
        records: Sequence[Mapping[str, object]],
    ) -> None: ...

    async def capabilities(self) -> Sequence[CapabilityStatus]: ...


class KnowledgeBackend(Protocol):
    async def search(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[Mapping[str, object]]: ...

    async def get_node(
        self,
        context: ExecutionContext,
        node_id: str,
    ) -> Mapping[str, object] | None: ...

    async def capabilities(self) -> Sequence[CapabilityStatus]: ...


class SandboxBackend(Protocol):
    async def create(
        self,
        context: ExecutionContext,
        spec: Mapping[str, object],
    ) -> str: ...

    async def exec(
        self,
        sandbox_id: str,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, object]: ...

    async def status(self, sandbox_id: str) -> Mapping[str, object]: ...

    async def terminate(self, sandbox_id: str) -> None: ...

    async def capabilities(self) -> Sequence[CapabilityStatus]: ...

class SessionStore(Protocol):
    async def save(self, session: SessionRecord) -> None: ...

    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def list_for_project(
        self,
        project_id: str,
    ) -> Sequence[SessionRecord]: ...

