from __future__ import annotations

from pathlib import Path
import tempfile
import pytest

from core.agent_role import AgentRole
from core.agent_run import AgentRunState
from core.agent_run_service import AgentRunService
from core.agent_work_result import AgentWorkResult
from persistence.sqlite_agent_run_store import SQLiteAgentRunStore


@pytest.mark.asyncio
async def test_agent_run_service_lifecycle_and_idempotency():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "service_test.db"
        store = SQLiteAgentRunStore(db_path)
        await store.initialize()
        service = AgentRunService(store)

        # 1. Create run
        run = await service.create_run(
            project_id="proj-1",
            task_id="task-1",
            agent_id="antigravity",
            execution_role=AgentRole.BACKEND_CODER,
            plan_id="plan-1",
            step_id="step-1",
        )
        assert run.state == AgentRunState.CREATED

        # 2. Start run
        started = await service.start_run(
            run.run_id,
            session_id="session-xyz",
            runtime_id="antigravity",
            model_id="gemini-3.7-flash",
        )
        assert started.state == AgentRunState.RUNNING
        assert started.session_id == "session-xyz"

        # 3. Complete run
        res = AgentWorkResult(
            run_id=run.run_id,
            status="completed",
            summary="Refactored database queries",
        )
        completed = await service.complete_run(run.run_id, res)
        assert completed.state == AgentRunState.COMPLETED
        assert completed.result is not None

        # 4. Duplicate completion idempotency (same summary)
        duplicate = await service.complete_run(run.run_id, res)
        assert duplicate.run_id == completed.run_id
        assert duplicate.state == AgentRunState.COMPLETED

        # 5. Conflicting completion on terminal run fails
        conflicting_res = AgentWorkResult(
            run_id=run.run_id,
            status="completed",
            summary="Completely different conflicting work",
        )
        with pytest.raises(ValueError, match="cannot complete already terminal"):
            await service.complete_run(run.run_id, conflicting_res)
