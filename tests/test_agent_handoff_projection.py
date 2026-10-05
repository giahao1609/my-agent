from __future__ import annotations

from core.agent_role import AgentRole
from core.agent_handoff_message import AgentHandoffMessage
from core.agent_run import AgentRunRecord, AgentRunState
from core.agent_work_result import AgentFinding, AgentWorkResult, FileChange, TestExecutionResult


def test_agent_handoff_message_rendering_and_metadata():
    run = AgentRunRecord(
        run_id="run-handoff-1",
        project_id="proj-1",
        task_id="task-1",
        plan_id="plan-1",
        step_id="step-1",
        agent_id="antigravity-architect",
        execution_role=AgentRole.ARCHITECT,
        state=AgentRunState.COMPLETED,
        result=AgentWorkResult(
            run_id="run-handoff-1",
            status="completed",
            summary="Designed database schema for multi-tenant isolation",
            changed_files=(FileChange(path="schema.sql", change_type="created"),),
            created_files=("schema.sql", "migrations/001.sql"),
            tests=(TestExecutionResult(passed=2, failed=0),),
            findings=(
                AgentFinding(
                    finding_id="f1",
                    kind="architecture",
                    severity="medium",
                    title="Index missing on tenant_id",
                    summary="Query performance might degrade without composite index",
                ),
            ),
            remaining_work=("Implement repository layer",),
            handoff_notes="Used PostgreSQL schema-based multi-tenancy",
        ),
    )

    msg = AgentHandoffMessage.from_run_record(run)
    assert msg.agent_id == "antigravity-architect"
    assert msg.role == AgentRole.ARCHITECT
    assert "schema.sql" in msg.created_files
    assert msg.findings_count == 1

    md = msg.render_markdown()
    assert "Bàn Giao Nhiệm Vụ" in md
    assert "antigravity-architect" in md
    assert "Designed database schema" in md
    assert "schema.sql" in md
    assert "Used PostgreSQL schema-based multi-tenancy" in md

    meta = msg.to_conversation_metadata()
    assert meta["event_type"] == "agent_handoff"
    assert meta["run_id"] == "run-handoff-1"
