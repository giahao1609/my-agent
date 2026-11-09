from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_role import AgentRole
from core.agent_run import AgentRunState
from core.agent_run_service import AgentRunService
from core.context import ExecutionContext
from core.handoff_contracts import TestResult
from core.plan import PlanState, PlanStepState
from core.plan_service import PlanService
from core.project import ProjectRecord
from core.step_execution import CoderRuntimeStepExecutor
from core.targeted_test_selector import TargetedTestSelector
from core.task import TaskState
from core.task_service import TaskService
from core.whole_plan_coordinator import (
    WholePlanCoordinator,
)
from persistence.sqlite_agent_run_store import SQLiteAgentRunStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def _setup_bridge_env(tmp_path: Path):
    db_path = tmp_path / "test_bridge.db"
    proj_store = SQLiteProjectStore(db_path)
    task_store = SQLiteTaskStore(db_path)
    plan_store = SQLitePlanStore(db_path)
    run_store = SQLiteAgentRunStore(db_path)

    await proj_store.initialize()
    await task_store.initialize()
    await plan_store.initialize()
    await run_store.initialize()

    await proj_store.create(
        ProjectRecord(
            project_id="proj-ext",
            name="External Project",
            workspace_path=str(tmp_path),
        )
    )

    task_svc = TaskService(project_store=proj_store, task_store=task_store)
    plan_svc = PlanService(task_store=task_store, plan_store=plan_store)
    run_svc = AgentRunService(run_store)

    return task_svc, plan_svc, plan_store, task_store, run_svc


class MockOrchestrator:
    def __init__(self) -> None:
        self.sessions: dict[str, list[str]] = {}
        self.session_counter = 0

    async def start_session(self, context: ExecutionContext) -> str:
        self.session_counter += 1
        sid = f"session-{self.session_counter}"
        self.sessions[sid] = []
        return sid

    async def send(self, session_id: str, message: str, context: ExecutionContext) -> None:
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append(message)


class MockCoderAgent:
    def __init__(self, orchestrator: MockOrchestrator) -> None:
        self.orchestrator = orchestrator


@pytest.mark.asyncio
async def test_external_coder_step_execution_waiting_for_host(tmp_path: Path):
    """Under external mode (worker_task_launcher=None), CoderRuntimeStepExecutor does not fail.

    It enqueues the prompt, marks AgentRun as WAITING, and returns WAITING_FOR_HOST evidence.
    """
    task_svc, plan_svc, plan_store, task_store, run_svc = await _setup_bridge_env(tmp_path)

    task = await task_svc.create_task(
        task_id="task-ext-1",
        project_id="proj-ext",
        objective="External host step execution",
    )
    await task_svc.start_task(task.task_id)

    plan = await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-ext-1",
        steps=(("step-ext-1", "Build data pipeline", "Implement ETL pipeline"),),
    )

    orchestrator = MockOrchestrator()
    mock_stack = SimpleNamespace(
        orchestrator=orchestrator,
        agent=MockCoderAgent(orchestrator),
    )

    executor = CoderRuntimeStepExecutor(
        coder_stack=mock_stack,
        workspace_path=tmp_path,
        project_id="proj-ext",
        task_id=task.task_id,
        plan_id=plan.plan_id,
        agent_run_service=run_svc,
        worker_task_launcher=None,  # External host mode!
    )

    result = await executor.execute_step(
        step_id="step-ext-1",
        step_title="Build data pipeline",
        step_role=AgentRole.BACKEND_CODER,
        step_instruction="Implement ETL pipeline",
    )

    assert result.success is True
    assert result.is_mocked is False
    evidence = result.execution_evidence
    assert evidence["runtime_id"] == "external_host_bridge"
    assert "WAITING_FOR_HOST" in evidence["notes"]
    assert evidence["session_id"] == "session-1"

    # Verify command was enqueued to orchestrator
    assert len(orchestrator.sessions["session-1"]) == 1
    assert "Build data pipeline" in orchestrator.sessions["session-1"][0]

    # Verify AgentRun was transitioned to WAITING
    runs = await run_svc.list_runs_for_step("step-ext-1")
    assert len(runs) == 1
    assert runs[0].state == AgentRunState.WAITING
    assert runs[0].session_id == "session-1"


