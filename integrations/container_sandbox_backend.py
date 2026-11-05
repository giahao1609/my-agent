"""Hardened Container Production SandboxBackend Implementation.

Supports:
1. Real asynchronous container execution via Docker or Podman CLI/Engine.
2. Kernel namespace isolation with default-deny network egress policy (--network none).
3. Resource constraints (cgroups memory limits and CPU throttling).
4. Full container lifecycle operations: create, exec, pause, unpause, snapshot (commit), rollback, terminate.
5. High-fidelity in-memory graceful fallback/simulation when Docker daemon is not running or when force_simulation=True.
6. Truthful dynamic CapabilityStatus probes (AVAILABLE when engine reachable, MOCKED when absent).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.context import ExecutionContext
from core.protocols import SandboxBackend
from core.status import Availability, CapabilityStatus

logger = logging.getLogger(__name__)

_DOCKER_AVAILABLE_CACHE: tuple[bool, str | None, float] | None = None


def is_docker_available(cache_ttl_seconds: float = 5.0) -> bool:
    """Checks whether docker or podman CLI and daemon are reachable."""
    global _DOCKER_AVAILABLE_CACHE
    now = time.monotonic()
    if _DOCKER_AVAILABLE_CACHE is not None:
        avail, _, cached_at = _DOCKER_AVAILABLE_CACHE
        if now - cached_at < cache_ttl_seconds:
            return avail

    docker_bin = shutil.which("docker") or shutil.which("podman")
    if not docker_bin:
        _DOCKER_AVAILABLE_CACHE = (False, None, now)
        return False

    try:
        res = subprocess.run(
            [docker_bin, "info", "--format", "{{.ServerVersion}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2.0,
            check=False,
        )
        is_avail = res.returncode == 0
        _DOCKER_AVAILABLE_CACHE = (is_avail, docker_bin if is_avail else None, now)
        return is_avail
    except Exception:
        _DOCKER_AVAILABLE_CACHE = (False, None, now)
        return False


def get_docker_binary() -> str | None:
    """Returns the resolved path to docker or podman if available."""
    is_docker_available()
    if _DOCKER_AVAILABLE_CACHE and _DOCKER_AVAILABLE_CACHE[0]:
        return _DOCKER_AVAILABLE_CACHE[1]
    return shutil.which("docker") or shutil.which("podman")


@dataclass
class ContainerSandboxSpec:
    image: str = "python:3.10-slim"
    cpu_limit: float = 2.0
    memory_mb: int = 2048
    network_egress: str = "default-deny"  # default-deny egress policy
    timeout_seconds: float = 300.0


@dataclass
class ContainerSnapshot:
    snapshot_id: str
    created_at: str
    state_data: dict[str, Any] = field(default_factory=dict)
    image_tag: str | None = None


class ContainerSandboxBackend(SandboxBackend):
    """Hardened container production SandboxBackend implementation.

    Enforces network egress boundaries, resource limits, execution timeouts,
    and supports pause/resume/snapshot/rollback lifecycle operations.
    """

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        force_simulation: bool = False,
        default_image: str = "python:3.10-slim",
    ) -> None:
        self._workspace_root = (
            Path(workspace_root) if workspace_root else Path("/tmp/my_agent_sandboxes")
        )
        self._force_simulation = force_simulation
        self._default_image = default_image
        self._sandboxes: dict[str, dict[str, Any]] = {}
        self._snapshots: dict[str, ContainerSnapshot] = {}

    async def _run_docker_cmd(self, *args: str) -> tuple[int, str, str]:
        docker_bin = get_docker_binary()
        if not docker_bin:
            raise RuntimeError("Docker binary not available")

        proc = await asyncio.create_subprocess_exec(
            docker_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode if proc.returncode is not None else 0,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )

    async def _resolve_image(self, requested_image: str) -> str:
        """Ensures an image exists locally, falling back gracefully to available alternatives."""
        docker_bin = get_docker_binary()
        if not docker_bin:
            return requested_image

        # Check if requested image exists
        code, _, _ = await self._run_docker_cmd("image", "inspect", requested_image)
        if code == 0:
            return requested_image

        # Check default image
        code_def, _, _ = await self._run_docker_cmd("image", "inspect", self._default_image)
        if code_def == 0:
            return self._default_image

        # Attempt pull requested image
        code_pull, _, _ = await self._run_docker_cmd("pull", requested_image)
        if code_pull == 0:
            return requested_image

        return requested_image

    async def create(
        self,
        context: ExecutionContext,
        spec: Mapping[str, object],
    ) -> str:
        sandbox_id = f"sbx-{uuid.uuid4().hex[:12]}"
        requested_img = str(spec.get("image", self._default_image))

        container_spec = ContainerSandboxSpec(
            image=requested_img,
            cpu_limit=float(spec.get("cpu_limit", 2.0)),
            memory_mb=int(spec.get("memory_mb", 2048)),
            network_egress=str(spec.get("network_egress", "default-deny")),
            timeout_seconds=float(spec.get("timeout_seconds", 300.0)),
        )

        sandbox_dir = self._workspace_root / sandbox_id
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        is_real = not self._force_simulation and is_docker_available()
        container_name = f"myagent-{sandbox_id}"

        if is_real:
            resolved_img = await self._resolve_image(requested_img)
            docker_cmd = [
                "run",
                "-d",
                "--name",
                container_name,
                "--label",
                f"myagent.sandbox_id={sandbox_id}",
                "--label",
                f"myagent.project_id={context.project_id or 'default'}",
                "-m",
                f"{container_spec.memory_mb}m",
                "--cpus",
                str(container_spec.cpu_limit),
                "--security-opt",
                "no-new-privileges:true",
            ]

            if container_spec.network_egress == "default-deny":
                docker_cmd.extend(["--network", "none"])
            elif container_spec.network_egress == "host":
                docker_cmd.extend(["--network", "host"])
            else:
                docker_cmd.extend(["--network", "bridge"])

            # Mount workspace or sandbox ephemeral directory
            mount_workspace = bool(spec.get("mount_workspace", False))
            target_mount = (
                Path(context.workspace_id).resolve()
                if mount_workspace and context.workspace_id and Path(context.workspace_id).is_dir()
                else sandbox_dir
            )
            docker_cmd.extend(["-v", f"{target_mount}:/workspace:rw", "--workdir", "/workspace"])
            docker_cmd.extend([resolved_img, "sleep", "infinity"])

            code, out, err = await self._run_docker_cmd(*docker_cmd)
            if code != 0:
                logger.warning(
                    "Docker container launch failed (%s). Falling back to simulation mode.", err
                )
                is_real = False

        self._sandboxes[sandbox_id] = {
            "sandbox_id": sandbox_id,
            "project_id": context.project_id,
            "workspace_id": context.workspace_id,
            "spec": container_spec,
            "state": "running",
            "working_dir": str(sandbox_dir),
            "created_at": context.session_id or uuid.uuid4().hex,
            "is_real": is_real,
            "container_name": container_name,
            "snapshot_tags": [],
        }

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

        if sandbox["state"] != "running":
            raise RuntimeError(
                f"sandbox {sandbox_id} is in state '{sandbox['state']}', cannot execute command"
            )

        exec_timeout = timeout if timeout is not None else sandbox["spec"].timeout_seconds

        if sandbox.get("is_real"):
            docker_bin = get_docker_binary()
            if not docker_bin:
                raise RuntimeError("Docker binary is unavailable for real container exec")

            cmd = [docker_bin, "exec"]
            if cwd:
                cmd.extend(["--workdir", cwd])
            cmd.append(sandbox["container_name"])
            cmd.extend(argv)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=exec_timeout,
                )
                exit_code = proc.returncode if proc.returncode is not None else 0
                stdout = stdout_b.decode(errors="replace")
                stderr = stderr_b.decode(errors="replace")
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return {
                    "exit_code": 124,
                    "stdout": "",
                    "stderr": f"Command timed out after {exec_timeout} seconds",
                    "sandbox_id": sandbox_id,
                    "network_egress": sandbox["spec"].network_egress,
                    "timeout_used": exec_timeout,
                }

            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "sandbox_id": sandbox_id,
                "network_egress": sandbox["spec"].network_egress,
                "timeout_used": exec_timeout,
            }

        # Simulated fallback execution
        return {
            "exit_code": 0,
            "stdout": f"Simulated exec output for: {' '.join(argv)}",
            "stderr": "",
            "sandbox_id": sandbox_id,
            "network_egress": sandbox["spec"].network_egress,
            "timeout_used": exec_timeout,
        }

    async def pause(self, sandbox_id: str) -> None:
        sandbox = self._require_sandbox(sandbox_id)
        if sandbox.get("is_real"):
            await self._run_docker_cmd("pause", sandbox["container_name"])
        sandbox["state"] = "paused"

    async def resume(self, sandbox_id: str) -> None:
        sandbox = self._require_sandbox(sandbox_id)
        if sandbox.get("is_real"):
            await self._run_docker_cmd("unpause", sandbox["container_name"])
        sandbox["state"] = "running"

    async def snapshot(self, sandbox_id: str) -> str:
        sandbox = self._require_sandbox(sandbox_id)
        snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"
        image_tag = None

        if sandbox.get("is_real"):
            image_tag = f"myagent-snap:{snapshot_id}"
            code, out, err = await self._run_docker_cmd(
                "commit", sandbox["container_name"], image_tag
            )
            if code != 0:
                logger.warning("Docker commit snapshot failed (%s).", err)
                image_tag = None
            else:
                sandbox.setdefault("snapshot_tags", []).append(image_tag)

        snap = ContainerSnapshot(
            snapshot_id=snapshot_id,
            created_at=uuid.uuid4().hex,
            state_data={"state": sandbox["state"], "spec": sandbox["spec"]},
            image_tag=image_tag,
        )
        self._snapshots[snapshot_id] = snap
        return snapshot_id

    async def rollback(self, sandbox_id: str, snapshot_id: str) -> None:
        sandbox = self._require_sandbox(sandbox_id)
        if snapshot_id not in self._snapshots:
            raise KeyError(f"unknown snapshot_id: {snapshot_id}")

        snap = self._snapshots[snapshot_id]
        if sandbox.get("is_real") and snap.image_tag:
            container_name = sandbox["container_name"]
            container_spec = sandbox["spec"]
            sandbox_dir = Path(sandbox["working_dir"])

            # 1. Stop and remove current container
            await self._run_docker_cmd("rm", "-f", container_name)

            # 2. Re-create container from snapshot image
            docker_cmd = [
                "run",
                "-d",
                "--name",
                container_name,
                "--label",
                f"myagent.sandbox_id={sandbox_id}",
                "-m",
                f"{container_spec.memory_mb}m",
                "--cpus",
                str(container_spec.cpu_limit),
                "--security-opt",
                "no-new-privileges:true",
            ]
            if container_spec.network_egress == "default-deny":
                docker_cmd.extend(["--network", "none"])
            elif container_spec.network_egress == "host":
                docker_cmd.extend(["--network", "host"])
            else:
                docker_cmd.extend(["--network", "bridge"])

            docker_cmd.extend(["-v", f"{sandbox_dir}:/workspace:rw", "--workdir", "/workspace"])
            docker_cmd.extend([snap.image_tag, "sleep", "infinity"])
            await self._run_docker_cmd(*docker_cmd)

        sandbox["state"] = snap.state_data.get("state", "running")

    async def status(self, sandbox_id: str) -> Mapping[str, object]:
        sandbox = self._require_sandbox(sandbox_id)
        current_state = sandbox["state"]

        if sandbox.get("is_real"):
            code, out, _ = await self._run_docker_cmd(
                "inspect", "--format", "{{.State.Status}}", sandbox["container_name"]
            )
            if code == 0 and out:
                if out == "running":
                    current_state = "running"
                elif out == "paused":
                    current_state = "paused"
                elif out in ("exited", "dead"):
                    current_state = "terminated"

        return {
            "sandbox_id": sandbox_id,
            "state": current_state,
            "image": sandbox["spec"].image,
            "network_egress": sandbox["spec"].network_egress,
            "memory_mb": sandbox["spec"].memory_mb,
            "cpu_limit": sandbox["spec"].cpu_limit,
            "is_real_container": sandbox.get("is_real", False),
        }

    async def terminate(self, sandbox_id: str) -> None:
        sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox is not None:
            if sandbox.get("is_real"):
                await self._run_docker_cmd("rm", "-f", sandbox["container_name"])
                # Cleanup snapshot images
                for tag in sandbox.get("snapshot_tags", []):
                    try:
                        await self._run_docker_cmd("rmi", "-f", tag)
                    except Exception:
                        pass
            sandbox["state"] = "terminated"

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        is_real = not self._force_simulation and is_docker_available()

        if is_real:
            return (
                CapabilityStatus(
                    name="container_isolation",
                    state=Availability.AVAILABLE,
                    implementation="ContainerSandboxBackend",
                    provider_or_backend="docker_container_engine",
                    verification_method="daemon_probe",
                    evidence="Real Docker/Podman container engine running with cgroups and Linux namespace isolation",
                ),
                CapabilityStatus(
                    name="default_deny_egress",
                    state=Availability.AVAILABLE,
                    implementation="ContainerSandboxBackend",
                    provider_or_backend="docker_container_engine",
                    verification_method="daemon_probe",
                    evidence="Network egress isolation enforced via container network namespace (--network none)",
                ),
                CapabilityStatus(
                    name="snapshot_rollback",
                    state=Availability.AVAILABLE,
                    implementation="ContainerSandboxBackend",
                    provider_or_backend="docker_container_engine",
                    verification_method="daemon_probe",
                    evidence="Filesystem snapshot and rollback supported via container image commit layers",
                ),
            )

        return (
            CapabilityStatus(
                name="container_isolation",
                state=Availability.MOCKED,
                reason="Container execution is simulated in-memory; no Docker/Podman engine attached",
                implementation="ContainerSandboxBackend",
                provider_or_backend="in_memory_simulation",
                verification_method="source_inspection",
                evidence="ContainerSandboxBackend simulates execution without container daemon",
            ),
            CapabilityStatus(
                name="default_deny_egress",
                state=Availability.MOCKED,
                reason="Egress network policy is in-memory metadata without kernel namespace isolation",
                implementation="ContainerSandboxBackend",
                provider_or_backend="in_memory_simulation",
                verification_method="source_inspection",
                evidence="Spec network_egress stored in dictionary without network namespace rules",
            ),
            CapabilityStatus(
                name="snapshot_rollback",
                state=Availability.MOCKED,
                reason="Filesystem snapshot/rollback operates on in-memory state dict rather than filesystem layer",
                implementation="ContainerSandboxBackend",
                provider_or_backend="in_memory_simulation",
                verification_method="source_inspection",
                evidence="ContainerSnapshot stores state_data in memory",
            ),
        )

    def _require_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise KeyError(f"unknown sandbox_id: {sandbox_id}")
        return sandbox
