from __future__ import annotations

from pathlib import Path
import tempfile
import pytest

from core.agent_role import AgentRole
from core.agent_handoff_context import AgentHandoffContextBuilder
from core.agent_run_service import AgentRunService
from core.agent_work_result import (
    AgentFinding,
    AgentWorkResult,
    CommandExecution,
    FileChange,
    TestExecutionResult,
)
from persistence.sqlite_agent_run_store import SQLiteAgentRunStore


@pytest.mark.asyncio
async def test_e2e_antigravity_to_codex_to_reviewer_durable_handoff():
    """Simulates real-world Cross-Agent execution continuity without relying on conversation text:

    Antigravity (Backend Coder)
          ↓ (Persists AgentRun #1 & AgentWorkResult in SQLite)
    Control Plane / SQLiteAgentRunStore
          ↓ (Constructs AgentHandoffContext)
    Codex (Tester / Security)
          ↓ (Inherits exact files, tests, findings without user copy-pasting)
          ↓ (Persists AgentRun #2 & AgentWorkResult)
    Reviewer Agent
          ↓ (Receives full unified durable ledger context from both agents)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "e2e_handoff.db"
        store = SQLiteAgentRunStore(db_path)
        await store.initialize()

        run_service = AgentRunService(store)
        context_builder = AgentHandoffContextBuilder(store)

        task_id = "task-auth-service"
        plan_id = "plan-auth-1"
        step_1 = "step-1-backend"
        step_2 = "step-2-security-test"
        step_3 = "step-3-review"

        # ─── 1. ANTIGRAVITY EXECUTES STEP 1 (BACKEND CODER) ───────────────────
        run_anti = await run_service.create_run(
            project_id="auth-platform",
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_1,
            agent_id="antigravity",
            execution_role=AgentRole.BACKEND_CODER,
            model_id="gemini-3.7-flash",
            runtime_id="antigravity",
        )
        await run_service.start_run(run_anti.run_id)

        anti_result = AgentWorkResult(
            run_id=run_anti.run_id,
            status="completed",
            summary="Implemented JWT authentication handler and password hashing",
            changed_files=(
                FileChange(path="src/auth.ts", change_type="created", diff_summary="+120 lines"),
                FileChange(path="src/user.ts", change_type="modified", diff_summary="+30 lines"),
            ),
            created_files=("src/auth.ts",),
            commands_run=(
                CommandExecution(command="npm run build", exit_code=0),
            ),
            tests=(
                TestExecutionResult(
                    command="npm test src/auth.test.ts",
                    framework="jest",
                    status="passed",
                    passed=5,
                    failed=0,
                    summary="5 unit tests passed",
                ),
            ),
            findings=(
                AgentFinding(
                    finding_id="warn-rate-limit",
                    kind="security",
                    severity="medium",
                    title="Missing Rate Limiter",
                    summary="Login endpoint is vulnerable to brute force without rate limiting middleware",
                ),
            ),
            remaining_work=("Add rate limiting middleware in security test step",),
            handoff_notes="JWT secret is read from env variable JWT_SECRET",
        )

        await run_service.complete_run(run_anti.run_id, anti_result)

        # ─── 2. CODEX STARTS STEP 2 (TESTER / SECURITY) ──────────────────────
        # Codex requests handoff context from MyAgent
        codex_handoff_ctx = await context_builder.build_context(
            task_id=task_id,
            target_role=AgentRole.TESTER,
            plan_id=plan_id,
            step_id=step_2,
        )

        # VERIFY: Codex immediately receives Antigravity's work without user copy-pasting
        assert len(codex_handoff_ctx.prior_runs) == 1
        assert codex_handoff_ctx.latest_summary == "Implemented JWT authentication handler and password hashing"
        assert "src/auth.ts" in codex_handoff_ctx.created_files
        assert "src/user.ts" in codex_handoff_ctx.changed_files
        assert len(codex_handoff_ctx.recent_tests) == 1
        assert codex_handoff_ctx.recent_tests[0].passed == 5
        assert len(codex_handoff_ctx.open_findings) == 1
        assert codex_handoff_ctx.open_findings[0].title == "Missing Rate Limiter"

        # Codex starts and executes Step 2
        run_codex = await run_service.create_run(
            project_id="auth-platform",
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_2,
            agent_id="codex",
            execution_role=AgentRole.TESTER,
            model_id="gpt-4o",
            runtime_id="codex",
        )
        await run_service.start_run(run_codex.run_id)

        codex_result = AgentWorkResult(
            run_id=run_codex.run_id,
            status="completed",
            summary="Added IP-based rate limiter and security brute-force integration tests",
            changed_files=(
                FileChange(path="src/rate_limiter.ts", change_type="created"),
                FileChange(path="src/auth.ts", change_type="modified", diff_summary="Attached rate limiter"),
            ),
            created_files=("src/rate_limiter.ts", "tests/security_auth.test.ts"),
            tests=(
                TestExecutionResult(
                    command="npm test tests/security_auth.test.ts",
                    framework="jest",
                    status="passed",
                    passed=4,
                    failed=0,
                    summary="4 security tests passed",
                ),
            ),
            remaining_work=(),
            handoff_notes="Rate limiting configured to 5 attempts per minute per IP",
        )
        await run_service.complete_run(run_codex.run_id, codex_result)

        # ─── 3. REVIEWER AGENT STARTS STEP 3 (FINAL REVIEW) ──────────────────
        reviewer_handoff_ctx = await context_builder.build_context(
            task_id=task_id,
            target_role=AgentRole.REVIEWER,
            plan_id=plan_id,
            step_id=step_3,
        )

        # VERIFY: Reviewer agent receives unified cumulative history from both Antigravity & Codex
        assert len(reviewer_handoff_ctx.prior_runs) == 2
        assert "src/auth.ts" in reviewer_handoff_ctx.changed_files
        assert "src/rate_limiter.ts" in reviewer_handoff_ctx.created_files
        assert len(reviewer_handoff_ctx.recent_tests) == 2
        total_passed = sum(t.passed for t in reviewer_handoff_ctx.recent_tests)
        assert total_passed == 9  # 5 from Antigravity + 4 from Codex