@pytest.mark.asyncio
async def test_whole_plan_external_mode_nonblocking_status(tmp_path: Path):
    """run_whole_plan does not lock for 180s when external host is driving.

    It returns execution_state=WAITING_FOR_HOST immediately.
    """
    task_svc, plan_svc, plan_store, task_store, run_svc = await _setup_bridge_env(tmp_path)

    task = await task_svc.create_task(
        task_id="task-ext-2",
        project_id="proj-ext",
        objective="Multi-step plan with host",
    )
    await task_svc.start_task(task.task_id)

    plan = await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-ext-2",
        steps=(
            ("step-1", "Design API contracts", "Write OpenAPI spec"),
            ("step-2", "Implement handlers", "Implement API endpoints"),
        ),
    )

    orchestrator = MockOrchestrator()
    mock_stack = SimpleNamespace(
        orchestrator=orchestrator,
        agent=MockCoderAgent(orchestrator),
    )

    executor = CoderRuntimeStepExecutor(
        coder_stack=mock_stack,
        workspace_path=tmp_path,
        project_id="proj-ext",
        task_id=task.task_id,
        plan_id=plan.plan_id,
        agent_run_service=run_svc,
        worker_task_launcher=None,  # External host mode
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        agent_run_service=run_svc,
        project_id="proj-ext",
    )

    summary = await coordinator.execute_whole_plan(plan.plan_id, tmp_path)

    assert summary.execution_state == "WAITING_FOR_HOST"
    assert summary.plan_completed is False
    assert summary.current_step_id == "step-1"
    assert summary.current_session_id == "session-1"
    assert "waiting for host execution" in summary.action_required

    # Step 1 must be RUNNING
    s1 = await plan_store.get_step("step-1")
    assert s1.state == PlanStepState.RUNNING

    # Step 2 must remain PENDING
    s2 = await plan_store.get_step("step-2")
    assert s2.state == PlanStepState.PENDING


@pytest.mark.asyncio
async def test_advance_step_flow_and_final_verification_gate(tmp_path: Path):
    """advance_step verifies host evidence, advances through steps, and triggers Final Verification Gate."""
    task_svc, plan_svc, plan_store, task_store, run_svc = await _setup_bridge_env(tmp_path)

    task = await task_svc.create_task(
        task_id="task-ext-3",
        project_id="proj-ext",
        objective="Host-driven 2-step advancement",
    )
    await task_svc.start_task(task.task_id)

    plan = await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-ext-3",
        steps=(
            ("step-s1", "Step 1 feature", "Do step 1"),
            ("step-s2", "Step 2 feature", "Do step 2"),
        ),
    )

    orchestrator = MockOrchestrator()
    mock_stack = SimpleNamespace(
        orchestrator=orchestrator,
        agent=MockCoderAgent(orchestrator),
    )

    executor = CoderRuntimeStepExecutor(
        coder_stack=mock_stack,
        workspace_path=tmp_path,
        project_id="proj-ext",
        task_id=task.task_id,
        plan_id=plan.plan_id,
        agent_run_service=run_svc,
        worker_task_launcher=None,
    )

    class PassingMockTestRunner:
        async def run_tests(self, workspace_path, step_id, target_files=None, timeout_seconds=120.0):
            return TestResult(
                step_id=step_id,
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
            )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        test_runner_registry=PassingMockTestRunner(),
        agent_run_service=run_svc,
        project_id="proj-ext",
    )

    # 1. Start whole plan -> transitions Step 1 to WAITING_FOR_HOST
    summary = await coordinator.execute_whole_plan(plan.plan_id, tmp_path)
    assert summary.execution_state == "WAITING_FOR_HOST"
    assert summary.current_step_id == "step-s1"

    # 2. Host advances Step 1 with evidence
    advance_res1 = await coordinator.advance_step(
        step_id="step-s1",
        workspace_path=tmp_path,
        execution_evidence={
            "session_id": "session-1",
            "files_changed": ["core/feature.py"],
        },
    )

    assert advance_res1.status == "step_approved"
    assert advance_res1.execution_state == "WAITING_FOR_HOST"
    assert advance_res1.next_step_id == "step-s2"
    assert advance_res1.plan_completed is False

    # Verify Step 1 is COMPLETED and Step 2 is RUNNING
    s1 = await plan_store.get_step("step-s1")
    assert s1.state == PlanStepState.COMPLETED
    s2 = await plan_store.get_step("step-s2")
    assert s2.state == PlanStepState.RUNNING

    # 3. Host advances Step 2 (final step)
    advance_res2 = await coordinator.advance_step(
        step_id="step-s2",
        workspace_path=tmp_path,
        execution_evidence={
            "session_id": advance_res1.next_session_id,
            "files_changed": ["core/feature2.py"],
        },
    )

    # All steps completed -> Final Verification Gate runs -> Plan COMPLETED!
    assert advance_res2.status == "plan_completed"
    assert advance_res2.execution_state == "COMPLETED"
    assert advance_res2.plan_completed is True

    # Check Plan and Task state
    final_plan = await plan_store.get_plan(plan.plan_id)
    assert final_plan.state == PlanState.COMPLETED
    final_task = await task_store.get(task.task_id)
    assert final_task.state == TaskState.COMPLETED


