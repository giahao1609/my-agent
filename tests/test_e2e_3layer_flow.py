from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from core.agent_role import AgentRole
from core.control_plane import MyAgentControlPlane
from core.decision import DecisionOption, DecisionSeverity, DecisionState
from core.decision_service import DecisionService
from core.front_agent import (
    FrontAgent,
    FrontAgentRequest,
)
from core.handoff_contracts import DecisionOptionDraft, DecisionRequiredResult, ImplementationResult
from core.plan_service import PlanService
from core.project import ProjectRecord
from core.specialist_execution import SpecialistExecutionResult
from core.task_service import TaskService
from persistence.sqlite_decision_store import SQLiteDecisionStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


@pytest.mark.asyncio
async def test_e2e_user_to_specialist_decision_flow():
    """End-to-End simulation of the full 3-layer lifecycle:

    User
      ↓
    Front Agent (Request)
      ↓
    Control Plane (Task & Plan Created)
      ↓
    Specialist Execution (Emits DecisionRequiredResult)
      ↓
    Control Plane (Stores OPEN Decision & Pauses Step)
      ↓
    Front Agent (Presents Decision Options with Trade-offs)
      ↓
    User Decision (User selects Option 1)
      ↓
    Control Plane (Resolves Decision, Resumes Execution, Completes Step)
      ↓
    Front Agent (Presents Final Result to User)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "e2e_flow.db"

        # 1. Initialize Stores
        project_store = SQLiteProjectStore(db_path)
        task_store = SQLiteTaskStore(db_path)
        plan_store = SQLitePlanStore(db_path)
        decision_store = SQLiteDecisionStore(db_path)

        await project_store.initialize()
        await task_store.initialize()
        await plan_store.initialize()
        await decision_store.initialize()

        project = ProjectRecord(
            project_id="payment-platform",
            name="Payment Platform Service",
            workspace_path=str(tmpdir),
        )
        await project_store.create(project)
        await project_store.set_active("payment-platform")

        task_service = TaskService(project_store=project_store, task_store=task_store)
        plan_service = PlanService(plan_store=plan_store, task_store=task_store)
        decision_service = DecisionService(decision_store=decision_store)

        control_plane = MyAgentControlPlane(
            task_store=task_store,
            task_service=task_service,
            plan_store=plan_store,
            plan_service=plan_service,
            decision_service=decision_service,
        )

        front_agent = FrontAgent(control_plane=control_plane)

        # ─── STEP 1: User sends initial request to Front Agent ─────────────────
        user_req_1 = FrontAgentRequest(
            user_input="Tích hợp cổng thanh toán VNPay và ZaloPay có bảo đảm idempotency webhook",
            project_id="payment-platform",
        )
        res_1 = await front_agent.handle_user_request(user_req_1)

        assert res_1.task_id is not None
        assert res_1.progress is not None
        assert res_1.progress.objective == "Tích hợp cổng thanh toán VNPay và ZaloPay có bảo đảm idempotency webhook"
        assert res_1.decision_view is None  # No decision open yet

        task_id = res_1.task_id

        # ─── STEP 2: Control Plane creates Plan with Steps ────────────────────
        await task_service.start_task(task_id)
        plan = await plan_service.materialize_plan(
            task_id=task_id,
            plan_id="plan-payment-1",
            steps=[
                (
                    "step-idempotency-1",
                    "Thiết kế cơ chế chống trùng lặp Webhook Callback (Idempotency)",
                    "Phân tích và quyết định giải pháp chống replay attack cho payment webhook",
                )
            ],
        )
        await plan_service.activate_plan("plan-payment-1")
        await plan_service.start_step("step-idempotency-1")



        # ─── STEP 3: Specialist Agent pauses with DecisionRequiredResult ───────
        opt_redis = DecisionOptionDraft(
            option_id="opt-redis-lock",
            title="Redis Distributed Lock + Idempotency Key",
            description="Lưu idempotency key trong Redis TTL 24h và acquite distributed lock khi nhận webhook.",
            trade_offs="Cần thêm hạ tầng Redis nhưng tốc độ phản hồi cực nhanh (< 5ms).",
            impact="Khả năng scale cao, ngăn chặn race condition tuyệt đối.",
            recommended=True,
        )
        opt_db = DecisionOptionDraft(
            option_id="opt-db-constraint",
            title="Database Unique Constraint & Row Lock",
            description="Tạo bảng payment_idempotency_keys với unique index trong PostgreSQL.",
            trade_offs="Tăng tải I/O lên Database chính khi lưu lượng giao dịch tăng đột biến.",
            impact="Không phụ thuộc thêm Redis, dễ triển khai ngay.",
        )

        specialist_result = SpecialistExecutionResult(
            status="decision_required",
            summary="Cần quyết định kiến trúc Idempotency cho Webhook",
            output_contract="DecisionRequiredResult",
            decision=DecisionRequiredResult(
                step_id="step-idempotency-1",
                prompt="Chọn giải pháp xử lý Idempotency cho Payment Webhook Callback:",
                severity="high",
                options=(opt_redis, opt_db),
            ),
        )

        # Control Plane handles specialist result and opens decision
        created_decision = await control_plane.handle_specialist_result(
            task_id=task_id,
            step_id="step-idempotency-1",
            result=specialist_result,
        )

        assert created_decision is not None
        assert created_decision.state == DecisionState.OPEN
        assert created_decision.severity == DecisionSeverity.HIGH

        # ─── STEP 4: Front Agent presents decision view to User ───────────────
        status_req = FrontAgentRequest(
            user_input="Tiến độ thế nào rồi?",
            project_id="payment-platform",
            task_id=task_id,
        )
        status_res = await front_agent.handle_user_request(status_req)

        assert status_res.decision_view is not None
        assert status_res.decision_view.decision_id == created_decision.decision_id
        assert len(status_res.decision_view.options) == 2

        formatted_ui = front_agent.format_decision_presentation(status_res.decision_view)
        assert "### Cần Quyết Định (Mức Độ: HIGH)" in formatted_ui
        assert "Redis Distributed Lock" in formatted_ui
        assert "Database Unique Constraint" in formatted_ui
        assert "**Bạn muốn chọn phương án nào?**" in formatted_ui

        # ─── STEP 5: User makes choice & resolves decision ────────────────────
        decision_choice_req = FrontAgentRequest(
            user_input="Tôi chọn phương án 1 dùng Redis lock vì hệ thống cần chịu tải cao",
            project_id="payment-platform",
            task_id=task_id,
            selected_decision={
                "decision_id": created_decision.decision_id,
                "option_id": "opt-redis-lock",
            },
        )
        choice_res = await front_agent.handle_user_request(decision_choice_req)

        assert choice_res.decision_view is None  # Decision resolved
        assert "opt-redis-lock" in choice_res.message

        # Verify decision is resolved in SQLite store
        reloaded_dec = await decision_service.get_decision(created_decision.decision_id)
        assert reloaded_dec.state == DecisionState.RESOLVED
        assert reloaded_dec.selected_option_id == "opt-redis-lock"

        # ─── STEP 6: Specialist completes step with ImplementationResult ──────
        completed_step_res = SpecialistExecutionResult(
            status="completed",
            summary="Đã triển khai Idempotency Middleware sử dụng Redis Distributed Lock",
            output_contract="ImplementationResult",
            payload=ImplementationResult(
                step_id="step-idempotency-1",
                summary="Triển khai Redis webhook idempotency",
                created_files=("services/payment-service/pkg/idempotency/redis_lock.go",),
            ).to_dict(),
        )

        await control_plane.handle_specialist_result(
            task_id=task_id,
            step_id="step-idempotency-1",
            result=completed_step_res,
        )


        # ─── STEP 7: Final Progress Check ─────────────────────────────────────
        final_req = FrontAgentRequest(
            user_input="Kiểm tra trạng thái hoàn thành",
            project_id="payment-platform",
            task_id=task_id,
        )
        final_res = await front_agent.handle_user_request(final_req)

        assert final_res.progress is not None
        assert final_res.progress.steps[0].state == "completed"
        assert final_res.decision_view is None
