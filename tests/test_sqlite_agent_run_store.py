from __future__ import annotations

from pathlib import Path
import tempfile
import pytest

from core.agent_role import AgentRole
from core.agent_run import AgentRunRecord, AgentRunState
from core.agent_work_result import AgentWorkResult, FileChange, TestExecutionResult
from persistence.sqlite_agent_run_store import SQLiteAgentRunStore


@pytest.mark.asyncio
async def test_sqlite_agent_run_store_lifecycle_and_queries():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_runs.db"
        store = SQLiteAgentRunStore(db_path)
        await store.initialize()

        run1 = AgentRunRecord(
            run_id="run-step1-1",
            project_id="payment-proj",
            task_id="task-100",
            plan_id="plan-1",
            step_id="step-1",
            agent_id="antigravity",
            execution_role=AgentRole.BACKEND_CODER,
            model_id="gemini-3.7-flash",
            runtime_id="antigravity",
            session_id="session-1",
            state=AgentRunState.COMPLETED,
            started_at="2026-08-28T10:00:00Z",
            finished_at="2026-08-28T10:05:00Z",
            result=AgentWorkResult(
                run_id="run-step1-1",
                status="completed",
                summary="Implemented webhook endpoint",
                changed_files=(FileChange(path="src/webhook.ts", change_type="created"),),
                tests=(TestExecutionResult(passed=3, failed=0, summary="3 tests passed"),),
            ),
        )

        run2 = AgentRunRecord(
            run_id="run-step1-2",
            project_id="payment-proj",
            task_id="task-100",
            plan_id="plan-1",
            step_id="step-1",
            agent_id="codex",
            execution_role=AgentRole.TESTER,
            model_id="gpt-4o",
            runtime_id="codex",
            session_id="session-2",
            state=AgentRunState.COMPLETED,
            started_at="2026-08-28T10:06:00Z",
            finished_at="2026-08-28T10:08:00Z",
            result=AgentWorkResult(
                run_id="run-step1-2",
                status="completed",
                summary="Added e2e test for webhook replay",
                tests=(TestExecutionResult(passed=5, failed=0, summary="5 tests passed"),),
            ),
        )

        await store.save(run1)
        await store.save(run2)

        # Get by id
        fetched = await store.get("run-step1-1")
        assert fetched is not None
        assert fetched.agent_id == "antigravity"
        assert fetched.result is not None
        assert fetched.result.summary == "Implemented webhook endpoint"
        assert fetched.result.tests[0].passed == 3

        # List for step
        step_runs = await store.list_for_step("step-1")
        assert len(step_runs) == 2
        assert step_runs[0].run_id == "run-step1-1"
        assert step_runs[1].run_id == "run-step1-2"

        # Latest completed for step
        latest_completed = await store.get_latest_completed_for_step("step-1")
        assert latest_completed is not None
        assert latest_completed.run_id == "run-step1-2"

        # List for task
        task_runs = await store.list_for_task("task-100")
        assert len(task_runs) == 2

        # Persistence across store reopen (simulate restart)
        reopened_store = SQLiteAgentRunStore(db_path)
        await reopened_store.initialize()
        reopened_run = await reopened_store.get("run-step1-2")
        assert reopened_run is not None
        assert reopened_run.agent_id == "codex"
        assert reopened_run.result is not None
        assert reopened_run.result.summary == "Added e2e test for webhook replay"
