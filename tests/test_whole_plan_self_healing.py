from __future__ import annotations

"""Comprehensive tests for Autonomous Closed-Loop Self-Healing & Verification Gate:
- Strict 3-attempt bound (Attempt 0 initial + Attempts 1, 2, 3 repairs = max 4 attempts)
- Upstream pattern alignment (Aider / SWE-agent / MetaGPT Reflexion protocol)
- Pre-step snapshotting and atomic rollback on repair exhaustion
- Reflexion memory and anti-looping protections across repair attempts
- Goal drift detection and guardrails during repair
- Surgical AST context packaging integration
- DecisionService escalation on exhaustion
"""

from pathlib import Path

import pytest

from core.agent_role import AgentRole
from core.decision import DecisionSeverity
from core.decision_service import DecisionService
from core.handoff_contracts import (
    ImplementationResult,
    ReviewResult,
    ReviewStatus,
)
from core.plan import PlanStepState
from core.plan_service import PlanService
from core.task_service import TaskService
from core.whole_plan_coordinator import (
    WholePlanCoordinator,
)
from persistence.sqlite_decision_store import SQLiteDecisionStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore

# ── Helpers & Test Stubs ────────────────────────────────────────────────────────


async def _setup_workspace_env(tmp_path: Path):
    db = tmp_path / "metadata.db"
    project_store = SQLiteProjectStore(db)
    task_store = SQLiteTaskStore(db)
    plan_store = SQLitePlanStore(db)
    decision_store = SQLiteDecisionStore(db)

    await project_store.initialize()
    await task_store.initialize()
    await plan_store.initialize()
    await decision_store.initialize()

    from core.project import ProjectRecord

    await project_store.create(ProjectRecord(project_id="proj-self-healing", name="P-SH", workspace_path=str(tmp_path)))
    task_svc = TaskService(project_store=project_store, task_store=task_store)
    plan_svc = PlanService(task_store=task_store, plan_store=plan_store)
    decision_svc = DecisionService(decision_store)

    # Provide valid test runner baseline in tmp_path
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    test_dir = tmp_path / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_baseline.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    return task_svc, plan_svc, plan_store, task_store, decision_svc


class _MockMutatingExecutor:
    """Simulates an agent modifying existing files and creating dirty temporary files."""

    def __init__(self, workspace_path: Path, repair_success_on_attempt: int | None = None) -> None:
        self.workspace_path = workspace_path
        self.repair_success_on_attempt = repair_success_on_attempt
        self.call_count = 0
        self.hints_received: list[str] = []

    async def execute_step(
        self,
        step_id: str,
        step_title: str,
        step_role: AgentRole,
        repair_hint: str = "",
        step_instruction: str = "",
    ) -> ImplementationResult:
        self.hints_received.append(repair_hint)
        current_attempt = self.call_count
        self.call_count += 1

        # Simulate code mutation
        target_file = self.workspace_path / "calculator.py"
        dirty_file = self.workspace_path / f"temp_garbage_attempt_{current_attempt}.tmp"

        # Dirty the workspace
        dirty_file.write_text(f"temporary file from attempt {current_attempt}\n", encoding="utf-8")

        if self.repair_success_on_attempt is not None and current_attempt == self.repair_success_on_attempt:
            # Correct fix applied
            target_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
            return ImplementationResult(
                step_id=step_id,
                summary=f"Successfully fixed add function on attempt {current_attempt}",
                success=True,
                is_mocked=False,
                execution_evidence={"is_real": True, "attempt": current_attempt},
                modified_files=("calculator.py",),
                created_files=(dirty_file.name,),
            )

        # Broken edit applied
        target_file.write_text(f"def add(a: int, b: int) -> int:\n    return -{current_attempt}\n", encoding="utf-8")
        return ImplementationResult(
            step_id=step_id,
            summary=f"Applied broken edit on attempt {current_attempt}",
            success=True,
            is_mocked=False,
            execution_evidence={"is_real": True, "attempt": current_attempt},
            modified_files=("calculator.py",),
            created_files=(dirty_file.name,),
        )