@pytest.mark.asyncio
async def test_advance_step_rejection_and_reflexion_hint(tmp_path: Path):
    """advance_step returns step_rejected and delivers Reflexion hint to session queue on gate failure."""
    task_svc, plan_svc, plan_store, task_store, run_svc = await _setup_bridge_env(tmp_path)

    task = await task_svc.create_task(
        task_id="task-ext-4",
        project_id="proj-ext",
        objective="Rejection and auto-repair test",
    )
    await task_svc.start_task(task.task_id)

    plan = await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-ext-4",
        steps=(("step-fail-1", "Faulty feature", "Implement faulty feature"),),
    )

    orchestrator = MockOrchestrator()
    mock_stack = SimpleNamespace(
        orchestrator=orchestrator,
        agent=MockCoderAgent(orchestrator),
    )

    executor = CoderRuntimeStepExecutor(
        coder_stack=mock_stack,
        workspace_path=tmp_path,
        project_id="proj-ext",
        task_id=task.task_id,
        plan_id=plan.plan_id,
        agent_run_service=run_svc,
        worker_task_launcher=None,
    )

    class FailingMockTestRunner:
        async def run_tests(self, workspace_path, step_id, target_files=None, timeout_seconds=120.0):
            return TestResult(
                step_id=step_id,
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                failure_details=("AssertionError: expected True but got False",),
            )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        test_runner_registry=FailingMockTestRunner(),
        agent_run_service=run_svc,
        project_id="proj-ext",
    )

    # Start whole plan
    await coordinator.execute_whole_plan(plan.plan_id, tmp_path)

    # Advance step with failure
    res = await coordinator.advance_step(
        step_id="step-fail-1",
        workspace_path=tmp_path,
        execution_evidence={
            "session_id": "session-1",
            "files_changed": ["core/faulty.py"],
        },
    )

    assert res.status == "step_rejected"
    assert res.execution_state == "WAITING_FOR_HOST"
    assert "AssertionError" in res.repair_hint

    # Verify repair hint was enqueued in session-1
    assert len(orchestrator.sessions["session-1"]) == 2
    assert "Repair required" in orchestrator.sessions["session-1"][1]


@pytest.mark.asyncio
async def test_advance_step_unknown_step_raises_keyerror(tmp_path: Path):
    task_svc, plan_svc, plan_store, task_store, run_svc = await _setup_bridge_env(tmp_path)

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        agent_run_service=run_svc,
        project_id="proj-ext",
    )

    with pytest.raises(KeyError, match="unknown plan step: nonexistent-step"):
        await coordinator.advance_step(
            step_id="nonexistent-step",
            workspace_path=tmp_path,
        )


@pytest.mark.asyncio
async def test_targeted_test_selector_reduces_execution_scope(tmp_path: Path):
    """TargetedTestSelector isolates test execution to only tests directly related to changed files."""
    selector = TargetedTestSelector()
    res = selector.select_tests(
        workspace_path=Path.cwd(),
        changed_files=["core/step_execution.py"],
    )
    assert res.is_targeted is True
    # Should only run test_step_execution or mapped candidate, not 1,105 tests
    assert len(res.selected_tests) >= 1
    assert all("step_execution" in t for t in res.selected_tests)


@pytest.mark.asyncio
async def test_mcp_advance_plan_step_tool_registration():
    """Verify advance_plan_step tool is registered on the MCP server."""
    from my_agent_mcp import server
    assert hasattr(server, "advance_plan_step")
    assert callable(server.advance_plan_step)
