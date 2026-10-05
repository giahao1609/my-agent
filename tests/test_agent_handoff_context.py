from __future__ import annotations

from pathlib import Path
import tempfile
import pytest

from core.agent_role import AgentRole
from core.agent_handoff_context import AgentHandoffContextBuilder
from core.agent_run import AgentRunRecord, AgentRunState
from core.agent_work_result import AgentFinding, AgentWorkResult, FileChange, TestExecutionResult
from persistence.sqlite_agent_run_store import SQLiteAgentRunStore


@pytest.mark.asyncio
async def test_agent_handoff_context_builder_selective_prioritization():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "handoff_test.db"
        store = SQLiteAgentRunStore(db_path)
        await store.initialize()

        # Step 1 Run (Antigravity)
        run_step1 = AgentRunRecord(
            run_id="run-step1-anti",
            project_id="proj-1",
            task_id="task-10",
            plan_id="plan-1",
            step_id="step-1",
            agent_id="antigravity",
            execution_role=AgentRole.BACKEND_CODER,
            state=AgentRunState.COMPLETED,
            created_at="2026-08-28T10:00:00Z",
            result=AgentWorkResult(
                run_id="run-step1-anti",
                status="completed",
                summary="Implemented Core API endpoints",
                changed_files=(FileChange(path="src/api.ts", change_type="modified"),),
                created_files=("src/api_routes.ts",),
                tests=(TestExecutionResult(passed=8, failed=0, summary="8 unit tests passed"),),
                findings=(
                    AgentFinding(
                        finding_id="f1",
                        kind="performance",
                        severity="low",
                        title="Missing caching on GET /routes",
                        summary="Response time 150ms can be improved with redis",
                    ),
                ),
                remaining_work=("Add redis cache middleware",),
                handoff_notes="Routes are fully mounted in Express app",
            ),
        )

        # Unrelated Task Run (Should be excluded)
        run_unrelated = AgentRunRecord(
            run_id="run-other-task",
            project_id="proj-1",
            task_id="task-999",
            agent_id="other-agent",
            execution_role=AgentRole.BACKEND_CODER,
            state=AgentRunState.COMPLETED,
            created_at="2026-08-28T09:00:00Z",
            result=AgentWorkResult(
                run_id="run-other-task",
                status="completed",
                summary="Unrelated work",
            ),
        )

        await store.save(run_step1)
        await store.save(run_unrelated)

        builder = AgentHandoffContextBuilder(store)
        ctx = await builder.build_context(
            task_id="task-10",
            target_role=AgentRole.TESTER,
            plan_id="plan-1",
            step_id="step-1",
        )

        assert ctx.task_id == "task-10"
        assert ctx.target_role == AgentRole.TESTER
        assert len(ctx.prior_runs) == 1
        assert ctx.prior_runs[0].run_id == "run-step1-anti"

        # Changed files and tests propagated
        assert "src/api.ts" in ctx.changed_files
        assert "src/api_routes.ts" in ctx.created_files
        assert len(ctx.recent_tests) == 1
        assert ctx.recent_tests[0].passed == 8
        assert len(ctx.open_findings) == 1
        assert "Add redis cache middleware" in ctx.remaining_work
        assert ctx.latest_summary == "Implemented Core API endpoints"

        # Format prompt brief
        brief = ctx.format_prompt_context()
        assert "Ngữ Cảnh Bàn Giao Từ Lượt Thực Thi Trước" in brief
        assert "src/api.ts" in brief
        assert "8 unit tests passed" in brief
        assert "Add redis cache middleware" in brief