class _StepGateEvaluator:
    """Evaluates whether the workspace satisfies the test condition."""

    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = workspace_path
        self.eval_count = 0

    def evaluate(self, step_id, implementation_result, test_result, security_result, require_real_execution=True):
        self.eval_count += 1
        target_file = self.workspace_path / "calculator.py"
        content = target_file.read_text(encoding="utf-8") if target_file.exists() else ""

        if "return a + b" in content:
            return ReviewResult(
                step_id=step_id,
                status=ReviewStatus.APPROVED,
                summary="All tests passed successfully: add(2, 3) == 5",
            )

        # Fails with structured diagnostic
        fail_msg = (
            'File "calculator.py", line 2, in add\n'
            f"AssertionError: assert {content.strip().split()[-1] if content.strip() else 'None'} == 5"
        )
        return ReviewResult(
            step_id=step_id,
            status=ReviewStatus.REJECTED,
            summary="Verification rejected: addition test failed",
            comments=(fail_msg,),
            required_repairs=(f"Test failure: {fail_msg}",),
        )


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bounded_3_attempt_repair_exhaustion_and_atomic_rollback(tmp_path: Path) -> None:
    """When a step fails all 3 repair retries, the workspace is atomically rolled back to pre-step baseline."""
    task_svc, plan_svc, plan_store, _task_store, decision_svc = await _setup_workspace_env(tmp_path)

    # 1. Establish pre-step baseline file
    baseline_code = "# Baseline implementation\ndef add(a: int, b: int) -> int:\n    return 0\n"
    calc_file = tmp_path / "calculator.py"
    calc_file.write_text(baseline_code, encoding="utf-8")

    task = await task_svc.create_task(task_id="task-sh-1", project_id="proj-self-healing", objective="Test self-healing")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-sh-1",
        steps=(("step-sh-1", "Fix add function", "Make add(a, b) return a + b"),),
    )

    # Executor never fixes the code -> exhausts attempt 0 + 3 repairs = 4 total attempts
    executor = _MockMutatingExecutor(tmp_path, repair_success_on_attempt=None)
    gate = _StepGateEvaluator(tmp_path)

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=gate,
        decision_service=decision_svc,
        max_auto_repair_attempts=3,
    )

    summary = await coordinator.execute_whole_plan("plan-sh-1", tmp_path)

    # Assertions on plan completion & failure
    assert summary.plan_completed is False
    assert summary.steps_completed == 0
    assert summary.steps_failed == 1
    assert summary.steps_auto_repaired == 0
    assert summary.rollbacks_executed == 1

    # Gate and Executor were called 4 times (1 initial attempt + 3 repair retries)
    assert executor.call_count == 4
    assert gate.eval_count == 4
    assert len(summary.review_history) == 4

    # Assertions on atomic rollback to pre-step baseline:
    # 1. calculator.py must be restored to its EXACT original baseline code
    assert calc_file.exists()
    assert calc_file.read_text(encoding="utf-8") == baseline_code

    # 2. All dirty temporary files created during any of the 4 attempts must be deleted
    for i in range(4):
        dirty_tmp = tmp_path / f"temp_garbage_attempt_{i}.tmp"
        assert not dirty_tmp.exists(), f"Temporary file {dirty_tmp.name} was not cleaned up during rollback!"

    # Assertions on telemetry
    telemetry = summary.step_telemetry[0]
    assert telemetry["step_id"] == "step-sh-1"
    assert telemetry["outcome"] == "failed"
    assert telemetry["attempts"] == 4
    assert telemetry["repair_exhausted"] is True
    assert telemetry["rollback_executed"] is True
    assert "calculator.py" in telemetry["rollback_report"]["reverted_files"]

    # Assertion on DecisionService escalation
    open_decisions = await decision_svc.list_open_decisions(task_id=task.task_id)
    assert len(open_decisions) == 1
    dec = open_decisions[0]
    assert dec.severity == DecisionSeverity.HIGH
    assert "failed verification after 3 automated repair attempts" in dec.prompt
    assert len(dec.options) == 3


