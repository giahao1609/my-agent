from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Sequence
from unittest.mock import MagicMock

import pytest

from core.capabilities import (
    Availability,
    CapabilityMockedError,
    CapabilityProvider,
    CapabilityRecord,
    CapabilityRegistry,
    CapabilityStatus,
    CapabilityUnavailableError,
    default_capability_registry,
)
from core.context import ExecutionContext
from core.agent_role import AgentRole
from core.handoff_contracts import ImplementationResult
from core.whole_plan_coordinator import StepExecutor, WholePlanCoordinator, _NoOpStepExecutor
from integrations.container_sandbox_backend import ContainerSandboxBackend
from integrations.local_sandbox_backend import LocalSandboxBackend
from integrations.playwright_browser import PlaywrightBrowserAdapter


# ===========================================================================
# TEST 1 — Browser mock truth
# ===========================================================================

@pytest.mark.asyncio
async def test_browser_mock_truth() -> None:
    """Mock browser implementation cannot report AVAILABLE."""
    adapter = PlaywrightBrowserAdapter()
    caps = await adapter.capabilities()

    assert len(caps) >= 3
    for cap in caps:
        assert isinstance(cap, CapabilityStatus)
        assert cap.state is Availability.MOCKED
        assert cap.available is False
        assert cap.is_mocked is True
        # Verify dict-like backward-compatibility indexing
        assert cap["status"] == "mocked"
        assert cap["name"] in (
            "playwright_browser_automation",
            "fresh_browser_context",
            "screenshot_trace_artifacts",
        )
        assert "no playwright" in cap.evidence.lower() or "synthetic" in cap.evidence.lower() or "writes" in cap.evidence.lower() or "without browser context" in cap.evidence.lower()

    # Check registry truth
    reg = default_capability_registry
    browser_nav = reg.get("browser_navigation")
    assert browser_nav.state is Availability.MOCKED
    assert browser_nav.available is False

    browser_shot = reg.get("browser_screenshot")
    assert browser_shot.state is Availability.MOCKED
    assert browser_shot.available is False


# ===========================================================================
# TEST 2 — Container truth
# ===========================================================================

@pytest.mark.asyncio
async def test_container_truth(tmp_path: Path) -> None:
    """ContainerSandboxBackend cannot report real AVAILABLE container isolation."""
    backend = ContainerSandboxBackend(workspace_root=tmp_path)
    caps = await backend.capabilities()

    assert len(caps) >= 3
    iso_cap = next(c for c in caps if c.name == "container_isolation")
    assert iso_cap.state is Availability.MOCKED
    assert iso_cap.available is False
    assert iso_cap.is_mocked is True
    assert iso_cap.reason is not None
    assert "simulated" in iso_cap.evidence.lower() or "without container daemon" in iso_cap.evidence.lower()

    # Check registry truth
    reg = default_capability_registry
    container_cap = reg.get("container_isolation")
    assert container_cap.state is Availability.MOCKED
    assert container_cap.available is False
    assert container_cap.is_mocked is True


# ===========================================================================
# TEST 3 — Backend distinction
# ===========================================================================

@pytest.mark.asyncio
async def test_backend_distinction(tmp_path: Path) -> None:
    """LocalSandboxBackend is distinguishable from container isolation."""
    local_backend = LocalSandboxBackend(enabled=True)
    container_backend = ContainerSandboxBackend(workspace_root=tmp_path)

    local_caps = await local_backend.capabilities()
    container_caps = await container_backend.capabilities()

    local_cap = local_caps[0]
    container_iso_cap = next(c for c in container_caps if c.name == "container_isolation")

    # Local is real subprocess execution (READY / AVAILABLE)
    assert local_cap.name == "local_sandbox"
    assert local_cap.state is Availability.READY
    assert local_cap.available is True
    assert local_cap.is_mocked is False

    # Container is MOCKED
    assert container_iso_cap.name == "container_isolation"
    assert container_iso_cap.state is Availability.MOCKED
    assert container_iso_cap.available is False
    assert container_iso_cap.is_mocked is True

    # Behavioral distinction: test actual command execution
    ctx = ExecutionContext(project_id="p1", workspace_id=str(tmp_path), session_id="s1")
    local_sbx_id = await local_backend.create(ctx, {})
    local_res = await local_backend.exec(local_sbx_id, ["echo", "real_local_execution"])
    assert local_res["status"] == "ok"
    assert "real_local_execution" in str(local_res["stdout"])

    container_sbx_id = await container_backend.create(ctx, {})
    container_res = await container_backend.exec(container_sbx_id, ["echo", "real_local_execution"])
    assert "Simulated exec output" in str(container_res["stdout"])


