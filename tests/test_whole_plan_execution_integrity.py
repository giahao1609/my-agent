from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent_role import AgentRole
from core.agent_run import AgentRunRecord, AgentRunState
from core.agent_run_service import AgentRunService
from core.capabilities import Availability, default_capability_registry
from core.context import ExecutionContext
from core.handoff_contracts import (
    ImplementationResult,
    ReviewResult,
    ReviewStatus,
    TestResult,
)
from core.model import ModelMessage, ModelTurn, ModelToolCall
from core.orchestrator import Orchestrator
from core.plan import PlanState, PlanStepState
from core.plan_service import PlanService
from core.step_execution import CoderRuntimeStepExecutor, StepExecutionEvidence
from core.task import TaskState
from core.task_service import TaskService
from core.tool_executor import ToolExecutor
from core.tool_policy import ToolPolicy
from core.tools import ToolDefinition, ToolPermission
from core.verification_gate_coordinator import VerificationGateCoordinator
from core.whole_plan_coordinator import (
    StepExecutor,
    WholePlanCoordinator,
    _NoOpStepExecutor,
)
from integrations.runtime_bridge import RuntimeBridge
from my_agent_mcp.server import run_whole_plan
from persistence.sqlite_agent_run_store import SQLiteAgentRunStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore
from tools.coder_tools import build_coder_tool_registry
from agents.coder_agent import CoderAgent
from agents.coder_runtime_worker import CoderRuntimeWorker
from agents.coder_stack import CoderAgentStack


class _FakeMemory:
    async def recall(self, context, query, limit=10):
        return ()

    async def capture(self, context, content, importance=0.5, tags=()):
        pass


class _FakeKnowledge:
    async def search(self, context, query, limit=10):
        return ()


async def _setup_env(tmp_path: Path):
    db = tmp_path / "test.db"
    proj_store = SQLiteProjectStore(db)
    task_store = SQLiteTaskStore(db)
    plan_store = SQLitePlanStore(db)
    agent_run_store = SQLiteAgentRunStore(db)
    await proj_store.initialize()
    await task_store.initialize()
    await plan_store.initialize()
    await agent_run_store.initialize()

    from core.project import ProjectRecord
    await proj_store.create(ProjectRecord(project_id="proj-1", name="P1", workspace_path=str(tmp_path)))

    task_svc = TaskService(project_store=proj_store, task_store=task_store)
    plan_svc = PlanService(task_store=task_store, plan_store=plan_store)
    agent_run_svc = AgentRunService(agent_run_store)

    return task_svc, plan_svc, plan_store, task_store, agent_run_svc


# ===========================================================================
# TEST 1 — NOOP CANNOT COMPLETE
# ===========================================================================

@pytest.mark.asyncio
async def test_noop_cannot_complete_step(tmp_path: Path) -> None:
    """_NoOpStepExecutor produces simulated output that CANNOT mark step COMPLETED."""
    task_svc, plan_svc, plan_store, task_store, _ = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-noop", project_id="proj-1", objective="NoOp test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-noop",
        steps=(("step-noop-1", "Simulated Step", "instruction"),),
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=_NoOpStepExecutor(),
        max_auto_repair_attempts=0,
    )

    summary = await coordinator.execute_whole_plan("plan-noop", tmp_path)

    assert summary.plan_completed is False
    assert summary.steps_completed == 0
    assert summary.steps_failed == 1

    steps = await plan_store.list_steps("plan-noop")
    assert steps[0].state == PlanStepState.FAILED


# ===========================================================================
# TEST 2 — MISSING REAL EXECUTOR
# ===========================================================================

@pytest.mark.asyncio
async def test_missing_real_executor_blocks_or_fails(tmp_path: Path) -> None:
    """When no step_executor is injected, default coordinator cannot falsely complete steps."""
    task_svc, plan_svc, plan_store, task_store, _ = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-none", project_id="proj-1", objective="No executor")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-none",
        steps=(("step-none-1", "Missing Executor Step", "instruction"),),
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=None,
        max_auto_repair_attempts=0,
    )

    summary = await coordinator.execute_whole_plan("plan-none", tmp_path)

    assert summary.plan_completed is False
    assert summary.steps_completed == 0
    assert summary.steps_failed == 1

    steps = await plan_store.list_steps("plan-none")
    assert steps[0].state == PlanStepState.FAILED


# ===========================================================================
# TEST 3 — REAL EXECUTOR SUCCESS (WITH ACTUAL WORKSPACE MUTATION)
# ===========================================================================

