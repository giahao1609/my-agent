from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from core.control_plane import MyAgentControlPlane
from core.decision import DecisionOption, DecisionSeverity
from core.decision_service import DecisionService
from core.front_agent import FrontAgentRequest
from core.handoff_contracts import DecisionOptionDraft, DecisionRequiredResult
from core.plan_service import PlanService
from core.specialist_execution import SpecialistExecutionResult
from core.task_service import TaskService
from persistence.sqlite_decision_store import SQLiteDecisionStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore
from core.project import ProjectRecord


@pytest.mark.asyncio
async def test_control_plane_task_and_decision_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "control_plane.db"
        proj_store = SQLiteProjectStore(db_path)
        task_store = SQLiteTaskStore(db_path)
        plan_store = SQLitePlanStore(db_path)
        dec_store = SQLiteDecisionStore(db_path)

        await proj_store.initialize()
        await task_store.initialize()
        await plan_store.initialize()
        await dec_store.initialize()

        project = ProjectRecord(
            project_id="proj-10",
            name="Test Project",
            workspace_path=str(tmpdir),
        )
        await proj_store.create(project)
        await proj_store.set_active("proj-10")


        task_service = TaskService(project_store=proj_store, task_store=task_store)
        plan_service = PlanService(plan_store=plan_store, task_store=task_store)
        dec_service = DecisionService(decision_store=dec_store)

        control_plane = MyAgentControlPlane(
            task_store=task_store,
            task_service=task_service,
            plan_store=plan_store,
            plan_service=plan_service,
            decision_service=dec_service,
        )

        # 1. Process initial user intent
        req = FrontAgentRequest(
            user_input="Build Auth Module",
            project_id="proj-10",
        )
        msg, progress, open_dec, summary = await control_plane.process_user_intent(req)
        assert progress is not None
        assert progress.objective == "Build Auth Module"
        assert open_dec is None

        task_id = progress.task_id

        # 2. Specialist pauses execution with DecisionRequiredResult
        opt1 = DecisionOptionDraft(option_id="opt-session", title="Session Auth", description="Cookie session")
        opt2 = DecisionOptionDraft(option_id="opt-jwt", title="JWT Auth", description="Stateless tokens", recommended=True)
        dec_req = DecisionRequiredResult(
            step_id="step-1",
            prompt="Choose token type",
            severity="high",
            options=(opt1, opt2),
        )
        specialist_res = SpecialistExecutionResult(
            status="decision_required",
            summary="Needs token choice",
            output_contract="DecisionRequiredResult",
            decision=dec_req,
        )

        created_dec = await control_plane.handle_specialist_result(
            task_id=task_id,
            step_id="step-1",
            result=specialist_res,
        )
        assert created_dec is not None
        assert created_dec.state.value == "open"
        assert created_dec.severity == DecisionSeverity.HIGH

        # 3. Subsequent user request now reflects the OPEN decision
        req2 = FrontAgentRequest(
            user_input="What is current status?",
            project_id="proj-10",
            task_id=task_id,
        )
        msg2, progress2, open_dec2, _ = await control_plane.process_user_intent(req2)
        assert open_dec2 is not None
        assert open_dec2.decision_id == created_dec.decision_id

        # 4. User resolves the decision
        req_resolve = FrontAgentRequest(
            user_input="We prefer JWT tokens",
            project_id="proj-10",
            task_id=task_id,
            selected_decision={
                "decision_id": created_dec.decision_id,
                "option_id": "opt-jwt",
            },
        )
        msg3, progress3, open_dec3, _ = await control_plane.process_user_intent(req_resolve)
        assert open_dec3 is None  # decision is resolved
        assert "opt-jwt" in msg3