# ===========================================================================
# TEST 4 — WholePlan truth
# ===========================================================================

def test_whole_plan_truth() -> None:
    """_NoOpStepExecutor cannot cause real autonomous WholePlan capability to report AVAILABLE."""
    task_svc = MagicMock()
    plan_svc = MagicMock()
    plan_store = MagicMock()

    # Coordinator with default fallback (_NoOpStepExecutor)
    coordinator_default = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
    )
    caps_default = coordinator_default.capabilities()
    assert len(caps_default) == 1
    assert caps_default[0].name == "whole_plan_autonomous_execution"
    assert caps_default[0].state is Availability.MOCKED
    assert caps_default[0].available is False
    assert caps_default[0].is_mocked is True
    assert "_NoOpStepExecutor" in caps_default[0].implementation

    # Coordinator with concrete real StepExecutor injected
    class _RealDummyStepExecutor:
        async def execute_step(
            self,
            step_id: str,
            step_title: str,
            step_role: AgentRole,
            repair_hint: str = "",
        ) -> ImplementationResult:
            return ImplementationResult(step_id=step_id, summary="executed", success=True)

    coordinator_real = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=_RealDummyStepExecutor(),
    )
    caps_real = coordinator_real.capabilities()
    assert len(caps_real) == 1
    assert caps_real[0].name == "whole_plan_autonomous_execution"
    assert caps_real[0].state is Availability.AVAILABLE
    assert caps_real[0].available is True
    assert caps_real[0].is_mocked is False


# ===========================================================================
# TEST 5 — Real capability
# ===========================================================================

def test_real_capability_verified() -> None:
    """A verified real provider can report AVAILABLE with genuine evidence."""
    reg = default_capability_registry

    real_caps = [
        "workspace_file_read",
        "workspace_file_write",
        "command_execution",
        "local_sandbox_execution",
        "test_runner",
        "code_graph",
        "local_docs_search",
        "persistent_task_store",
        "persistent_plan_store",
        "persistent_checkpoint_store",
        "persistent_memory_store",
    ]

    for name in real_caps:
        cap = reg.get(name)
        assert cap.available is True, f"Expected {name} to be available"
        assert cap.is_mocked is False, f"Expected {name} not to be mocked"
        assert cap.state in (Availability.AVAILABLE, Availability.READY)
        assert len(cap.evidence) > 0, f"Expected evidence for {name}"
        assert len(cap.implementation) > 0, f"Expected implementation for {name}"


# ===========================================================================
# TEST 6 — Unknown behavior
# ===========================================================================

def test_unknown_behavior() -> None:
    """A capability without enough evidence returns UNKNOWN rather than an optimistic status."""
    reg = CapabilityRegistry()
    cap = reg.get("quantum_quantum_optimizer")

    assert cap.name == "quantum_quantum_optimizer"
    assert cap.state is Availability.UNKNOWN
    assert cap.available is False
    assert cap.is_unknown is True
    assert cap.is_mocked is False
    assert "insufficient evidence" in cap.reason.lower()


# ===========================================================================
# TEST 7 — Evidence
# ===========================================================================

def test_evidence_inspection() -> None:
    """Registry exposes evidence supporting capability status and serializes clearly."""
    reg = default_capability_registry
    cap = reg.get("browser_screenshot")

    assert cap.is_mocked is True
    assert "PNG" in cap.evidence
    assert cap.verification_method == "source_inspection"
    assert cap.failure_reason is not None

    d = cap.to_dict()
    assert d["name"] == "browser_screenshot"
    assert d["status"] == "mocked"
    assert d["available"] is False
    assert d["is_mocked"] is True
    assert "evidence" in d
    assert "verification_method" in d
    assert "last_verified_at" in d


# ===========================================================================
# TEST 8 — Compatibility
# ===========================================================================

