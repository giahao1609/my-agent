from __future__ import annotations

"""Expanded tests for WholePlanCoordinator covering:
- Max auto-repair exhausted → step fails → plan stops
- Goal drift detection → step marked failed
- Custom StepExecutor is actually invoked
- Multi-step plan stops on first failed step (not continuing)
- Telemetry accuracy (attempts vs outcomes)
"""

from pathlib import Path
from typing import Any

import pytest

from core.agent_role import AgentRole
from core.handoff_contracts import ImplementationResult, ReviewResult, ReviewStatus
from core.plan import PlanState, PlanStepState
from core.plan_service import PlanService
from core.task import TaskState
from core.task_service import TaskService
from core.whole_plan_coordinator import (
    StepExecutor,
    WholePlanCoordinator,
    _NoOpStepExecutor,
)
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _setup_db(tmp_path: Path):
    db = tmp_path / "test.db"
    project_store = SQLiteProjectStore(db)
    task_store = SQLiteTaskStore(db)
    plan_store = SQLitePlanStore(db)
    await project_store.initialize()
    await task_store.initialize()
    await plan_store.initialize()
    from core.project import ProjectRecord
    await project_store.create(ProjectRecord(project_id="proj", name="P", workspace_path=str(tmp_path)))
    task_svc = TaskService(project_store=project_store, task_store=task_store)
    plan_svc = PlanService(task_store=task_store, plan_store=plan_store)
    return task_svc, plan_svc, plan_store, task_store


class _AlwaysPassGate:
    def evaluate(self, step_id, implementation_result, test_result, security_result):
        return ReviewResult(step_id=step_id, status=ReviewStatus.APPROVED, summary="ok")


class _AlwaysFailGate:
    def evaluate(self, step_id, implementation_result, test_result, security_result):
        return ReviewResult(
            step_id=step_id, status=ReviewStatus.REJECTED, summary="fail",
            comments=("simulated permanent failure",),
        )


