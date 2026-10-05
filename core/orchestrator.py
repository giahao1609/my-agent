from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from .context import ExecutionContext
from .events import AgentEvent, EventType
from .protocols import (
    AgentRuntime,
    KnowledgeBackend,
    MemoryBackend,
    SessionStore,
)
from .sandbox_runtime import SandboxRuntime
from .session import SessionRecord, SessionState
from .tool_executor import ToolExecutor
from .tools import ToolResult


class Orchestrator:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        memory: MemoryBackend,
        knowledge: KnowledgeBackend,
        tools: ToolExecutor,
        session_store: SessionStore | None = None,
        sandbox_runtime: SandboxRuntime | None = None,
    ) -> None:
        self._runtime = runtime
        self._memory = memory
        self._knowledge = knowledge
        self._tools = tools
        self._session_store = session_store
        self._sandbox_runtime = sandbox_runtime
        self._sessions: dict[str, SessionRecord] = {}

    async def _persist_session(self, session: SessionRecord) -> None:
        if self._session_store is not None:
            await self._session_store.save(session)

    async def _terminate_sandbox(self, session_id: str) -> None:
        if self._sandbox_runtime is not None:
            await self._sandbox_runtime.terminate_session(session_id)

    async def start_session(self, context: ExecutionContext) -> str:
        session_id = await self._runtime.start(context)
        session = SessionRecord(
            session_id=session_id,
            context=context,
        )
        session.transition(SessionState.STARTING)
        session.transition(SessionState.RUNNING)
        self._sessions[session_id] = session
        await self._persist_session(session)
        return session_id

    def get_session(self, session_id: str) -> SessionRecord:
        return self._sessions[session_id]

    async def set_execution_target(
        self,
        session_id: str,
        *,
        runtime_id: str | None,
        model_id: str | None,
        history: tuple[Mapping[str, object], ...] | None = None,
    ) -> None:
        session = self.get_session(session_id)

        if session.terminal:
            raise ValueError(
                f"cannot change execution target for terminal session: {session_id}"
            )

        if runtime_id is not None and not runtime_id.strip():
            raise ValueError("runtime_id must not be empty")
        if model_id is not None and not model_id.strip():
            raise ValueError("model_id must not be empty")

        await self._runtime.set_execution_target(
            session_id,
            runtime_id=runtime_id,
            model_id=model_id,
            history=history,
        )

        session.set_runtime(runtime_id)
        session.set_model(model_id)
        await self._persist_session(session)

    async def resume_session(
        self,
        session_id: str,
        context: ExecutionContext,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        existing = self._sessions.get(session_id)
        if existing is not None:
            if existing.state is SessionState.RUNNING:
                return
            if existing.terminal:
                raise ValueError(
                    f"cannot resume terminal session: {session_id}"
                )
            return

        durable: SessionRecord | None = None
        if self._session_store is not None:
            durable = await self._session_store.get(session_id)

        if durable is not None:
            if durable.terminal:
                raise ValueError(
                    f"cannot resume terminal session: {session_id}"
                )

            session = durable
            self._sessions[session_id] = session

            try:
                await self._runtime.resume(
                    session_id,
                    session.context,
                )
            except Exception:
                if not session.terminal:
                    session.transition(SessionState.FAILED)
                    await self._persist_session(session)
                    await self._terminate_sandbox(session_id)
                raise

            await self._persist_session(session)
            return

        session = SessionRecord(
            session_id=session_id,
            context=context,
        )
        session.transition(SessionState.STARTING)
        self._sessions[session_id] = session
        await self._persist_session(session)

        try:
            await self._runtime.resume(session_id, context)
        except Exception:
            session.transition(SessionState.FAILED)
            await self._persist_session(session)
            await self._terminate_sandbox(session_id)
            raise

        session.transition(SessionState.RUNNING)
        await self._persist_session(session)

    async def send(
        self,
        session_id: str,
        message: str,
        context: ExecutionContext,
    ) -> None:
        retrieval_context: dict[str, object] | None = None

        if context.project_id is not None and context.project_id.strip():
            memory = await self.recall(
                context,
                message,
            )
            knowledge = await self.search_knowledge(
                context,
                message,
            )
            retrieval_context = {
                "memory": [dict(record) for record in memory],
                "knowledge": [dict(record) for record in knowledge],
            }

        if retrieval_context is None:
            await self._runtime.send(
                session_id,
                message,
                context,
            )
            return

        await self._runtime.send(
            session_id,
            message,
            context,
            retrieval_context=retrieval_context,
        )

    async def stream_events(
        self,
        session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        try:
            async for event in self._runtime.stream_events(session_id):
                session = self._sessions.get(session_id)

                if (
                    session is not None
                    and event.type is EventType.STOP
                    and not session.terminal
                ):
                    target_state = (
                        SessionState.FAILED
                        if event.payload.get("reason") == "runtime_error"
                        else SessionState.STOPPED
                    )
                    session.transition(target_state)
                    await self._persist_session(session)
                    await self._terminate_sandbox(session_id)

                yield event
        except Exception:
            session = self._sessions.get(session_id)
            if session is not None and not session.terminal:
                session.transition(SessionState.FAILED)
                await self._persist_session(session)
                await self._terminate_sandbox(session_id)
            raise

    async def recall(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> tuple[Mapping[str, object], ...]:
        records = await self._memory.recall(
            context,
            query,
            limit=limit,
        )
        return tuple(records)

    async def search_knowledge(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> tuple[Mapping[str, object], ...]:
        records = await self._knowledge.search(
            context,
            query,
            limit=limit,
        )
        return tuple(records)

    async def execute_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
        context: ExecutionContext,
        *,
        approved: bool = False,
    ) -> ToolResult:
        result = await self._tools.execute(
            name,
            arguments,
            context,
            approved=approved,
        )

        if (
            result.get("status") == "approval_required"
            and context.session_id is not None
        ):
            session = self._sessions.get(context.session_id)
            if (
                session is not None
                and session.state is SessionState.RUNNING
            ):
                session.transition(SessionState.WAITING_APPROVAL)
                await self._persist_session(session)

        return result

    async def submit_tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        result: Mapping[str, object],
        context: ExecutionContext,
    ) -> None:
        await self._runtime.submit_tool_result(
            session_id,
            tool_call_id,
            result,
            context,
        )

        session = self._sessions.get(session_id)
        if (
            session is not None
            and session.state is SessionState.WAITING_APPROVAL
            and result.get("status") != "approval_required"
        ):
            session.transition(SessionState.RUNNING)
            await self._persist_session(session)

    async def cancel(self, session_id: str) -> None:
        session = self._sessions.get(session_id)

        if session is None and self._session_store is not None:
            session = await self._session_store.get(session_id)
            if session is not None:
                self._sessions[session_id] = session

        if session is None:
            await self._runtime.cancel(session_id)
            return

        if session.terminal:
            return

        if session.state in {
            SessionState.RUNNING,
            SessionState.WAITING_APPROVAL,
            SessionState.CANCELLING,
        }:
            session.transition(SessionState.CANCELLING)
            await self._persist_session(session)

            try:
                await self._runtime.cancel(session_id)
            except Exception:
                session.transition(SessionState.FAILED)
                await self._persist_session(session)
                await self._terminate_sandbox(session_id)
                raise

            session.transition(SessionState.STOPPED)
            await self._persist_session(session)
            await self._terminate_sandbox(session_id)
            return

        # CREATED and STARTING support a direct transition to STOPPED.
        try:
            await self._runtime.cancel(session_id)
        except Exception:
            session.transition(SessionState.FAILED)
            await self._persist_session(session)
            await self._terminate_sandbox(session_id)
            raise

        session.transition(SessionState.STOPPED)
        await self._persist_session(session)
        await self._terminate_sandbox(session_id)
