"""Focused security regression suite for Front Agent and Control Plane authorization (SEC-01 .. SEC-18)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from core.control_plane import InMemoryPendingApprovalStore, MyAgentControlPlane
from core.decision import DecisionOption, DecisionSeverity, DecisionState
from core.decision_service import DecisionService
from core.front_agent import FrontAgentRequest, ResponseKind
from core.plan_service import PlanService
from core.project import ProjectRecord
from core.security_authorization import (
    SecurityAuthorizationError,
    SecurityAuthorizationManager,
)
from core.task_service import TaskService
from my_agent_mcp import server
from persistence.sqlite_decision_store import SQLiteDecisionStore
from persistence.sqlite_pending_approval_store import SqlitePendingApprovalStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def _setup_two_project_control_plane(tmp_path: Path):
    """Creates synthetic Alpha and Beta projects with isolated workspaces and tasks."""
    db_path = tmp_path / "front_agent_auth.db"
    ws_alpha = tmp_path / "ws_alpha"
    ws_beta = tmp_path / "ws_beta"
    ws_unbound = tmp_path / "ws_unbound"
    ws_alpha.mkdir(parents=True, exist_ok=True)
    ws_beta.mkdir(parents=True, exist_ok=True)
    ws_unbound.mkdir(parents=True, exist_ok=True)

    projects = SQLiteProjectStore(db_path)
    tasks = SQLiteTaskStore(db_path)
    plans = SQLitePlanStore(db_path)
    decisions = SQLiteDecisionStore(db_path)

    await projects.initialize()
    await tasks.initialize()
    await plans.initialize()
    await decisions.initialize()

    await projects.create(
        ProjectRecord(
            project_id="proj-alpha",
            name="Project Alpha",
            workspace_path=str(ws_alpha),
            active_task_id="task-alpha-1",
        )
    )
    await projects.create(
        ProjectRecord(
            project_id="proj-beta",
            name="Project Beta",
            workspace_path=str(ws_beta),
            active_task_id="task-beta-secret",
        )
    )
    # Deliberately set active project to Beta while workspace binds to Alpha
    await projects.set_active("proj-beta")

    task_service = TaskService(project_store=projects, task_store=tasks)
    await task_service.create_task(
        project_id="proj-alpha",
        task_id="task-alpha-1",
        objective="Implement Alpha UI widget",
    )
    await task_service.create_task(
        project_id="proj-beta",
        task_id="task-beta-secret",
        objective="SECRET_BETA_CREDENTIAL_ROTATION",
    )

    decision_service = DecisionService(decisions)
    approval_store = InMemoryPendingApprovalStore()
    control_plane = MyAgentControlPlane(
        task_store=tasks,
        task_service=task_service,
        plan_store=plans,
        plan_service=PlanService(plan_store=plans, task_store=tasks),
        decision_service=decision_service,
        approval_store=approval_store,
    )

    return {
        "db_path": db_path,
        "ws_alpha": ws_alpha,
        "ws_beta": ws_beta,
        "ws_unbound": ws_unbound,
        "projects": projects,
        "tasks": tasks,
        "plans": plans,
        "decisions": decisions,
        "task_service": task_service,
        "decision_service": decision_service,
        "approval_store": approval_store,
        "control_plane": control_plane,
    }


def _patch_front_agent_mcp_server(monkeypatch, env: dict, *, workspace_root: Path):
    """Binds MCP server globals to synthetic test stores and workspace without side effects."""

    async def fake_initialize() -> None:
        return None

    auth_manager = SecurityAuthorizationManager(
        project_store=env["projects"],
        server_workspace_root=workspace_root,
    )

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "projects", env["projects"])
    monkeypatch.setattr(server, "tasks", env["tasks"], raising=False)
    monkeypatch.setattr(server, "plans", env["plans"], raising=False)
    monkeypatch.setattr(server, "decisions", env["decisions"], raising=False)
    monkeypatch.setattr(server, "_decision_service", env["decision_service"], raising=False)
    monkeypatch.setattr(server, "pending_approvals", env["approval_store"], raising=False)
    monkeypatch.setattr(server, "_agent_run_service", None, raising=False)
    monkeypatch.setattr(server, "_user_model_engine", None, raising=False)
    monkeypatch.setattr(server, "_auth_manager", auth_manager)
    return auth_manager


def _count_rows(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0


@pytest.mark.asyncio
async def test_sec_01_control_plane_same_project_task_allowed(tmp_path: Path) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    cp: MyAgentControlPlane = env["control_plane"]

    req = FrontAgentRequest(
        user_input="Check status of Alpha task",
        project_id="proj-alpha",
        task_id="task-alpha-1",
    )
    kind, msg, progress, _dec, _app, _res = await cp.submit_user_request(req)

    assert kind == ResponseKind.MESSAGE
    assert progress is not None
    assert progress.task_id == "task-alpha-1"
    assert "Implement Alpha UI widget" in msg
    assert len(await env["tasks"].list_for_project("proj-alpha")) == 1


@pytest.mark.asyncio
async def test_sec_02_control_plane_cross_project_task_idor_rejected(tmp_path: Path) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    cp: MyAgentControlPlane = env["control_plane"]

    # Seed decision & pending approval on Beta's task to ensure mutation gates cannot bypass task auth.
    beta_dec = await env["decision_service"].create_decision(
        task_id="task-beta-secret",
        prompt="Rotate secret?",
        severity=DecisionSeverity.HIGH,
        options=[
            DecisionOption(option_id="yes", title="Yes", description="Rotate"),
            DecisionOption(option_id="no", title="No", description="Keep"),
        ],
    )
    await env["approval_store"].save(
        {"tool_call_id": "call-beta-1", "task_id": "task-beta-secret", "tool_name": "secret_rotator"}
    )

    alpha_before = len(await env["tasks"].list_for_project("proj-alpha"))
    beta_before = len(await env["tasks"].list_for_project("proj-beta"))

    with pytest.raises(SecurityAuthorizationError) as exc_info:
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Read Beta task",
                project_id="proj-alpha",
                task_id="task-beta-secret",
            )
        )
    assert exc_info.value.violation_type == "UNAUTHORIZED_ACCESS"
    assert exc_info.value.authorized_project_id == "proj-alpha"

    # Even if paired with selected_decision or approval_response, foreign task_id fails BEFORE mutating state.
    with pytest.raises(SecurityAuthorizationError) as dec_exc:
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Approve Beta decision",
                project_id="proj-alpha",
                task_id="task-beta-secret",
                selected_decision={"decision_id": beta_dec.decision_id, "option_id": "yes"},
            )
        )
    assert dec_exc.value.violation_type == "UNAUTHORIZED_ACCESS"
    still_open = await env["decision_service"].list_open_decisions("task-beta-secret")
    assert len(still_open) == 1

    with pytest.raises(SecurityAuthorizationError) as app_exc:
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Approve Beta tool",
                project_id="proj-alpha",
                task_id="task-beta-secret",
                approval_response={"tool_call_id": "call-beta-1", "approved": True},
            )
        )
    assert app_exc.value.violation_type == "UNAUTHORIZED_ACCESS"
    assert await env["approval_store"].get("call-beta-1") is not None

    assert len(await env["tasks"].list_for_project("proj-alpha")) == alpha_before
    assert len(await env["tasks"].list_for_project("proj-beta")) == beta_before


@pytest.mark.asyncio
async def test_sec_03_mcp_front_agent_request_unbound_workspace_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_unbound"])
    tasks_before = _count_rows(env["db_path"], "tasks")

    with pytest.raises(SecurityAuthorizationError) as exc_info:
        await server.front_agent_request(
            user_input="Should not run on unbound workspace",
            project_id=None,
            task_id=None,
        )

    assert exc_info.value.violation_type == "UNAUTHORIZED_WORKSPACE"
    assert _count_rows(env["db_path"], "tasks") == tasks_before


@pytest.mark.asyncio
async def test_sec_04_mcp_front_agent_request_mismatched_project_id_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])
    tasks_before = _count_rows(env["db_path"], "tasks")

    with pytest.raises(SecurityAuthorizationError) as exc_info:
        await server.front_agent_request(
            user_input="Access Beta project from Alpha workspace",
            project_id="proj-beta",
            task_id=None,
        )

    assert exc_info.value.violation_type == "CROSS_PROJECT_IDOR"
    assert _count_rows(env["db_path"], "tasks") == tasks_before


@pytest.mark.asyncio
async def test_sec_05_mcp_front_agent_request_cross_project_task_idor_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    auth_manager = _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    audit_events: list[dict] = []
    orig_log = auth_manager._log_security_audit

    def capture_audit(**kwargs):
        audit_events.append(kwargs)
        orig_log(**kwargs)

    monkeypatch.setattr(auth_manager, "_log_security_audit", capture_audit)
    tasks_before = _count_rows(env["db_path"], "tasks")

    # Both explicit matching project_id and omitted project_id (plus padded whitespace) must fail closed.
    for caller_proj, caller_task in [
        ("proj-alpha", "task-beta-secret"),
        (None, "  task-beta-secret  "),
    ]:
        with pytest.raises(SecurityAuthorizationError) as exc_info:
            await server.front_agent_request(
                user_input="Inspect foreign task",
                project_id=caller_proj,
                task_id=caller_task,
            )
        assert exc_info.value.violation_type == "UNAUTHORIZED_ACCESS"
        assert exc_info.value.authorized_project_id == "proj-alpha"

    denied_events = [ev for ev in audit_events if ev["decision"] == "DENIED"]
    assert _count_rows(env["db_path"], "tasks") == tasks_before
    assert len(denied_events) == 2
    assert all(
        ev["event"] == "FRONT_AGENT_REQUEST"
        and ev["reason"] == "CROSS_PROJECT_IDOR_TASK_ACCESS_FORBIDDEN"
        and ev["authorized_project_id"] == "proj-alpha"
        for ev in denied_events
    )


@pytest.mark.asyncio
async def test_sec_06_unknown_and_blank_task_id_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    cp: MyAgentControlPlane = env["control_plane"]
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])
    tasks_before = _count_rows(env["db_path"], "tasks")

    # ControlPlane rejects unknown task ID with unified SecurityAuthorizationError and blanks with ValueError.
    with pytest.raises(SecurityAuthorizationError) as cp_missing:
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Resume ghost task",
                project_id="proj-alpha",
                task_id="task-missing-999",
            )
        )
    assert cp_missing.value.violation_type == "UNAUTHORIZED_ACCESS"
    assert cp_missing.value.authorized_project_id == "proj-alpha"
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="task_id must not be blank"):
            await cp.submit_user_request(
                FrontAgentRequest(
                    user_input="Blank task id",
                    project_id="proj-alpha",
                    task_id=blank,
                )
            )

    # MCP front_agent_request also rejects unknown task ID cleanly and blanks with ValueError.
    with pytest.raises(SecurityAuthorizationError) as mcp_missing:
        await server.front_agent_request(
            user_input="Resume ghost task",
            project_id="proj-alpha",
            task_id="task-missing-999",
        )
    assert mcp_missing.value.violation_type == "UNAUTHORIZED_ACCESS"
    assert mcp_missing.value.authorized_project_id == "proj-alpha"
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="task_id must not be blank"):
            await server.front_agent_request(
                user_input="Blank task id",
                project_id="proj-alpha",
                task_id=blank,
            )

    assert _count_rows(env["db_path"], "tasks") == tasks_before


@pytest.mark.asyncio
async def test_sec_07_error_payload_does_not_disclose_foreign_task_data(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    cp: MyAgentControlPlane = env["control_plane"]
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    with pytest.raises(SecurityAuthorizationError) as cp_exc:
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Attempt disclosure via ControlPlane",
                project_id="proj-alpha",
                task_id="task-beta-secret",
            )
        )

    with pytest.raises(SecurityAuthorizationError) as mcp_exc:
        await server.front_agent_request(
            user_input="Attempt disclosure via MCP",
            project_id="proj-alpha",
            task_id="task-beta-secret",
        )

    for err in (cp_exc.value, mcp_exc.value):
        serialized = json.dumps(err.to_dict(), ensure_ascii=False)
        for text in (str(err), serialized):
            assert "proj-beta" not in text
            assert "SECRET_BETA_CREDENTIAL_ROTATION" not in text


@pytest.mark.asyncio
async def test_sec_08_mcp_front_agent_request_same_project_and_new_task_preserved(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    # 1. Existing same-project task succeeds cleanly.
    res_existing = await server.front_agent_request(
        user_input="Continue Alpha widget",
        project_id="proj-alpha",
        task_id="task-alpha-1",
    )
    assert res_existing["status"] == "ok"
    assert res_existing["response"]["progress"]["task_id"] == "task-alpha-1"
    assert "Implement Alpha UI widget" in res_existing["response"]["message"]

    # 2. Omitting task_id creates a new task scoped strictly to the authorized Alpha project.
    res_new = await server.front_agent_request(
        user_input="Create Alpha follow-up task",
        project_id=None,
        task_id=None,
    )
    assert res_new["status"] == "ok"
    new_task_id = res_new["response"]["progress"]["task_id"]
    assert new_task_id != "task-alpha-1"
    created = await env["tasks"].get(new_task_id)
    assert created is not None
    assert created.project_id == "proj-alpha"


@pytest.mark.asyncio
async def test_sec_09_task_existence_disclosure_indistinguishable_unknown_vs_foreign(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    cp: MyAgentControlPlane = env["control_plane"]
    auth_manager = _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    audit_events: list[dict] = []
    orig_log = auth_manager._log_security_audit

    def capture_audit(**kwargs):
        audit_events.append(kwargs)
        orig_log(**kwargs)

    monkeypatch.setattr(auth_manager, "_log_security_audit", capture_audit)

    # 1. ControlPlane responses for unknown vs foreign task_id must be identical.
    with pytest.raises(SecurityAuthorizationError) as cp_unknown:
        await cp.submit_user_request(
            FrontAgentRequest(user_input="Probe", project_id="proj-alpha", task_id="task-never-existed")
        )
    with pytest.raises(SecurityAuthorizationError) as cp_foreign:
        await cp.submit_user_request(
            FrontAgentRequest(user_input="Probe", project_id="proj-alpha", task_id="task-beta-secret")
        )
    assert type(cp_unknown.value) is type(cp_foreign.value)
    assert str(cp_unknown.value) == str(cp_foreign.value)
    assert cp_unknown.value.to_dict() == cp_foreign.value.to_dict()
    assert json.dumps(cp_unknown.value.to_dict()) == json.dumps(cp_foreign.value.to_dict())

    # 2. Direct server.front_agent_request responses must also be byte-for-byte identical.
    with pytest.raises(SecurityAuthorizationError) as mcp_unknown:
        await server.front_agent_request(
            user_input="Probe", project_id="proj-alpha", task_id="task-never-existed"
        )
    with pytest.raises(SecurityAuthorizationError) as mcp_foreign:
        await server.front_agent_request(
            user_input="Probe", project_id="proj-alpha", task_id="task-beta-secret"
        )
    assert type(mcp_unknown.value) is type(mcp_foreign.value)
    assert str(mcp_unknown.value) == str(mcp_foreign.value)
    assert mcp_unknown.value.to_dict() == mcp_foreign.value.to_dict()
    assert json.dumps(mcp_unknown.value.to_dict()) == json.dumps(mcp_foreign.value.to_dict())

    # 3. MCP wire call_tool error payload must also be identical.
    with pytest.raises(ToolError) as wire_unknown:
        await server.mcp.call_tool(
            "front_agent_request",
            {"user_input": "Probe", "project_id": "proj-alpha", "task_id": "task-never-existed"},
        )
    with pytest.raises(ToolError) as wire_foreign:
        await server.mcp.call_tool(
            "front_agent_request",
            {"user_input": "Probe", "project_id": "proj-alpha", "task_id": "task-beta-secret"},
        )
    assert str(wire_unknown.value) == str(wire_foreign.value)
    assert "proj-beta" not in str(wire_foreign.value)
    assert "SECRET_BETA_CREDENTIAL_ROTATION" not in str(wire_foreign.value)

    # 4. Internal server security audit STILL preserves distinct classification.
    denied_reasons = [ev["reason"] for ev in audit_events if ev["decision"] == "DENIED"]
    assert "TASK_NOT_FOUND_OR_UNAUTHORIZED" in denied_reasons
    assert "CROSS_PROJECT_IDOR_TASK_ACCESS_FORBIDDEN" in denied_reasons


@pytest.mark.asyncio
async def test_sec_10_cross_project_decision_without_task_id_rejected_and_indistinguishable(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    cp: MyAgentControlPlane = env["control_plane"]
    auth_manager = _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    beta_dec = await env["decision_service"].create_decision(
        task_id="task-beta-secret",
        prompt="Rotate Beta prod key?",
        severity=DecisionSeverity.HIGH,
        options=[
            DecisionOption(option_id="yes", title="Yes", description="Rotate"),
            DecisionOption(option_id="no", title="No", description="Keep"),
        ],
    )

    audit_events: list[dict] = []
    orig_log = auth_manager._log_security_audit

    def capture_audit(**kwargs):
        audit_events.append(kwargs)
        orig_log(**kwargs)

    monkeypatch.setattr(auth_manager, "_log_security_audit", capture_audit)
    tasks_before = _count_rows(env["db_path"], "tasks")

    # 1. Foreign decision_id without task_id AND unknown decision_id must match on ControlPlane & MCP.
    with pytest.raises(SecurityAuthorizationError) as cp_unknown:
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Resolve unknown decision",
                project_id="proj-alpha",
                task_id=None,
                selected_decision={"decision_id": "dec-does-not-exist", "option_id": "yes"},
            )
        )
    with pytest.raises(SecurityAuthorizationError) as cp_foreign:
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Resolve Beta decision without task_id",
                project_id="proj-alpha",
                task_id=None,
                selected_decision={"decision_id": beta_dec.decision_id, "option_id": "yes"},
            )
        )
    assert cp_unknown.value.to_dict() == cp_foreign.value.to_dict()

    with pytest.raises(SecurityAuthorizationError) as mcp_unknown:
        await server.front_agent_request(
            user_input="Resolve unknown decision",
            project_id="proj-alpha",
            task_id=None,
            selected_decision={"decision_id": "dec-does-not-exist", "option_id": "yes"},
        )
    with pytest.raises(SecurityAuthorizationError) as mcp_foreign:
        await server.front_agent_request(
            user_input="Resolve Beta decision without task_id",
            project_id="proj-alpha",
            task_id=None,
            selected_decision={"decision_id": beta_dec.decision_id, "option_id": "yes"},
        )
    # Pairing foreign decision_id with Alpha's task_id must also fail indistinguishably.
    with pytest.raises(SecurityAuthorizationError) as mcp_mismatch_task:
        await server.front_agent_request(
            user_input="Mismatched task/decision",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            selected_decision={"decision_id": beta_dec.decision_id, "option_id": "yes"},
        )
    assert mcp_unknown.value.to_dict() == mcp_foreign.value.to_dict()
    assert mcp_unknown.value.to_dict() == mcp_mismatch_task.value.to_dict()
    assert "proj-beta" not in json.dumps(mcp_foreign.value.to_dict())

    # 2. Malformed/blank selected_decision fails closed with ValueError.
    with pytest.raises(ValueError, match="selected_decision"):
        await server.front_agent_request(
            user_input="Blank decision",
            project_id="proj-alpha",
            selected_decision={"decision_id": "   ", "option_id": "yes"},
        )

    # 3. Verify Beta decision stays OPEN and zero tasks were created.
    reloaded = await env["decision_service"].get_decision(beta_dec.decision_id)
    assert reloaded is not None and reloaded.state == DecisionState.OPEN
    assert _count_rows(env["db_path"], "tasks") == tasks_before

    denied_reasons = [ev["reason"] for ev in audit_events if ev["decision"] == "DENIED"]
    assert "DECISION_NOT_FOUND_OR_UNAUTHORIZED" in denied_reasons
    assert "CROSS_PROJECT_IDOR_DECISION_ACCESS_FORBIDDEN" in denied_reasons


@pytest.mark.asyncio
async def test_sec_11_cross_project_approval_without_task_id_rejected_and_indistinguishable(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    sqlite_approvals = SqlitePendingApprovalStore(env["db_path"])
    await sqlite_approvals.initialize()
    env["approval_store"] = sqlite_approvals

    cp = MyAgentControlPlane(
        task_store=env["tasks"],
        task_service=env["task_service"],
        plan_store=env["plans"],
        plan_service=PlanService(plan_store=env["plans"], task_store=env["tasks"]),
        decision_service=env["decision_service"],
        approval_store=sqlite_approvals,
    )
    auth_manager = _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    await sqlite_approvals.save(
        {
            "tool_call_id": "call-beta-sqlite-1",
            "task_id": "task-beta-secret",
            "project_id": "proj-beta",
            "tool_name": "delete_prod_secret",
            "arguments": {"target": "prod"},
        }
    )
    # Seed an unscoped/orphan record (neither project_id nor task_id) to confirm fail-closed handling.
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-orphan-1",
            "tool_name": "orphan_op",
            "arguments": {},
        }
    )

    audit_events: list[dict] = []
    orig_log = auth_manager._log_security_audit

    def capture_audit(**kwargs):
        audit_events.append(kwargs)
        orig_log(**kwargs)

    monkeypatch.setattr(auth_manager, "_log_security_audit", capture_audit)
    tasks_before = _count_rows(env["db_path"], "tasks")
    approvals_before = _count_rows(env["db_path"], "pending_approvals")

    # Unknown, foreign (without task_id), foreign (with Alpha task_id), and orphan approvals must all match.
    for target_call_id, req_task_id in [
        ("call-never-existed", None),
        ("call-beta-sqlite-1", None),
        ("call-beta-sqlite-1", "task-alpha-1"),
        ("call-orphan-1", None),
    ]:
        with pytest.raises(SecurityAuthorizationError) as cp_err:
            await cp.submit_user_request(
                FrontAgentRequest(
                    user_input="Approve tool",
                    project_id="proj-alpha",
                    task_id=req_task_id,
                    approval_response={"tool_call_id": target_call_id, "approved": True},
                )
            )
        with pytest.raises(SecurityAuthorizationError) as mcp_err:
            await server.front_agent_request(
                user_input="Approve tool",
                project_id="proj-alpha",
                task_id=req_task_id,
                approval_response={"tool_call_id": target_call_id, "approved": True},
            )
        assert cp_err.value.violation_type == "UNAUTHORIZED_ACCESS"
        assert mcp_err.value.violation_type == "UNAUTHORIZED_ACCESS"
        assert cp_err.value.to_dict() == mcp_err.value.to_dict()
        assert "proj-beta" not in json.dumps(mcp_err.value.to_dict())

    # Blank tool_call_id fails closed with ValueError.
    with pytest.raises(ValueError, match="approval_response"):
        await server.front_agent_request(
            user_input="Blank approval",
            project_id="proj-alpha",
            approval_response={"tool_call_id": "   ", "approved": True},
        )

    assert await sqlite_approvals.get("call-beta-sqlite-1") is not None
    assert await sqlite_approvals.get("call-orphan-1") is not None
    assert _count_rows(env["db_path"], "tasks") == tasks_before
    assert _count_rows(env["db_path"], "pending_approvals") == approvals_before

    denied_reasons = [ev["reason"] for ev in audit_events if ev["decision"] == "DENIED"]
    assert "APPROVAL_NOT_FOUND_OR_UNAUTHORIZED" in denied_reasons
    assert "CROSS_PROJECT_IDOR_APPROVAL_ACCESS_FORBIDDEN" in denied_reasons


@pytest.mark.asyncio
async def test_sec_12_authorized_same_project_decision_and_approval_succeed(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    sqlite_approvals = SqlitePendingApprovalStore(env["db_path"])
    await sqlite_approvals.initialize()
    env["approval_store"] = sqlite_approvals
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    alpha_dec = await env["decision_service"].create_decision(
        task_id="task-alpha-1",
        prompt="Select Alpha theme?",
        severity=DecisionSeverity.MEDIUM,
        options=[
            DecisionOption(option_id="light", title="Light", description="Light UI"),
            DecisionOption(option_id="dark", title="Dark", description="Dark UI"),
        ],
    )
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-alpha-1",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "write_widget",
            "arguments": {"path": "widget.tsx"},
        }
    )

    # Authorized resolution works even when task_id is omitted.
    dec_res = await server.front_agent_request(
        user_input="Choose dark",
        project_id="proj-alpha",
        task_id=None,
        selected_decision={"decision_id": alpha_dec.decision_id, "option_id": "dark"},
    )
    assert dec_res["status"] == "ok"
    reloaded_dec = await env["decision_service"].get_decision(alpha_dec.decision_id)
    assert reloaded_dec is not None and reloaded_dec.state == DecisionState.RESOLVED
    assert reloaded_dec.selected_option_id == "dark"

    app_res = await server.front_agent_request(
        user_input="Approve widget write",
        project_id="proj-alpha",
        task_id=None,
        approval_response={"tool_call_id": "call-alpha-1", "approved": True},
    )
    assert app_res["status"] == "ok"
    resolved_row = await sqlite_approvals.get("call-alpha-1")
    assert resolved_row is not None
    assert resolved_row["status"] == "APPROVED"
    assert resolved_row["resolved_at"] is not None
    assert "Approve widget write" in (resolved_row["resolution_note"] or "")

    # Subsequent task status check must not re-present the already-approved call as PENDING.
    follow_up = await server.front_agent_request(
        user_input="Check Alpha task status",
        project_id="proj-alpha",
        task_id="task-alpha-1",
    )
    assert follow_up["status"] == "ok"
    assert follow_up["response"]["kind"] == ResponseKind.MESSAGE.value


@pytest.mark.asyncio
async def test_sec_13_mcp_wire_call_tool_positive_and_negative_acceptance(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    sqlite_approvals = SqlitePendingApprovalStore(env["db_path"])
    await sqlite_approvals.initialize()
    env["approval_store"] = sqlite_approvals
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    beta_dec = await env["decision_service"].create_decision(
        task_id="task-beta-secret",
        prompt="Beta wire prompt",
        severity=DecisionSeverity.HIGH,
        options=[
            DecisionOption(option_id="opt1", title="Opt 1", description="One"),
            DecisionOption(option_id="opt2", title="Opt 2", description="Two"),
        ],
    )
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-beta-wire",
            "task_id": "task-beta-secret",
            "project_id": "proj-beta",
            "tool_name": "beta_tool",
            "arguments": {},
        }
    )

    # Positive MCP call succeeds.
    ok_res = await server.mcp.call_tool(
        "front_agent_request",
        {"user_input": "Check Alpha task", "project_id": "proj-alpha", "task_id": "task-alpha-1"},
    )
    assert not ok_res.is_error
    payload = json.loads(ok_res.content[0].text)
    assert payload["status"] == "ok"
    assert payload["response"]["progress"]["task_id"] == "task-alpha-1"

    # Negative MCP calls fail closed and never reveal foreign project or task existence.
    with pytest.raises(ToolError) as dec_unknown:
        await server.mcp.call_tool(
            "front_agent_request",
            {
                "user_input": "Probe dec",
                "project_id": "proj-alpha",
                "selected_decision": {"decision_id": "dec-unknown-wire", "option_id": "opt1"},
            },
        )
    with pytest.raises(ToolError) as dec_foreign:
        await server.mcp.call_tool(
            "front_agent_request",
            {
                "user_input": "Probe dec",
                "project_id": "proj-alpha",
                "selected_decision": {"decision_id": beta_dec.decision_id, "option_id": "opt1"},
            },
        )
    assert str(dec_unknown.value) == str(dec_foreign.value)

    with pytest.raises(ToolError) as app_unknown:
        await server.mcp.call_tool(
            "front_agent_request",
            {
                "user_input": "Probe app",
                "project_id": "proj-alpha",
                "approval_response": {"tool_call_id": "call-unknown-wire", "approved": True},
            },
        )
    with pytest.raises(ToolError) as app_foreign:
        await server.mcp.call_tool(
            "front_agent_request",
            {
                "user_input": "Probe app",
                "project_id": "proj-alpha",
                "approval_response": {"tool_call_id": "call-beta-wire", "approved": True},
            },
        )
    assert str(app_unknown.value) == str(app_foreign.value)
    assert await sqlite_approvals.get("call-beta-wire") is not None


@pytest.mark.asyncio
async def test_sec_14_sqlite_approval_lifecycle_replay_expiry_and_race_protection(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    sqlite_approvals = SqlitePendingApprovalStore(env["db_path"])
    await sqlite_approvals.initialize()
    env["approval_store"] = sqlite_approvals
    cp = MyAgentControlPlane(
        task_store=env["tasks"],
        task_service=env["task_service"],
        plan_store=env["plans"],
        plan_service=PlanService(plan_store=env["plans"], task_store=env["tasks"]),
        decision_service=env["decision_service"],
        approval_store=sqlite_approvals,
    )
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    # 1. Rejection preserves SQLite row with REJECTED status and blocks replay.
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-alpha-reject",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "run_command",
            "arguments": {"cmd": "ls"},
        }
    )
    rej_res = await server.front_agent_request(
        user_input="Do not run",
        project_id="proj-alpha",
        task_id="task-alpha-1",
        approval_response={"tool_call_id": "call-alpha-reject", "approved": False},
    )
    assert rej_res["status"] == "ok"
    rej_row = await sqlite_approvals.get("call-alpha-reject")
    assert rej_row is not None and rej_row["status"] == "REJECTED"

    # Replaying terminal approval fails closed without deleting or changing status.
    with pytest.raises((ValueError, PermissionError)):
        await server.front_agent_request(
            user_input="Replay reject as approve",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            approval_response={"tool_call_id": "call-alpha-reject", "approved": True},
        )
    after_replay = await sqlite_approvals.get("call-alpha-reject")
    assert after_replay is not None and after_replay["status"] == "REJECTED"

    # 2. Expired TTL approval fails closed, persists as EXPIRED, and is never deleted.
    past_iso = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-alpha-expired",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "write_file",
            "arguments": {"path": "x.py"},
            "expires_at": past_iso,
        }
    )
    with pytest.raises((PermissionError, ValueError)):
        await server.front_agent_request(
            user_input="Approve stale call",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            approval_response={"tool_call_id": "call-alpha-expired", "approved": True},
        )
    exp_row = await sqlite_approvals.get("call-alpha-expired")
    assert exp_row is not None and exp_row["status"] == "EXPIRED"

    # Also test already-marked EXPIRED row cannot be deleted/resolved.
    with pytest.raises((PermissionError, ValueError)):
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Retry expired",
                project_id="proj-alpha",
                task_id="task-alpha-1",
                approval_response={"tool_call_id": "call-alpha-expired", "approved": True},
            )
        )
    assert await sqlite_approvals.get("call-alpha-expired") is not None

    # 3. Concurrent race: exactly one resolution succeeds; loser fails closed without corrupting state.
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-alpha-race",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "deploy_local",
            "arguments": {},
        }
    )
    race_results = await asyncio.gather(
        server.front_agent_request(
            user_input="Approve race",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            approval_response={"tool_call_id": "call-alpha-race", "approved": True},
        ),
        server.front_agent_request(
            user_input="Reject race",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            approval_response={"tool_call_id": "call-alpha-race", "approved": False},
        ),
        return_exceptions=True,
    )
    successes = [r for r in race_results if isinstance(r, dict) and r.get("status") == "ok"]
    failures = [r for r in race_results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    race_row = await sqlite_approvals.get("call-alpha-race")
    assert race_row is not None and race_row["status"] in {"APPROVED", "REJECTED"}


@pytest.mark.asyncio
async def test_sec_15_in_memory_approval_store_blocks_unscoped_leak_and_preserves_scoped_legacy(
    tmp_path: Path,
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    mem_store: InMemoryPendingApprovalStore = env["approval_store"]
    cp: MyAgentControlPlane = env["control_plane"]

    # 1. Unscoped in-memory record must NEVER appear under unrelated tasks.
    await mem_store.save(
        {
            "tool_call_id": "call-unscoped-mem",
            "tool_name": "dangerous_op",
            "arguments": {},
        }
    )
    assert await mem_store.list_by_task("task-alpha-1") == []
    assert await mem_store.list_by_task("task-beta-secret") == []
    assert await cp.get_pending_interaction("task-alpha-1") is None

    kind, _msg, _prog, _dec, app, _res = await cp.submit_user_request(
        FrontAgentRequest(
            user_input="Check Alpha task",
            project_id="proj-alpha",
            task_id="task-alpha-1",
        )
    )
    assert kind == ResponseKind.MESSAGE
    assert app is None

    # 2. Explicitly task-scoped legacy registration preserves in-memory flow + durable status retention.
    await cp.register_pending_approval(
        tool_call_id="call-alpha-mem-1",
        tool_name="delete_cache",
        arguments={"path": "tmp/"},
        task_id="task-alpha-1",
    )
    pending = await cp.get_pending_interaction("task-alpha-1")
    assert pending is not None and pending["type"] == "approval"
    assert pending["approval"]["tool_call_id"] == "call-alpha-mem-1"

    res_kind, _msg, _prog, _dec, _app, _res = await cp.submit_user_request(
        FrontAgentRequest(
            user_input="Approve memory action",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            approval_response={"tool_call_id": "call-alpha-mem-1", "approved": True},
        )
    )
    assert res_kind == ResponseKind.PROGRESS
    mem_record = await mem_store.get("call-alpha-mem-1")
    assert mem_record is not None and mem_record["status"] == "APPROVED"
    assert await cp.get_pending_interaction("task-alpha-1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_approved",
    ["false", "true", "0", "1", 1, 0, None, ["true"], {"value": True}],
)
async def test_sec_16_approval_response_requires_strict_boolean_approved(
    tmp_path: Path, monkeypatch, bad_approved
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    sqlite_approvals = SqlitePendingApprovalStore(env["db_path"])
    await sqlite_approvals.initialize()
    env["approval_store"] = sqlite_approvals
    cp = MyAgentControlPlane(
        task_store=env["tasks"],
        task_service=env["task_service"],
        plan_store=env["plans"],
        plan_service=PlanService(plan_store=env["plans"], task_store=env["tasks"]),
        decision_service=env["decision_service"],
        approval_store=sqlite_approvals,
    )
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    await sqlite_approvals.save(
        {
            "tool_call_id": "call-alpha-bool",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "write_file",
            "arguments": {},
        }
    )

    with pytest.raises(ValueError, match="approved"):
        await cp.submit_user_request(
            FrontAgentRequest(
                user_input="Bad bool cp",
                project_id="proj-alpha",
                task_id="task-alpha-1",
                approval_response={"tool_call_id": "call-alpha-bool", "approved": bad_approved},
            )
        )

    with pytest.raises(ValueError, match="approved"):
        await server.front_agent_request(
            user_input="Bad bool mcp",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            approval_response={"tool_call_id": "call-alpha-bool", "approved": bad_approved},
        )

    # Also test completely missing 'approved' key.
    with pytest.raises(ValueError, match="approved"):
        await server.front_agent_request(
            user_input="Missing approved key",
            project_id="proj-alpha",
            task_id="task-alpha-1",
            approval_response={"tool_call_id": "call-alpha-bool"},
        )

    row = await sqlite_approvals.get("call-alpha-bool")
    assert row is not None and row["status"] == "PENDING"


@pytest.mark.asyncio
async def test_sec_17_terminal_and_expired_approvals_never_presented_as_pending(
    tmp_path: Path, monkeypatch
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    sqlite_approvals = SqlitePendingApprovalStore(env["db_path"])
    await sqlite_approvals.initialize()
    env["approval_store"] = sqlite_approvals
    cp = MyAgentControlPlane(
        task_store=env["tasks"],
        task_service=env["task_service"],
        plan_store=env["plans"],
        plan_service=PlanService(plan_store=env["plans"], task_store=env["tasks"]),
        decision_service=env["decision_service"],
        approval_store=sqlite_approvals,
    )
    _patch_front_agent_mcp_server(monkeypatch, env, workspace_root=env["ws_alpha"])

    past_iso = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-term-approved",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "op1",
            "status": "APPROVED",
        }
    )
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-term-rejected",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "op2",
            "status": "REJECTED",
        }
    )
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-term-expired",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "op3",
            "status": "EXPIRED",
            "expires_at": past_iso,
        }
    )
    await sqlite_approvals.save(
        {
            "tool_call_id": "call-ttl-elapsed-pending",
            "task_id": "task-alpha-1",
            "project_id": "proj-alpha",
            "tool_name": "op4",
            "status": "PENDING",
            "expires_at": past_iso,
        }
    )

    assert await cp.get_pending_interaction("task-alpha-1") is None
    res = await server.front_agent_request(
        user_input="What is the task status?",
        project_id="proj-alpha",
        task_id="task-alpha-1",
    )
    assert res["status"] == "ok"
    assert res["response"]["kind"] == ResponseKind.MESSAGE.value
    assert res["response"]["approval_view"] is None


@pytest.mark.asyncio
async def test_sec_18_resolve_user_approval_direct_fails_closed_on_missing_or_foreign(
    tmp_path: Path,
) -> None:
    env = await _setup_two_project_control_plane(tmp_path)
    sqlite_approvals = SqlitePendingApprovalStore(env["db_path"])
    await sqlite_approvals.initialize()
    cp = MyAgentControlPlane(
        task_store=env["tasks"],
        task_service=env["task_service"],
        plan_store=env["plans"],
        plan_service=PlanService(plan_store=env["plans"], task_store=env["tasks"]),
        decision_service=env["decision_service"],
        approval_store=sqlite_approvals,
    )

    await sqlite_approvals.save(
        {
            "tool_call_id": "call-beta-direct",
            "task_id": "task-beta-secret",
            "project_id": "proj-beta",
            "tool_name": "secret_op",
            "arguments": {},
        }
    )

    # Missing tool_call_id must raise KeyError, never report "resolved".
    with pytest.raises(KeyError, match="no pending approval"):
        await cp.resolve_user_approval("call-missing-direct", approved=True, project_id="proj-alpha")

    # Cross-project / cross-task scope mismatch fails closed atomically without mutating Beta's row.
    with pytest.raises(ValueError):
        await cp.resolve_user_approval("call-beta-direct", approved=True, project_id="proj-alpha")
    with pytest.raises(ValueError):
        await cp.resolve_user_approval("call-beta-direct", approved=True, task_id="task-alpha-1")

    beta_row = await sqlite_approvals.get("call-beta-direct")
    assert beta_row is not None and beta_row["status"] == "PENDING"