class _FileWritingModel:
    def __init__(self, filename: str, content: str) -> None:
        self.filename = filename
        self.content = content
        self.calls = 0

    async def generate(self, messages, tools, context, target=None):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                text="Writing requested file",
                tool_calls=(
                    ModelToolCall(
                        tool_call_id="call-write-1",
                        name="write_file",
                        arguments={"path": self.filename, "content": self.content},
                    ),
                ),
            )
        return ModelTurn(text=f"Wrote {self.filename} successfully", stop=True)


@pytest.mark.asyncio
async def test_real_executor_success_with_workspace_mutation(tmp_path: Path) -> None:
    """Real CoderRuntimeStepExecutor runs tool loop, mutates workspace, passes gates, and completes step."""
    task_svc, plan_svc, plan_store, task_store, agent_run_svc = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-real", project_id="proj-1", objective="Real execution")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-real",
        steps=(("step-real-1", "Create app file", "instruction"),),
    )

    runtime = RuntimeBridge()
    registry = build_coder_tool_registry()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=_FakeMemory(),
        knowledge=_FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)
    model = _FileWritingModel(filename="hello.txt", content="real execution evidence")
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=registry,
    )

    class _StackWrapper:
        def __init__(self, ag, orch):
            self.agent = ag
            self.orchestrator = orch

    stack = _StackWrapper(agent, orchestrator)

    async def _launch_worker(session_id: str):
        return asyncio.create_task(worker.run_session(session_id))

    real_executor = CoderRuntimeStepExecutor(
        coder_stack=stack,
        workspace_path=tmp_path,
        project_id="proj-1",
        task_id=task.task_id,
        plan_id="plan-real",
        agent_run_service=agent_run_svc,
        worker_task_launcher=_launch_worker,
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=real_executor,
    )

    summary = await coordinator.execute_whole_plan("plan-real", tmp_path)

    assert summary.plan_completed is True
    assert summary.steps_completed == 1
    assert summary.steps_failed == 0

    target_file = tmp_path / "hello.txt"
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "real execution evidence"

    steps = await plan_store.list_steps("plan-real")
    assert steps[0].state == PlanStepState.COMPLETED


# ===========================================================================
# TEST 4 — EXECUTOR FAILURE
# ===========================================================================

class _FailingModel:
    async def generate(self, messages, tools, context, target=None):
        raise RuntimeError("Model runtime internal crash")


@pytest.mark.asyncio
async def test_executor_failure_propagates_truthfully(tmp_path: Path) -> None:
    """When the executor encounters an error, the step is NOT marked COMPLETED."""
    task_svc, plan_svc, plan_store, task_store, agent_run_svc = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-fail", project_id="proj-1", objective="Fail test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-fail",
        steps=(("step-fail-1", "Crash step", "instruction"),),
    )

    runtime = RuntimeBridge()
    registry = build_coder_tool_registry()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=_FakeMemory(),
        knowledge=_FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)
    model = _FailingModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=registry,
    )

    class _StackWrapper:
        def __init__(self, ag, orch):
            self.agent = ag
            self.orchestrator = orch

    stack = _StackWrapper(agent, orchestrator)

    async def _launch_worker(session_id: str):
        return asyncio.create_task(worker.run_session(session_id))

    real_executor = CoderRuntimeStepExecutor(
        coder_stack=stack,
        workspace_path=tmp_path,
        project_id="proj-1",
        task_id=task.task_id,
        plan_id="plan-fail",
        agent_run_service=agent_run_svc,
        worker_task_launcher=_launch_worker,
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=real_executor,
        max_auto_repair_attempts=0,
    )

    with pytest.raises(RuntimeError, match="Model runtime internal crash"):
        await coordinator.execute_whole_plan("plan-fail", tmp_path)

    steps = await plan_store.list_steps("plan-fail")
    assert steps[0].state != PlanStepState.COMPLETED


# ===========================================================================
# TEST 5 — VERIFICATION FAILURE
# ===========================================================================

