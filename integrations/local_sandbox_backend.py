from __future__ import annotations

import asyncio

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from core.context import ExecutionContext
from core.errors import CapabilityUnavailableError, SandboxError
from core.status import Availability, CapabilityStatus


@dataclass(slots=True)
class _LocalSandbox:
    workspace_path: Path
    state: str = "running"


class LocalSandboxBackend:
    """Explicit opt-in local sandbox for development and tests only."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        default_timeout: float = 30.0,
    ) -> None:
        if default_timeout <= 0:
            raise ValueError("default_timeout must be > 0")

        self._enabled = enabled
        self._default_timeout = default_timeout
        self._sandboxes: dict[str, _LocalSandbox] = {}

    async def create(
        self,
        context: ExecutionContext,
        spec: Mapping[str, object],
    ) -> str:
        if not self._enabled:
            raise CapabilityUnavailableError(
                "local sandbox backend is disabled"
            )

        workspace = Path(context.workspace_id).expanduser().resolve()
        if not workspace.is_dir():
            raise SandboxError(
                f"workspace does not exist: {workspace}"
            )

        sandbox_id = f"local-{uuid4().hex}"
        self._sandboxes[sandbox_id] = _LocalSandbox(
            workspace_path=workspace,
        )
        return sandbox_id

    async def exec(
        self,
        sandbox_id: str,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, object]:
        sandbox = self._require_sandbox(sandbox_id)

        if sandbox.state != "running":
            raise SandboxError(
                f"sandbox is not running: {sandbox_id}"
            )

        if (
            not isinstance(argv, Sequence)
            or isinstance(argv, (str, bytes))
            or not argv
        ):
            raise ValueError("argv must be a non-empty sequence")

        command = tuple(argv)
        if not all(
            isinstance(item, str) and item
            for item in command
        ):
            raise TypeError("argv must contain non-empty strings")

        working_directory = sandbox.workspace_path
        if cwd is not None:
            candidate = Path(cwd).expanduser()
            working_directory = (
                candidate.resolve()
                if candidate.is_absolute()
                else (sandbox.workspace_path / candidate).resolve()
            )

            if not working_directory.is_relative_to(
                sandbox.workspace_path
            ):
                raise SandboxError("cwd escapes sandbox workspace")

            if not working_directory.is_dir():
                raise SandboxError(
                    f"cwd does not exist: {working_directory}"
                )

        execution_timeout = (
            self._default_timeout
            if timeout is None
            else float(timeout)
        )

        if execution_timeout <= 0:
            raise ValueError("timeout must be > 0")

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(working_directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return {
                "status": "error",
                "exit_code": None,
                "stdout": "",
                "stderr": str(exc),
            }

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=execution_timeout,
            )
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            return {
                "status": "timeout",
                "exit_code": process.returncode,
                "stdout": stdout.decode(
                    "utf-8",
                    errors="replace",
                ),
                "stderr": stderr.decode(
                    "utf-8",
                    errors="replace",
                ),
            }

        return {
            "status": (
                "ok"
                if process.returncode == 0
                else "error"
            ),
            "exit_code": process.returncode,
            "stdout": stdout.decode(
                "utf-8",
                errors="replace",
            ),
            "stderr": stderr.decode(
                "utf-8",
                errors="replace",
            ),
        }

    async def status(
        self,
        sandbox_id: str,
    ) -> Mapping[str, object]:
        sandbox = self._require_sandbox(sandbox_id)

        return {
            "status": sandbox.state,
            "backend": "local",
            "workspace_path": str(sandbox.workspace_path),
        }

    async def terminate(self, sandbox_id: str) -> None:
        sandbox = self._require_sandbox(sandbox_id)
        sandbox.state = "terminated"

    async def capabilities(
        self,
    ) -> tuple[CapabilityStatus, ...]:
        return (
            CapabilityStatus(
                name="local_sandbox",
                state=(
                    Availability.READY
                    if self._enabled
                    else Availability.UNAVAILABLE
                ),
                reason=(
                    None
                    if self._enabled
                    else "local sandbox backend is disabled (opt-in only)"
                ),
                implementation="LocalSandboxBackend",
                provider_or_backend="local_subprocess",
                verification_method="runtime_probe",
                evidence="Real asyncio.create_subprocess_exec execution path",
            ),
        )

    def _require_sandbox(
        self,
        sandbox_id: str,
    ) -> _LocalSandbox:
        if not sandbox_id.strip():
            raise ValueError("sandbox_id must not be empty")

        try:
            return self._sandboxes[sandbox_id]
        except KeyError as exc:
            raise SandboxError(
                f"unknown sandbox: {sandbox_id}"
            ) from exc
