from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from core.context import ExecutionContext
from core.sandbox_runtime import SandboxRuntime
from core.status import CapabilityStatus


class FakeSandboxBackend:
    def __init__(self) -> None:
        self.created: list[tuple[ExecutionContext, Mapping[str, object]]] = []
        self.executed: list[tuple[str, tuple[str, ...], str | None, float | None]] = []
        self.terminated: list[str] = []

    async def create(
        self,
        context: ExecutionContext,
        spec: Mapping[str, object],
    ) -> str:
        self.created.append((context, dict(spec)))
        return f"sandbox-{len(self.created)}"

    async def exec(
        self,
        sandbox_id: str,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, object]:
        self.executed.append(
            (
                sandbox_id,
                tuple(argv),
                cwd,
                timeout,
            )
        )
        return {
            "status": "ok",
            "exit_code": 0,
            "stdout": "done",
            "stderr": "",
        }

    async def status(self, sandbox_id: str) -> Mapping[str, object]:
        return {
            "status": "running",
            "sandbox_id": sandbox_id,
        }

    async def terminate(self, sandbox_id: str) -> None:
        self.terminated.append(sandbox_id)

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()


@pytest.mark.asyncio
async def test_sandbox_runtime_lazily_creates_and_reuses_session_sandbox() -> None:
    backend = FakeSandboxBackend()
    runtime = SandboxRuntime(backend)
    context = ExecutionContext(
        workspace_id="workspace-1",
        project_id="project-1",
        session_id="session-1",
    )

    first = await runtime.exec(
        context,
        ["/usr/bin/printf", "one"],
        timeout=2,
    )
    second = await runtime.exec(
        context,
        ["/usr/bin/printf", "two"],
        timeout=2,
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"

    assert len(backend.created) == 1
    assert backend.executed == [
        (
            "sandbox-1",
            ("/usr/bin/printf", "one"),
            None,
            2,
        ),
        (
            "sandbox-1",
            ("/usr/bin/printf", "two"),
            None,
            2,
        ),
    ]


@pytest.mark.asyncio
async def test_sandbox_runtime_terminates_session_sandbox() -> None:
    backend = FakeSandboxBackend()
    runtime = SandboxRuntime(backend)
    context = ExecutionContext(
        workspace_id="workspace-1",
        session_id="session-1",
    )

    await runtime.exec(
        context,
        ["/usr/bin/printf", "hello"],
    )

    await runtime.terminate_session("session-1")

    assert backend.terminated == ["sandbox-1"]