@pytest.mark.asyncio
async def test_verification_failure_prevents_completion(tmp_path: Path) -> None:
    """Even if executor ran with success, verification gate failure prevents step COMPLETED."""
    task_svc, plan_svc, plan_store, task_store, _ = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-gate-fail", project_id="proj-1", objective="Gate fail")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-gate-fail",
        steps=(("step-gf-1", "Pass execution fail gate", "instruction"),),
    )

    class _FailingTestRunner:
        async def run_tests(self, workspace_path, step_id):
            return TestResult(step_id=step_id, total_tests=5, passed_tests=3, failed_tests=2)

    class _MockRealExecutor:
        is_real_executor = True
        async def execute_step(self, step_id, step_title, step_role, repair_hint=""):
            return ImplementationResult(
                step_id=step_id,
                summary="Work done",
                success=True,
                is_mocked=False,
                execution_evidence={"is_real": True},
            )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=_MockRealExecutor(),
        test_runner_registry=_FailingTestRunner(),
        max_auto_repair_attempts=0,
    )

    summary = await coordinator.execute_whole_plan("plan-gate-fail", tmp_path)

    assert summary.plan_completed is False
    assert summary.steps_completed == 0
    assert summary.steps_failed == 1

    steps = await plan_store.list_steps("plan-gate-fail")
    assert steps[0].state == PlanStepState.FAILED


# ===========================================================================
# TEST 6 — MOCKED RESULT REJECTION
# ===========================================================================

def test_mocked_result_rejected_by_verification_gate() -> None:
    """VerificationGateCoordinator strictly rejects results flagged as is_mocked or [NoOpStepExecutor]."""
    gate = VerificationGateCoordinator()

    mock_result_1 = ImplementationResult(
        step_id="step-m1",
        summary="Done",
        success=True,
        is_mocked=True,
    )
    rev_1 = gate.evaluate(step_id="step-m1", implementation_result=mock_result_1)
    assert rev_1.status == ReviewStatus.REJECTED
    assert "simulated/mocked" in rev_1.summary

    mock_result_2 = ImplementationResult(
        step_id="step-m2",
        summary="[NoOpStepExecutor] Simulated execution",
        success=True,
        is_mocked=False,
    )
    rev_2 = gate.evaluate(step_id="step-m2", implementation_result=mock_result_2)
    assert rev_2.status == ReviewStatus.REJECTED
    assert "simulated/mocked" in rev_2.summary


# ===========================================================================
# TEST 7 — MCP WHOLE PLAN TRUTH
# ===========================================================================

@pytest.mark.asyncio
async def test_mcp_run_whole_plan_truth(tmp_path: Path) -> None:
    """run_whole_plan tool executes real coordinator and does not report completion on simulated steps."""
    from persistence.sqlite_project_store import SQLiteProjectStore
    from persistence.sqlite_task_store import SQLiteTaskStore
    from persistence.sqlite_plan_store import SQLitePlanStore
    import my_agent_mcp.server as srv

    db_path = srv.DB_PATH
    plan = await srv.plans.get_plan("non-existent-plan")
    res = await run_whole_plan(plan_id="non-existent-plan", workspace_path=str(tmp_path))
    assert res["plan_completed"] is False
    assert res["steps_completed"] == 0


# ===========================================================================
# TEST 8 — AGENT RUN EVIDENCE
# ===========================================================================

@pytest.mark.asyncio
async def test_agent_run_ledger_contains_execution_evidence(tmp_path: Path) -> None:
    """Real execution creates and completes an AgentRun record containing structured evidence."""
    task_svc, plan_svc, plan_store, task_store, agent_run_svc = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-ledger", project_id="proj-1", objective="Ledger test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-ledger",
        steps=(("step-led-1", "Ledger file creation", "instruction"),),
    )

    runtime = RuntimeBridge()
    registry = build_coder_tool_registry()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=_FakeMemory(),
        knowledge=_FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)
    model = _FileWritingModel(filename="evidence.txt", content="verifiable content")
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=registry,
    )

    class _StackWrapper:
        def __init__(self, ag, orch):
            self.agent = ag
            self.orchestrator = orch

    stack = _StackWrapper(agent, orchestrator)

    async def _launch_worker(session_id: str):
        return asyncio.create_task(worker.run_session(session_id))

    real_executor = CoderRuntimeStepExecutor(
        coder_stack=stack,
        workspace_path=tmp_path,
        project_id="proj-1",
        task_id=task.task_id,
        plan_id="plan-ledger",
        agent_run_service=agent_run_svc,
        worker_task_launcher=_launch_worker,
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=real_executor,
    )

    summary = await coordinator.execute_whole_plan("plan-ledger", tmp_path)
    assert summary.plan_completed is True

    runs = await agent_run_svc.list_runs_for_step("step-led-1")
    assert len(runs) >= 1
    completed_run = runs[0]
    assert completed_run.state == AgentRunState.COMPLETED
    assert completed_run.result is not None
    assert "evidence.txt" in [f.path for f in completed_run.result.changed_files]
    assert completed_run.result.metadata.get("is_real") is True
    assert completed_run.result.metadata.get("executor_identity") == "CoderRuntimeStepExecutor"


