from __future__ import annotations

from collections.abc import Mapping, Sequence

from .context import ExecutionContext
from .protocols import SandboxBackend


class SandboxRuntime:
    """Manage lazy sandbox acquisition and reuse per agent session."""

    def __init__(
        self,
        backend: SandboxBackend,
        *,
        default_spec: Mapping[str, object] | None = None,
    ) -> None:
        self._backend = backend
        self._default_spec = dict(default_spec or {})
        self._session_sandboxes: dict[str, str] = {}

    async def _sandbox_for(
        self,
        context: ExecutionContext,
    ) -> str:
        session_id = context.require_session()

        existing = self._session_sandboxes.get(session_id)
        if existing is not None:
            return existing

        sandbox_id = await self._backend.create(
            context,
            self._default_spec,
        )
        self._session_sandboxes[session_id] = sandbox_id
        return sandbox_id

    async def exec(
        self,
        context: ExecutionContext,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, object]:
        sandbox_id = await self._sandbox_for(context)

        return await self._backend.exec(
            sandbox_id,
            argv,
            cwd=cwd,
            timeout=timeout,
        )

    async def status(
        self,
        session_id: str,
    ) -> Mapping[str, object] | None:
        sandbox_id = self._session_sandboxes.get(session_id)
        if sandbox_id is None:
            return None

        return await self._backend.status(sandbox_id)

    async def terminate_session(
        self,
        session_id: str,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        sandbox_id = self._session_sandboxes.pop(
            session_id,
            None,
        )
        if sandbox_id is None:
            return

        await self._backend.terminate(sandbox_id)
