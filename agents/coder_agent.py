from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from typing import Any

from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from core.orchestrator import Orchestrator


class CoderAgent:
    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        approval_store: Any | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._approval_store = approval_store
        self._pending_approvals: dict[
            tuple[str, str],
            tuple[str, Mapping[str, object], ExecutionContext],
        ] = {}

    async def run(
        self,
        message: str,
        context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        session_id = await self._orchestrator.start_session(context)

        async for event in self.run_session(
            session_id,
            message,
            context,
        ):
            yield event

    async def run_session(
        self,
        session_id: str,
        message: str,
        context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        session_context = await self._resume_context(
            session_id,
            context,
        )

        await self._orchestrator.send(
            session_id,
            message,
            session_context,
        )

        async for event in self._stream_session_events(
            session_id,
            session_context,
        ):
            yield event

    async def attach_session(
        self,
        session_id: str,
        context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        session_context = await self._resume_context(
            session_id,
            context,
        )

        async for event in self._stream_session_events(
            session_id,
            session_context,
        ):
            yield event

    async def _resume_context(
        self,
        session_id: str,
        context: ExecutionContext,
    ) -> ExecutionContext:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        session_context = replace(context, session_id=session_id)

        await self._orchestrator.resume_session(
            session_id,
            session_context,
        )

        return session_context

    async def _stream_session_events(
        self,
        session_id: str,
        session_context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        async for event in self._orchestrator.stream_events(session_id):
            if event.type is not EventType.TOOL_USE:
                yield event
                continue

            yield event

            tool_call_id = event.payload.get("tool_call_id")
            tool_name = event.payload.get("name")
            arguments = event.payload.get("arguments", {})

            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                raise ValueError("tool_use event requires tool_call_id")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("tool_use event requires tool name")
            if not isinstance(arguments, Mapping):
                raise TypeError("tool_use arguments must be a mapping")

            try:
                result = await self._orchestrator.execute_tool(
                    tool_name,
                    arguments,
                    session_context,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }

            if result.get("status") == "approval_required":
                self._pending_approvals[(session_id, tool_call_id)] = (
                    tool_name,
                    dict(arguments),
                    session_context,
                )
                if self._approval_store is not None:
                    approval_rec = {
                        "tool_call_id": tool_call_id,
                        "session_id": session_id,
                        "task_id": session_context.task_id,
                        "project_id": getattr(session_context, "project_id", None),
                        "tool_name": tool_name,
                        "arguments": dict(arguments),
                        "reason": result.get("reason", ""),
                        "risk_explanation": result.get("risk_explanation", ""),
                        "prompt": result.get("prompt", "May MyAgent perform this sensitive action?"),
                        "status": "PENDING",
                    }
                    if hasattr(self._approval_store, "save"):
                        await self._approval_store.save(approval_rec)
                    elif hasattr(self._approval_store, "_save_sync"):
                        self._approval_store._save_sync(approval_rec)

            event_type = (
                EventType.TOOL_CONFIRM
                if result.get("status") == "approval_required"
                else EventType.TOOL_RESULT
            )

            yield AgentEvent(
                type=event_type,
                session_id=session_id,
                agent_id=session_context.agent_id,
                task_id=session_context.task_id,
                payload={
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "result": result,
                },
            )

            if result.get("status") != "approval_required":
                await self._orchestrator.submit_tool_result(
                    session_id,
                    tool_call_id,
                    result,
                    session_context,
                )


    def restore_pending_approvals(
        self,
        session_id: str,
        history: tuple[Mapping[str, object], ...],
        context: ExecutionContext,
    ) -> tuple[str, ...]:
        """Rebuild unresolved approval state.

        Prefers the durable approval store (scoped by session and project,
        filtering out resolved, rejected, and expired approvals).
        Falls back to scanning durable tool history only for backward compatibility
        with legacy pre-hardening sessions.
        """
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        session_context = replace(context, session_id=session_id)
        pending: dict[
            str,
            tuple[str, Mapping[str, object], ExecutionContext],
        ] = {}

        # 1. Query all durable records for the session (active records and tombstones)
        known_durable_tool_ids: set[str] = set()
        if self._approval_store is not None:
            records = []
            if hasattr(self._approval_store, "_list_by_session_sync"):
                records = self._approval_store._list_by_session_sync(
                    session_id,
                    active_only=False,
                )
            elif hasattr(self._approval_store, "list_by_session_sync"):
                records = self._approval_store.list_by_session_sync(
                    session_id,
                    active_only=False,
                )

            context_project_id = getattr(context, "project_id", None)
            for rec in records:
                tool_call_id = rec.get("tool_call_id")
                if not tool_call_id:
                    continue
                known_durable_tool_ids.add(tool_call_id)

                status = rec.get("status")
                is_exp = (
                    self._approval_store.is_expired(rec)
                    if hasattr(self._approval_store, "is_expired")
                    else False
                )
                rec_project_id = rec.get("project_id")
                scope_mismatch = bool(
                    context_project_id
                    and rec_project_id
                    and rec_project_id != context_project_id
                )

                if status == "PENDING" and not is_exp and not scope_mismatch:
                    name = rec.get("tool_name", "")
                    arguments = rec.get("arguments", {})
                    pending[tool_call_id] = (
                        name,
                        dict(arguments) if isinstance(arguments, Mapping) else {},
                        session_context,
                    )
                else:
                    # Durable record exists but is expired, approved, rejected, or scope mismatch.
                    # It acts as an authoritative tombstone and MUST NOT be restored.
                    pass

        # 2. History fallback: reconstruct ONLY legacy tool_call_ids for which NO durable row exists
        legacy_pending: dict[
            str,
            tuple[str, Mapping[str, object], ExecutionContext],
        ] = {}
        for item in history:
            metadata = item.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue

            event_type = metadata.get("event_type")
            tool_call_id = metadata.get("tool_call_id")

            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                continue

            # If a durable record exists for this tool_call_id, durable store is authoritative
            if tool_call_id in known_durable_tool_ids:
                continue

            if event_type == EventType.TOOL_RESULT.value:
                legacy_pending.pop(tool_call_id, None)
                continue

            if event_type != EventType.TOOL_USE.value:
                continue

            # Provenance validation: only model/assistant messages can initiate tool calls
            msg_role = metadata.get("role") or item.get("role")
            if msg_role not in {"assistant", "model", None}:
                continue

            # Scope validation: cannot adopt tool calls with conflicting session or project metadata
            if metadata.get("session_id") and metadata.get("session_id") != session_id:
                continue
            context_project_id = getattr(context, "project_id", None)
            if (
                context_project_id
                and metadata.get("project_id")
                and metadata.get("project_id") != context_project_id
            ):
                continue

            name = metadata.get("name")
            arguments: object = {}

            content = item.get("content")
            if isinstance(content, str):
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    payload = None

                if isinstance(payload, Mapping):
                    if not isinstance(name, str) or not name.strip():
                        name = payload.get("name")
                    arguments = payload.get("arguments", {})

            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(arguments, Mapping):
                continue

            legacy_pending[tool_call_id] = (
                name,
                dict(arguments),
                session_context,
            )

        for tool_call_id, val in legacy_pending.items():
            if tool_call_id not in known_durable_tool_ids:
                pending[tool_call_id] = val
                # Bring legacy approval immediately into durable store governance if store is active
                if self._approval_store is not None and hasattr(self._approval_store, "_save_sync"):
                    try:
                        self._approval_store._save_sync({
                            "tool_call_id": tool_call_id,
                            "session_id": session_id,
                            "project_id": getattr(context, "project_id", None),
                            "task_id": getattr(context, "task_id", None),
                            "tool_name": val[0],
                            "arguments": val[1],
                            "status": "PENDING",
                        })
                    except Exception:
                        pass

        for key in tuple(self._pending_approvals):
            if key[0] == session_id:
                self._pending_approvals.pop(key)

        for tool_call_id, value in pending.items():
            self._pending_approvals[(session_id, tool_call_id)] = value

        return tuple(pending)

    async def resolve_tool_approval(
        self,
        session_id: str,
        tool_call_id: str,
        *,
        approved: bool,
        submit_result: bool = True,
    ) -> AgentEvent:
        key = (session_id, tool_call_id)
        try:
            tool_name, arguments, context = self._pending_approvals.pop(key)
        except KeyError as exc:
            raise KeyError(
                f"no pending approval for tool call: {tool_call_id}"
            ) from exc

        # Pre-execution durable state validation, atomic claim & TTL enforcement
        if self._approval_store is not None:
            new_status = "APPROVED" if approved else "REJECTED"
            note = f"Resolved with approved={approved}"
            context_project_id = getattr(context, "project_id", None)
            if hasattr(self._approval_store, "claim_and_resolve"):
                await self._approval_store.claim_and_resolve(
                    tool_call_id,
                    status=new_status,
                    session_id=session_id,
                    project_id=context_project_id,
                    note=note,
                )
            elif hasattr(self._approval_store, "resolve"):
                await self._approval_store.resolve(
                    tool_call_id,
                    status=new_status,
                    session_id=session_id,
                    project_id=context_project_id,
                    note=note,
                )
            elif hasattr(self._approval_store, "_resolve_sync"):
                self._approval_store._resolve_sync(
                    tool_call_id,
                    status=new_status,
                    session_id=session_id,
                    project_id=context_project_id,
                    note=note,
                )

        if approved:
            try:
                result = await self._orchestrator.execute_tool(
                    tool_name,
                    arguments,
                    context,
                    approved=True,
                )
                exec_success = True if (isinstance(result, dict) and result.get("status") != "error") else False
            except Exception as exc:  # noqa: BLE001
                exec_success = False
                result = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }

            if self._approval_store is not None and hasattr(self._approval_store, "record_execution"):
                await self._approval_store.record_execution(
                    tool_call_id,
                    execution_success=exec_success,
                    result=result if isinstance(result, dict) else {},
                )
        else:
            result = {
                "status": "rejected",
                "tool": tool_name,
            }

        if submit_result:
            await self._orchestrator.submit_tool_result(
                session_id,
                tool_call_id,
                result,
                context,
            )

        return AgentEvent(
            type=EventType.TOOL_RESULT,
            session_id=session_id,
            agent_id=context.agent_id,
            task_id=context.task_id,
            payload={
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "result": result,
            },
        )
