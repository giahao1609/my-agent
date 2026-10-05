from __future__ import annotations

import pytest

from core.agent_role import AgentRole
from core.agent_run import AgentRunRecord, AgentRunState
from core.agent_work_result import AgentWorkResult, FileChange


def test_agent_run_record_validation():
    # Empty run_id
    with pytest.raises(ValueError, match="run_id must not be empty"):
        AgentRunRecord(
            run_id="",
            project_id="proj-1",
            task_id="task-1",
            agent_id="agent-1",
            execution_role=AgentRole.BACKEND_CODER,
        )

    # Valid creation
    run = AgentRunRecord(
        run_id="run-1",
        project_id="proj-1",
        task_id="task-1",
        plan_id="plan-1",
        step_id="step-1",
        agent_id="antigravity-coder",
        execution_role=AgentRole.BACKEND_CODER,
    )
    assert run.state == AgentRunState.CREATED
    assert not run.is_terminal


def test_agent_run_lifecycle_transitions():
    run = AgentRunRecord(
        run_id="run-10",
        project_id="proj-1",
        task_id="task-1",
        step_id="step-1",
        agent_id="antigravity-ui",
        execution_role=AgentRole.UI_CODER,
    )

    # CREATED -> RUNNING
    running = run.mark_running(session_id="session-abc", model_id="gemini-3.7-flash")
    assert running.state == AgentRunState.RUNNING
    assert running.session_id == "session-abc"
    assert running.model_id == "gemini-3.7-flash"
    assert running.started_at is not None

    # RUNNING -> COMPLETED
    work_res = AgentWorkResult(
        run_id="run-10",
        status="completed",
        summary="Designed UI Components",
        changed_files=(FileChange(path="src/App.tsx", change_type="modified"),),
    )
    completed = running.complete(work_res)
    assert completed.state == AgentRunState.COMPLETED
    assert completed.is_terminal
    assert completed.finished_at is not None
    assert completed.result is not None
    assert len(completed.result.changed_files) == 1

    # Terminal run cannot transition back to RUNNING or complete again
    with pytest.raises(ValueError, match="cannot transition terminal run"):
        completed.mark_running()

    with pytest.raises(ValueError, match="cannot complete already terminal run"):
        completed.complete(work_res)


def test_agent_run_failure_and_cancellation():
    run = AgentRunRecord(
        run_id="run-fail-1",
        project_id="proj-1",
        task_id="task-1",
        agent_id="tester-agent",
        execution_role=AgentRole.TESTER,
    )
    running = run.mark_running()

    # Fail
    failed = running.fail(reason="Syntax error in test file")
    assert failed.state == AgentRunState.FAILED
    assert failed.is_terminal
    assert failed.metadata.get("failure_reason") == "Syntax error in test file"
    assert failed.result is not None
    assert failed.result.status == "failed"

    # Cancel
    run2 = AgentRunRecord(
        run_id="run-cancel-1",
        project_id="proj-1",
        task_id="task-1",
        agent_id="researcher-agent",
        execution_role=AgentRole.RESEARCHER,
    )
    cancelled = run2.cancel(reason="User cancelled task")
    assert cancelled.state == AgentRunState.CANCELLED
    assert cancelled.is_terminal
    assert cancelled.metadata.get("cancel_reason") == "User cancelled task"


def test_multiple_agent_runs_per_step_with_different_identities():
    # Step 1 can have Run 1 (Antigravity UI), Run 2 (Codex Backend), Run 3 (Tester)
    run1 = AgentRunRecord(
        run_id="run-step1-anti",
        project_id="proj-1",
        task_id="task-1",
        step_id="step-1",
        agent_id="antigravity",
        execution_role=AgentRole.UI_CODER,
        model_id="gemini-3.7-flash",
    )
    run2 = AgentRunRecord(
        run_id="run-step1-codex",
        project_id="proj-1",
        task_id="task-1",
        step_id="step-1",
        agent_id="codex",
        execution_role=AgentRole.BACKEND_CODER,
        model_id="gpt-4o",
    )

    assert run1.step_id == run2.step_id
    assert run1.agent_id != run2.agent_id
    assert run1.execution_role != run2.execution_role
    assert run1.model_id != run2.model_id