def test_capability_status_backward_compatibility() -> None:
    """Existing AgentRuntime.capabilities() users remain compatible without duplicate types."""
    # Ensure CapabilityRecord IS CapabilityStatus (no overlapping second type)
    assert CapabilityRecord is CapabilityStatus

    # Old-style construction with name and state only
    cap_old = CapabilityStatus(name="memory_access", state=Availability.READY)
    assert cap_old.name == "memory_access"
    assert cap_old.state is Availability.READY
    assert cap_old.available is True
    assert cap_old.reason is None

    # Old-style construction with reason
    cap_unavail = CapabilityStatus(
        name="gpu_inference",
        state=Availability.UNAVAILABLE,
        reason="No CUDA device found",
    )
    assert cap_unavail.available is False
    assert cap_unavail.reason == "No CUDA device found"

    # Mapping / dict protocol support
    assert cap_old["name"] == "memory_access"
    assert cap_old["available"] is True


# ===========================================================================
# TEST 9 — Real-provider requirement
# ===========================================================================

@pytest.mark.asyncio
async def test_real_provider_requirement() -> None:
    """If a caller explicitly requires an AVAILABLE real capability, a MOCKED provider cannot satisfy that requirement."""
    reg = default_capability_registry

    # Requiring an available capability succeeds
    real_cap = reg.require("workspace_file_read")
    assert real_cap.available is True

    # Requiring a mocked capability raises CapabilityMockedError
    with pytest.raises(CapabilityMockedError) as exc_info:
        reg.require("browser_navigation")
    assert "MOCKED" in str(exc_info.value)
    assert issubclass(CapabilityMockedError, CapabilityUnavailableError)

    # Requiring an unknown capability raises CapabilityUnavailableError
    with pytest.raises(CapabilityUnavailableError):
        reg.require("hypothetical_future_capability")

    # select_provider rejects mocked candidates when require_real=True
    mock_candidate = ContainerSandboxBackend()
    local_candidate = LocalSandboxBackend(enabled=True)

    selected = await reg.select_provider(
        "local_sandbox_execution",
        [mock_candidate, local_candidate],
        require_real=True,
    )
    assert selected is local_candidate

    with pytest.raises(CapabilityUnavailableError):
        await reg.select_provider(
            "container_isolation",
            [mock_candidate],
            require_real=True,
        )


# ===========================================================================
# TEST 10 — General semantics
# ===========================================================================

def test_general_capability_semantics() -> None:
    """Tests validate capability semantics generically without hardcoding specific class names."""
    reg = CapabilityRegistry()

    # Generic provider states
    reg.register(
        CapabilityRecord(
            name="custom_math_engine",
            state=Availability.AVAILABLE,
            implementation="CustomMathEngine",
            provider_or_backend="native_c",
            evidence="benchmark test passed with 100% precision",
        )
    )
    reg.register(
        CapabilityRecord(
            name="simulated_gpu",
            state=Availability.MOCKED,
            reason="Software fallback emulation",
            implementation="CPUEmulator",
            provider_or_backend="cpu",
            evidence="Synthetic CUDA calls redirected to CPU thread pool",
        )
    )
    reg.register(
        CapabilityRecord(
            name="cloud_backup",
            state=Availability.DEGRADED,
            reason="High network latency to storage bucket",
            implementation="S3Adapter",
            provider_or_backend="aws",
            evidence="Bucket reachable but write speed < 1MB/s",
        )
    )
    reg.register(
        CapabilityRecord(
            name="quantum_teleport",
            state=Availability.UNAVAILABLE,
            reason="Hardware offline",
            implementation="QubitCore",
            provider_or_backend="rigetti",
            evidence="Connection refused on port 9000",
        )
    )

    # Verification of canonical invariants
    assert reg.is_available("custom_math_engine") is True
    assert reg.get("custom_math_engine").is_mocked is False

    assert reg.is_available("simulated_gpu") is False
    assert reg.get("simulated_gpu").is_mocked is True

    assert reg.is_available("cloud_backup") is False
    assert reg.get("cloud_backup").is_degraded is True

    assert reg.is_available("quantum_teleport") is False
    assert reg.get("quantum_teleport").is_unavailable is True

    assert reg.is_available("totally_nonexistent") is False
    assert reg.get("totally_nonexistent").is_unknown is True

    # Test list contains all 4 registered
    all_caps = reg.list()
    assert len(all_caps) == 4