@pytest.mark.asyncio
async def test_self_healing_succeeds_on_repair_attempt_2(tmp_path: Path) -> None:
    """When a step fails initially but succeeds on Repair Attempt 2, changes are committed and kept."""
    task_svc, plan_svc, plan_store, _task_store, _ = await _setup_workspace_env(tmp_path)

    baseline_code = "# Baseline implementation\ndef add(a: int, b: int) -> int:\n    return 0\n"
    calc_file = tmp_path / "calculator.py"
    calc_file.write_text(baseline_code, encoding="utf-8")

    task = await task_svc.create_task(task_id="task-sh-2", project_id="proj-self-healing", objective="Test repair success")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-sh-2",
        steps=(("step-sh-2", "Fix add function", "Make add(a, b) return a + b"),),
    )

    # Attempt 0: fails, Attempt 1: fails, Attempt 2: succeeds!
    executor = _MockMutatingExecutor(tmp_path, repair_success_on_attempt=2)
    gate = _StepGateEvaluator(tmp_path)

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=gate,
        max_auto_repair_attempts=3,
    )

    summary = await coordinator.execute_whole_plan("plan-sh-2", tmp_path)

    # Assertions on plan completion & auto-repair count
    assert summary.plan_completed is True
    assert summary.steps_completed == 1
    assert summary.steps_failed == 0
    assert summary.steps_auto_repaired == 1
    assert summary.rollbacks_executed == 0

    # Total attempts = 3 (Attempt 0 initial, Attempt 1 repair 1, Attempt 2 repair 2)
    assert executor.call_count == 3
    assert gate.eval_count == 3

    # Verified changes MUST be preserved
    assert calc_file.exists()
    assert "return a + b" in calc_file.read_text(encoding="utf-8")

    # Step in store is COMPLETED
    steps = await plan_store.list_steps("plan-sh-2")
    assert steps[0].state == PlanStepState.COMPLETED

    # Telemetry
    telemetry = summary.step_telemetry[0]
    assert telemetry["outcome"] == "approved"
    assert telemetry["auto_repaired"] is True
    assert telemetry["attempts"] == 2


@pytest.mark.asyncio
async def test_reflexion_memory_and_anti_looping_advisory(tmp_path: Path) -> None:
    """Successive repair attempts receive Reflexion Memory with explicit anti-looping warnings."""
    task_svc, plan_svc, plan_store, _task_store, _ = await _setup_workspace_env(tmp_path)

    calc_file = tmp_path / "calculator.py"
    calc_file.write_text("def add(a, b): return 0\n", encoding="utf-8")

    task = await task_svc.create_task(task_id="task-sh-3", project_id="proj-self-healing", objective="Reflexion test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-sh-3",
        steps=(("step-sh-3", "Test Reflexion hints", "desc"),),
    )

    executor = _MockMutatingExecutor(tmp_path, repair_success_on_attempt=None)
    gate = _StepGateEvaluator(tmp_path)

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=gate,
        max_auto_repair_attempts=3,
    )

    await coordinator.execute_whole_plan("plan-sh-3", tmp_path)

    hints = executor.hints_received
    assert len(hints) == 4

    # Attempt 0 (initial): repair_hint is empty
    assert hints[0] == ""

    # Attempt 1 (first repair): contains repair attempt 1/3 and diagnosis
    assert "[AUTONOMOUS REPAIR ATTEMPT 1/3]" in hints[1]
    assert "ASSERTION_FAILURE" in hints[1]
    assert "calculator.py:2" in hints[1]

    # Attempt 2 (second repair): contains attempt 2/3, AND anti-looping section referencing Attempt #1
    assert "[AUTONOMOUS REPAIR ATTEMPT 2/3]" in hints[2]
    assert "[Reflexion Memory & Anti-Looping Protection]" in hints[2]
    assert "Attempt #1:" in hints[2]
    assert "Do NOT repeat the identical edits" in hints[2]

    # Attempt 3 (third repair): contains attempt 3/3, AND anti-looping section referencing Attempts #1 and #2
    assert "[AUTONOMOUS REPAIR ATTEMPT 3/3]" in hints[3]
    assert "Attempt #1:" in hints[3]
    assert "Attempt #2:" in hints[3]