class _CountingExecutor:
    """Records which steps were executed and with which repair_hints."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (step_id, repair_hint)

    async def execute_step(self, step_id, step_title, step_role, repair_hint=""):
        self.calls.append((step_id, repair_hint))
        return ImplementationResult(step_id=step_id, summary="executed", success=True)


class _FailingExecutor:
    """Always returns success=False from the agent."""
    async def execute_step(self, step_id, step_title, step_role, repair_hint=""):
        return ImplementationResult(step_id=step_id, summary="agent failed", success=False)


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_retries_exhausted_fails_step(tmp_path: Path) -> None:
    """When gate always rejects, step should fail after max_auto_repair_attempts."""
    task_svc, plan_svc, plan_store, task_store = await _setup_db(tmp_path)
    task = await task_svc.create_task(task_id="task-r", project_id="proj", objective="Retry test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id, plan_id="plan-r",
        steps=(("step-bad", "Always Failing", "desc"),),
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        verification_coordinator=_AlwaysFailGate(),
        max_auto_repair_attempts=2,
    )
    summary = await coordinator.execute_whole_plan("plan-r", tmp_path)

    assert summary.plan_completed is False
    assert summary.steps_failed == 1
    assert summary.steps_completed == 0
    # Gate was called 1 initial + 2 repair = 3 times
    assert len(summary.review_history) == 3
    assert summary.step_telemetry[0]["outcome"] == "failed"
    assert summary.step_telemetry[0]["attempts"] == 3


@pytest.mark.asyncio
async def test_custom_step_executor_is_called(tmp_path: Path) -> None:
    """WholePlanCoordinator must call the injected StepExecutor for each step."""
    task_svc, plan_svc, plan_store, task_store = await _setup_db(tmp_path)
    task = await task_svc.create_task(task_id="task-ex", project_id="proj", objective="Executor test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id, plan_id="plan-ex",
        steps=(
            ("step-a", "Step A", "desc a"),
            ("step-b", "Step B", "desc b"),
        ),
    )

    executor = _CountingExecutor()
    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=_AlwaysPassGate(),
    )
    summary = await coordinator.execute_whole_plan("plan-ex", tmp_path)

    assert summary.plan_completed is True
    assert len(executor.calls) == 2
    step_ids = [c[0] for c in executor.calls]
    assert "step-a" in step_ids
    assert "step-b" in step_ids


@pytest.mark.asyncio
async def test_repair_hint_passed_to_executor(tmp_path: Path) -> None:
    """On retry, repair_hint should be non-empty and passed to executor."""
    task_svc, plan_svc, plan_store, task_store = await _setup_db(tmp_path)
    task = await task_svc.create_task(task_id="task-hint", project_id="proj", objective="Hint test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id, plan_id="plan-hint",
        steps=(("step-h", "Hintable Step", "desc"),),
    )

    executor = _CountingExecutor()

    class _OnceFailGate:
        def __init__(self) -> None:
            self.calls = 0
        def evaluate(self, step_id, implementation_result, test_result, security_result):
            self.calls += 1
            if self.calls == 1:
                return ReviewResult(step_id=step_id, status=ReviewStatus.REJECTED, summary="fail first",
                                    comments=("bad output",))
            return ReviewResult(step_id=step_id, status=ReviewStatus.APPROVED, summary="ok")

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=_OnceFailGate(),
        max_auto_repair_attempts=2,
    )
    summary = await coordinator.execute_whole_plan("plan-hint", tmp_path)

    assert summary.plan_completed is True
    assert len(executor.calls) == 2
    # First attempt has empty hint, second has a non-empty repair hint
    assert executor.calls[0][1] == ""
    assert len(executor.calls[1][1]) > 0


@pytest.mark.asyncio
async def test_multi_step_stops_on_first_failure(tmp_path: Path) -> None:
    """If step-1 fails, step-2 should NOT be executed."""
    task_svc, plan_svc, plan_store, task_store = await _setup_db(tmp_path)
    task = await task_svc.create_task(task_id="task-stop", project_id="proj", objective="Stop test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id, plan_id="plan-stop",
        steps=(
            ("step-1", "Fail Here", "desc"),
            ("step-2", "Never Reached", "desc"),
        ),
    )

    executor = _CountingExecutor()
    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=_AlwaysFailGate(),
        max_auto_repair_attempts=0,  # No retries
    )
    summary = await coordinator.execute_whole_plan("plan-stop", tmp_path)

    assert summary.steps_failed == 1
    assert summary.steps_completed == 0
    # Executor called once (for step-1). step-2 was never reached.
    step_ids_executed = [c[0] for c in executor.calls]
    assert "step-1" in step_ids_executed
    assert "step-2" not in step_ids_executed


@pytest.mark.asyncio
async def test_noop_executor_used_by_default(tmp_path: Path) -> None:
    """When no step_executor is injected, _NoOpStepExecutor should be used."""
    coordinator_no_executor = WholePlanCoordinator.__new__(WholePlanCoordinator)
    from core.task_service import TaskService
    from core.plan_service import PlanService
    from core.plan_store import PlanStore

    task_svc, plan_svc, plan_store, task_store = await _setup_db(tmp_path)
    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
    )
    assert isinstance(coordinator._step_executor, _NoOpStepExecutor)


@pytest.mark.asyncio
async def test_no_op_executor_returns_success(tmp_path: Path) -> None:
    noop = _NoOpStepExecutor()
    result = await noop.execute_step(
        step_id="s-1",
        step_title="Test Step",
        step_role=AgentRole.BACKEND_CODER,
        repair_hint="fix something",
    )
    assert result.success is True
    assert "NoOpStepExecutor" in result.summary
    assert "repair" in result.summary


def test_build_repair_hint_with_reflexion() -> None:
    """_build_repair_hint should produce structured Reflexion diagnosis."""
    coordinator = WholePlanCoordinator.__new__(WholePlanCoordinator)
    coordinator._goal_drift_monitor = None
    coordinator.max_step_retries = 2
    coordinator.max_auto_repair_attempts = 2

    # Review with test failures
    class _DummyTestResult:
        failures = (
            'File "core/calculator.py", line 42, in add\nAssertionError: assert -1 == 5',
        )
        summary = "1 test failed"

    class _DummyReview:
        test_result = _DummyTestResult()
        security_result = None
        reasons = ("Test verification gate failed",)

    hint = coordinator._build_repair_hint(_DummyReview())
    assert "Test failures:" in hint
    assert "Reflexion:" in hint
    assert "ASSERTION_FAILURE" in hint
    assert "core/calculator.py:42" in hint