# ===========================================================================
# TEST 9 — EXISTING PLAN TRANSITIONS PRESERVED
# ===========================================================================

@pytest.mark.asyncio
async def test_plan_state_transitions_preserved(tmp_path: Path) -> None:
    """State machines for Plan and PlanStep remain intact across whole plan lifecycle."""
    task_svc, plan_svc, plan_store, task_store, _ = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-states", project_id="proj-1", objective="State test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-states",
        steps=(("step-s1", "Step 1", "instruction"),),
    )

    plan_initial = await plan_store.get_plan("plan-states")
    assert plan_initial.state == PlanState.DRAFT

    class _MockRealPassExecutor:
        is_real_executor = True
        async def execute_step(self, step_id, step_title, step_role, repair_hint=""):
            return ImplementationResult(
                step_id=step_id,
                summary="Done",
                success=True,
                is_mocked=False,
                execution_evidence={"is_real": True},
            )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=_MockRealPassExecutor(),
    )

    await coordinator.execute_whole_plan("plan-states", tmp_path)

    plan_final = await plan_store.get_plan("plan-states")
    assert plan_final.state == PlanState.COMPLETED
    task_final = await task_store.get(task.task_id)
    assert task_final.state == TaskState.COMPLETED


# ===========================================================================
# TEST 10 — CAPABILITY STATUS
# ===========================================================================

def test_capability_status_reflection() -> None:
    """WholePlanCoordinator reports AVAILABLE only when a concrete real executor is present."""
    task_svc = MagicMock()
    plan_svc = MagicMock()
    plan_store = MagicMock()

    coordinator_noop = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=_NoOpStepExecutor(),
    )
    caps_noop = coordinator_noop.capabilities()
    assert caps_noop[0].state is Availability.MOCKED
    assert caps_noop[0].available is False

    class _MockRealStepExecutor:
        is_real_executor = True
        async def execute_step(self, step_id, step_title, step_role, repair_hint=""):
            return ImplementationResult(step_id=step_id, summary="ok", success=True)

    coordinator_real = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=_MockRealStepExecutor(),
    )
    caps_real = coordinator_real.capabilities()
    assert caps_real[0].state is Availability.AVAILABLE
    assert caps_real[0].available is True


# ===========================================================================
# TEST 11 — GOVERNANCE PRESERVED (APPROVAL GATE ENFORCEMENT)
# ===========================================================================

class _ApprovalRequiringModel:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages, tools, context, target=None):
        self.calls += 1
        return ModelTurn(
            text="Running rm -rf command",
            tool_calls=(
                ModelToolCall(
                    tool_call_id="call-dangerous-1",
                    name="run_command",
                    arguments={"command": "rm -rf /tmp/test"},
                ),
            ),
        )


@pytest.mark.asyncio
async def test_governance_approval_preserved_in_real_execution(tmp_path: Path) -> None:
    """Dangerous commands trigger approval requirement and prevent auto-completion."""
    task_svc, plan_svc, plan_store, task_store, agent_run_svc = await _setup_env(tmp_path)
    task = await task_svc.create_task(task_id="task-gov", project_id="proj-1", objective="Governance test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-gov",
        steps=(("step-gov-1", "Delete files", "instruction"),),
    )

    runtime = RuntimeBridge()
    registry = build_coder_tool_registry()
    policy = ToolPolicy()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=_FakeMemory(),
        knowledge=_FakeKnowledge(),
        tools=ToolExecutor(registry, policy),
    )
    agent = CoderAgent(orchestrator)
    model = _ApprovalRequiringModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=registry,
    )

    class _StackWrapper:
        def __init__(self, ag, orch):
            self.agent = ag
            self.orchestrator = orch

    stack = _StackWrapper(agent, orchestrator)

    async def _launch_worker(session_id: str):
        return asyncio.create_task(worker.run_session(session_id))

    real_executor = CoderRuntimeStepExecutor(
        coder_stack=stack,
        workspace_path=tmp_path,
        project_id="proj-1",
        task_id=task.task_id,
        plan_id="plan-gov",
        agent_run_service=agent_run_svc,
        worker_task_launcher=_launch_worker,
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=real_executor,
        max_auto_repair_attempts=0,
    )

    summary = await coordinator.execute_whole_plan("plan-gov", tmp_path)

    assert summary.plan_completed is False
    assert summary.steps_completed == 0
    assert summary.steps_failed == 1

    steps = await plan_store.list_steps("plan-gov")
    assert steps[0].state == PlanStepState.FAILED