@pytest.mark.asyncio
async def test_goal_drift_detection_and_rollback_during_repair(tmp_path: Path) -> None:
    """When an agent causes goal drift on out-of-scope files during repair, it is rejected and rolled back."""
    task_svc, plan_svc, plan_store, _task_store, _ = await _setup_workspace_env(tmp_path)

    app_file = tmp_path / "app.py"
    app_file.write_text("print('hello')\n", encoding="utf-8")

    task = await task_svc.create_task(task_id="task-drift", project_id="proj-self-healing", objective="Drift test")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-drift",
        steps=(("step-drift", "Only modify app.py", "allowed app.py only"),),
    )

    class _DriftingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute_step(self, step_id, step_title, step_role, repair_hint="", step_instruction=""):
            self.calls += 1
            # Tamper with prohibited file outside scope
            secret_file = tmp_path / "secret.env"
            secret_file.write_text("LEAKED=TRUE\n", encoding="utf-8")
            return ImplementationResult(
                step_id=step_id,
                summary="Drifting action",
                success=True,
                is_mocked=False,
                execution_evidence={"is_real": True},
                modified_files=("secret.env",),
            )

    class _PassGateIfNoDrift:
        def evaluate(self, step_id, implementation_result, test_result, security_result, require_real_execution=True):
            if "DRIFT:" in implementation_result.summary or not implementation_result.success:
                return ReviewResult(
                    step_id=step_id,
                    status=ReviewStatus.REJECTED,
                    summary="Drift detected: out-of-scope modification",
                    comments=("Prohibited file modified",),
                    required_repairs=("Revert changes to secret.env",),
                )
            return ReviewResult(step_id=step_id, status=ReviewStatus.APPROVED, summary="ok")

    executor = _DriftingExecutor()
    gate = _PassGateIfNoDrift()

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=gate,
        max_auto_repair_attempts=2,
    )

    summary = await coordinator.execute_whole_plan("plan-drift", tmp_path)

    assert summary.plan_completed is False
    assert summary.steps_failed == 1
    assert summary.rollbacks_executed == 1

    # Verify secret.env was rolled back / deleted!
    assert not (tmp_path / "secret.env").exists()
    assert app_file.read_text(encoding="utf-8") == "print('hello')\n"


@pytest.mark.asyncio
async def test_surgical_ast_context_packaging_in_repair_hint(tmp_path: Path) -> None:
    """When a Python target file is diagnosed, surgical AST context with SensitiveDataGate is embedded in hint."""
    code = (
        "def compute_total(items: list[int]) -> int:\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item\n"
        "    return -1  # BUG\n"
    )
    src_file = tmp_path / "service.py"
    src_file.write_text(code, encoding="utf-8")

    from core.error_reflexion import ErrorReflexionEngine

    failure_text = 'File "service.py", line 5, in compute_total\nAssertionError: assert -1 == 10'
    diagnosis = ErrorReflexionEngine.diagnose_test_failure(failure_text)

    coordinator = WholePlanCoordinator.__new__(WholePlanCoordinator)
    hint = coordinator._build_reflexion_repair_hint(
        review=None,
        attempt=1,
        max_attempts=3,
        diagnoses=[diagnosis],
        workspace_root=tmp_path,
    )

    assert "[AUTONOMOUS REPAIR ATTEMPT 1/3]" in hint
    assert "ASSERTION_FAILURE" in hint
    assert "service.py:5" in hint
    assert "[SURGICAL CODE CONTEXT]" in hint
    assert "service.py" in hint
    assert "compute_total" in hint
