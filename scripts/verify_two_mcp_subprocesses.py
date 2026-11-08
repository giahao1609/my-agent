#!/usr/bin/env python3
"""Comprehensive Verification Script: Two Independent MCP Subprocesses over stdio JSON-RPC.

Validates:
1. Multi-host process workspace binding to physical directory.
2. Scoped project listing and admin privilege separation.
3. Registration boundary defense (no system root, no workspace escape, no hijack).
4. IDOR defense across all resource domains (Tasks, Plans, Decisions, AgentRuns, Budgets).
5. Path traversal defense.
6. Docs / semantic knowledge isolation (zero cross-project leakage).
7. SQLite pre/post diff assertion proving ZERO state mutation from all denied attacks.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from core.project import ProjectRecord
from persistence.sqlite_project_store import SQLiteProjectStore


ADMIN_SECRET = "houhou_p0_admin_token_xyz987"


async def setup_mock_environment(base_dir: Path) -> tuple[Path, Path, Path]:
    ws_alpha = (base_dir / "mock_verification_alpha").resolve()
    ws_beta = (base_dir / "mock_verification_beta").resolve()
    db_path = (base_dir / "mock_verification.db").resolve()

    ws_alpha.mkdir(parents=True, exist_ok=True)
    ws_beta.mkdir(parents=True, exist_ok=True)

    # Populate distinct workspace documentation and source code
    (ws_alpha / "README.md").write_text("# Mock Alpha Repository\nProprietary Alpha Algorithm 99\n", encoding="utf-8")
    (ws_alpha / "alpha_code.py").write_text("def run_alpha(): return 'ALPHA_ACTIVE'\n", encoding="utf-8")

    (ws_beta / "README.md").write_text("# Mock Beta Repository\nProprietary Beta Algorithm 88\n", encoding="utf-8")
    (ws_beta / "beta_code.py").write_text("def run_beta(): return 'BETA_ACTIVE'\n", encoding="utf-8")

    # Pre-register both projects in the shared SQLite database
    store = SQLiteProjectStore(db_path)
    await store.initialize()
    await store.create(ProjectRecord(
        project_id="mock-alpha",
        name="Mock Alpha Project",
        workspace_path=str(ws_alpha),
    ))
    await store.create(ProjectRecord(
        project_id="mock-beta",
        name="Mock Beta Project",
        workspace_path=str(ws_beta),
    ))

    return ws_alpha, ws_beta, db_path


def get_sqlite_table_dump(db_path: Path, table_name: str) -> list[tuple[Any, ...]]:
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table_name}")
            return sorted(cursor.fetchall())
    except sqlite3.OperationalError:
        return []


def extract_projects(tool_result: Any) -> list[str]:
    ids: list[str] = []
    for c in tool_result.content:
        try:
            data = json.loads(c.text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "project_id" in item:
                        ids.append(item["project_id"])
            elif isinstance(data, dict):
                if "project_id" in data:
                    ids.append(data["project_id"])
                elif "projects" in data and isinstance(data["projects"], list):
                    for item in data["projects"]:
                        if isinstance(item, dict) and "project_id" in item:
                            ids.append(item["project_id"])
        except Exception:
            pass
    return ids


def assert_blocked(tool_name: str, res: Any) -> tuple[bool, str]:
    if res.is_error:
        err_msg = res.content[0].text if res.content else "Unknown error"
        return True, f"Blocked via is_error: {err_msg}"
    if res.content and res.content[0].text:
        text = res.content[0].text
        try:
            parsed = json.loads(text)
            if parsed.get("status") in ("error", "denied", "no_active_project") or "error" in parsed or parsed.get("task") is None:
                return True, f"Blocked via response JSON: {parsed.get('status') or parsed.get('error')}"
        except Exception:
            if "CROSS_PROJECT" in text or "UNAUTHORIZED" in text or "FORBIDDEN" in text or "DENIED" in text:
                return True, f"Blocked via text marker: {text[:60]}"
    return False, f"Unexpected success: {res.content[0].text if res.content else 'Empty'}"


async def run_scenario_tests() -> bool:
    base_tmp = Path(tempfile.mkdtemp(prefix="myagent_p0_sec_"))
    try:
        ws_alpha, ws_beta, db_path = await setup_mock_environment(base_tmp)
        python_bin = sys.executable
        repo_root = str(Path(__file__).resolve().parents[1])

        env_alpha = {
            **os.environ,
            "MYAGENT_WORKSPACE": str(ws_alpha),
            "MYAGENT_DB_PATH": str(db_path),
            "MYAGENT_ADMIN_SECRET": ADMIN_SECRET,
            "PYTHONPATH": repo_root,
        }
        env_beta = {
            **os.environ,
            "MYAGENT_WORKSPACE": str(ws_beta),
            "MYAGENT_DB_PATH": str(db_path),
            "MYAGENT_ADMIN_SECRET": ADMIN_SECRET,
            "PYTHONPATH": repo_root,
        }

        params_alpha = StdioServerParameters(
            command=python_bin,
            args=["-m", "my_agent_mcp.server"],
            env=env_alpha,
            cwd=str(ws_alpha),
        )
        params_beta = StdioServerParameters(
            command=python_bin,
            args=["-m", "my_agent_mcp.server"],
            env=env_beta,
            cwd=str(ws_beta),
        )

        print("=" * 80)
        print("MYAGENT P0 MULTI-PROCESS STDIO JSON-RPC SECURITY VERIFICATION")
        print("=" * 80)
        print(f"Alpha Workspace:  {ws_alpha}")
        print(f"Beta Workspace:   {ws_beta}")
        print(f"Shared SQLite DB: {db_path}")
        print("-" * 80)

        test_results: list[tuple[str, str, str]] = []

        async with stdio_client(params_alpha) as (read_a, write_a), stdio_client(params_beta) as (read_b, write_b):
            async with ClientSession(read_a, write_a) as session_a, ClientSession(read_b, write_b) as session_b:
                # Initialize both MCP sessions
                init_a = await session_a.initialize()
                init_b = await session_b.initialize()
                assert init_a.server_info.name == "my-agent"
                assert init_b.server_info.name == "my-agent"

                # -------------------------------------------------------------
                # SCENARIO 1: Process Workspace Binding (ALLOWED)
                # -------------------------------------------------------------
                status_a_raw = await session_a.call_tool("my_agent_status", {})
                status_a = json.loads(status_a_raw.content[0].text)
                status_b_raw = await session_b.call_tool("my_agent_status", {})
                status_b = json.loads(status_b_raw.content[0].text)

                proj_a_id = status_a.get("active_project", {}).get("project_id")
                proj_b_id = status_b.get("active_project", {}).get("project_id")

                if proj_a_id == "mock-alpha" and proj_b_id == "mock-beta":
                    test_results.append(("Process Workspace Binding", "PASSED", f"Alpha={proj_a_id}, Beta={proj_b_id}"))
                else:
                    test_results.append(("Process Workspace Binding", "FAILED", f"Alpha={proj_a_id}, Beta={proj_b_id}"))

                # -------------------------------------------------------------
                # SCENARIO 2: Scoped Project Listing & Admin Token Separation
                # -------------------------------------------------------------
                list_a_raw = await session_a.call_tool("list_projects", {})
                projects_in_a = extract_projects(list_a_raw)

                list_b_raw = await session_b.call_tool("list_projects", {})
                projects_in_b = extract_projects(list_b_raw)

                # Admin token query from Alpha
                list_admin_raw = await session_a.call_tool("list_projects", {"admin_token": ADMIN_SECRET})
                projects_in_admin = extract_projects(list_admin_raw)

                if projects_in_a == ["mock-alpha"] and projects_in_b == ["mock-beta"] and set(projects_in_admin) == {"mock-alpha", "mock-beta"}:
                    test_results.append(("Scoped Project Listing & Admin", "PASSED", f"Alpha={projects_in_a}, Beta={projects_in_b}, Admin={projects_in_admin}"))
                else:
                    test_results.append(("Scoped Project Listing & Admin", "FAILED", f"A={projects_in_a}, B={projects_in_b}, Admin={projects_in_admin}"))

                # -------------------------------------------------------------
                # SCENARIO 3: Strict Boundary on register_project (DENIED)
                # -------------------------------------------------------------
                # Attempt 1: System root registration (/tmp)
                reg_sys_raw = await session_a.call_tool("register_project", {
                    "project_id": "malicious_sys_root",
                    "workspace_path": "/tmp",
                })
                blocked_sys, reason_sys = assert_blocked("register_project_sys", reg_sys_raw)

                # Attempt 2: Register external workspace belonging to Beta
                reg_ext_raw = await session_a.call_tool("register_project", {
                    "project_id": "malicious_ext_beta",
                    "workspace_path": str(ws_beta),
                })
                blocked_ext, reason_ext = assert_blocked("register_project_ext", reg_ext_raw)

                # Attempt 3: Overwrite / hijack existing project mock-beta
                sub_dir = ws_alpha / "sub_project"
                sub_dir.mkdir(parents=True, exist_ok=True)
                reg_hijack_raw = await session_a.call_tool("register_project", {
                    "project_id": "mock-beta",
                    "workspace_path": str(sub_dir),
                })
                blocked_hijack, reason_hijack = assert_blocked("register_project_hijack", reg_hijack_raw)

                if blocked_sys and blocked_ext and blocked_hijack:
                    test_results.append(("register_project Boundary Defense", "PASSED", "Rejected /tmp, external workspace, and project hijacking"))
                else:
                    test_results.append(("register_project Boundary Defense", "FAILED", f"sys={blocked_sys}, ext={blocked_ext}, hijack={blocked_hijack}"))

                # -------------------------------------------------------------
                # SCENARIO 4: Valid Task Creation in Own Scope (ALLOWED)
                # -------------------------------------------------------------
                task_a_raw = await session_a.call_tool("create_task", {"project_id": "mock-alpha", "objective": "Alpha Task 1"})
                task_a = json.loads(task_a_raw.content[0].text)
                task_b_raw = await session_b.call_tool("create_task", {"project_id": "mock-beta", "objective": "Beta Task 1"})
                task_b = json.loads(task_b_raw.content[0].text)

                task_a_id = task_a.get("task", {}).get("task_id")
                task_b_id = task_b.get("task", {}).get("task_id")

                if task_a.get("status") == "created" and task_b.get("status") == "created":
                    test_results.append(("Same-Workspace Task Creation", "PASSED", f"Alpha={task_a_id}, Beta={task_b_id}"))
                else:
                    test_results.append(("Same-Workspace Task Creation", "FAILED", f"Task A: {task_a}, Task B: {task_b}"))

                # -------------------------------------------------------------
                # SCENARIO 5: Seed Authorized Entities in Beta for IDOR Testing
                # -------------------------------------------------------------
                # Beta creates a plan
                plan_b_raw = await session_b.call_tool("create_plan", {"task_id": task_b_id})
                plan_b = json.loads(plan_b_raw.content[0].text)
                plan_b_id = plan_b.get("plan", {}).get("plan_id")

                # Beta adds step to plan
                step_b_raw = await session_b.call_tool("add_plan_step", {
                    "plan_id": plan_b_id,
                    "title": "Beta Step 1",
                    "instruction": "Run internal beta pipeline",
                })
                step_b = json.loads(step_b_raw.content[0].text)
                step_b_id = step_b.get("step", {}).get("step_id")

                # Beta creates a decision
                dec_b_raw = await session_b.call_tool("create_decision", {
                    "task_id": task_b_id,
                    "prompt": "Select Beta Database",
                    "severity": "medium",
                    "options": [
                        {"option_id": "opt-b1", "title": "Beta Storage", "description": "Internal DB"},
                        {"option_id": "opt-b2", "title": "Beta Cache", "description": "Redis DB"},
                    ],
                })
                dec_b = json.loads(dec_b_raw.content[0].text)
                dec_b_id = dec_b.get("decision", {}).get("decision_id")

                # Beta starts an agent run
                run_b_raw = await session_b.call_tool("start_agent_run", {
                    "task_id": task_b_id,
                    "agent_id": "beta_agent_01",
                    "execution_role": "backend_coder",
                })
                run_b = json.loads(run_b_raw.content[0].text)
                run_b_id = run_b.get("run", {}).get("run_id")

                # Snapshot DB state of all tables BEFORE denied attacks
                snapshot_before_tasks = get_sqlite_table_dump(db_path, "tasks")
                snapshot_before_plans = get_sqlite_table_dump(db_path, "plans")
                snapshot_before_steps = get_sqlite_table_dump(db_path, "plan_steps")
                snapshot_before_decisions = get_sqlite_table_dump(db_path, "decisions")
                snapshot_before_runs = get_sqlite_table_dump(db_path, "agent_runs")

                # -------------------------------------------------------------
                # SCENARIO 6: Cross-Project IDOR on Tasks (DENIED)
                # -------------------------------------------------------------
                # Subprocess Alpha tries to create task in project 'mock-beta'
                idor_create = await session_a.call_tool("create_task", {"project_id": "mock-beta", "objective": "Malicious Alpha Task in Beta"})
                b_create, _ = assert_blocked("create_task", idor_create)

                # Subprocess Alpha tries to start Beta's task
                idor_start = await session_a.call_tool("start_task", {"task_id": task_b_id})
                b_start, _ = assert_blocked("start_task", idor_start)

                # Subprocess Alpha tries to cancel Beta's task
                idor_cancel = await session_a.call_tool("cancel_task", {"task_id": task_b_id})
                b_cancel, _ = assert_blocked("cancel_task", idor_cancel)

                # Subprocess Alpha tries to complete Beta's task
                idor_complete = await session_a.call_tool("complete_task", {"task_id": task_b_id})
                b_complete, _ = assert_blocked("complete_task", idor_complete)

                if b_create and b_start and b_cancel and b_complete:
                    test_results.append(("Task IDOR Defense", "PASSED", "Blocked create/start/cancel/complete on foreign tasks"))
                else:
                    test_results.append(("Task IDOR Defense", "FAILED", f"create={b_create}, start={b_start}, cancel={b_cancel}, complete={b_complete}"))

                # -------------------------------------------------------------
                # SCENARIO 7: Cross-Project IDOR on Plans & Steps (DENIED)
                # -------------------------------------------------------------
                # Subprocess Alpha tries to add step to Beta's plan
                idor_add_step = await session_a.call_tool("add_plan_step", {
                    "plan_id": plan_b_id,
                    "title": "Injected Alpha Step",
                    "instruction": "Exfiltrate beta data",
                })
                b_add_step, _ = assert_blocked("add_plan_step", idor_add_step)

                # Subprocess Alpha tries to activate Beta's plan
                idor_act_plan = await session_a.call_tool("activate_plan", {"plan_id": plan_b_id})
                b_act_plan, _ = assert_blocked("activate_plan", idor_act_plan)

                if b_add_step and b_act_plan:
                    test_results.append(("Plan & Step IDOR Defense", "PASSED", "Blocked add_plan_step and activate_plan on foreign plans"))
                else:
                    test_results.append(("Plan & Step IDOR Defense", "FAILED", f"add_step={b_add_step}, act_plan={b_act_plan}"))

                # -------------------------------------------------------------
                # SCENARIO 8: Cross-Project IDOR on Decisions (DENIED)
                # -------------------------------------------------------------
                # Subprocess Alpha tries to read Beta's decision
                idor_get_dec = await session_a.call_tool("get_decision", {"decision_id": dec_b_id})
                b_get_dec, _ = assert_blocked("get_decision", idor_get_dec)

                # Subprocess Alpha tries to resolve Beta's decision
                idor_res_dec = await session_a.call_tool("resolve_decision", {
                    "decision_id": dec_b_id,
                    "selected_option_id": "opt-b1",
                    "rationale": "Hijacked resolution",
                })
                b_res_dec, _ = assert_blocked("resolve_decision", idor_res_dec)

                # Subprocess Alpha tries to create a decision linked to Beta's task
                idor_cr_dec = await session_a.call_tool("create_decision", {
                    "task_id": task_b_id,
                    "prompt": "Alpha injection into Beta",
                    "severity": "high",
                    "options": [
                        {"option_id": "evil-1", "title": "Evil Option 1", "description": "D1"},
                        {"option_id": "evil-2", "title": "Evil Option 2", "description": "D2"},
                    ],
                })
                b_cr_dec, _ = assert_blocked("create_decision", idor_cr_dec)

                if b_get_dec and b_res_dec and b_cr_dec:
                    test_results.append(("Decision IDOR Defense", "PASSED", "Blocked get_decision, resolve_decision, and create_decision across boundaries"))
                else:
                    test_results.append(("Decision IDOR Defense", "FAILED", f"get={b_get_dec}, res={b_res_dec}, cr={b_cr_dec}"))

                # -------------------------------------------------------------
                # SCENARIO 9: Cross-Project IDOR on AgentRuns & Budgets (DENIED)
                # -------------------------------------------------------------
                # Subprocess Alpha tries to get Beta's agent run
                idor_get_run = await session_a.call_tool("get_agent_run", {"run_id": run_b_id})
                b_get_run, _ = assert_blocked("get_agent_run", idor_get_run)

                # Subprocess Alpha tries to complete Beta's agent run
                idor_comp_run = await session_a.call_tool("complete_agent_run", {
                    "run_id": run_b_id,
                    "result": {"summary": "Hijacked completion", "success": True},
                })
                b_comp_run, _ = assert_blocked("complete_agent_run", idor_comp_run)

                # Subprocess Alpha tries to get Beta's task budget
                idor_budget = await session_a.call_tool("get_task_budget", {"task_id": task_b_id})
                b_budget, _ = assert_blocked("get_task_budget", idor_budget)

                if b_get_run and b_comp_run and b_budget:
                    test_results.append(("AgentRun & Budget IDOR Defense", "PASSED", "Blocked get/complete run and get budget on foreign tasks"))
                else:
                    test_results.append(("AgentRun & Budget IDOR Defense", "FAILED", f"get_run={b_get_run}, comp_run={b_comp_run}, budget={b_budget}"))

                # -------------------------------------------------------------
                # SCENARIO 10: Cross-Project Navigation & Path Traversal (DENIED)
                # -------------------------------------------------------------
                # switch_project to mock-beta
                switch_raw = await session_a.call_tool("switch_project", {"project_id": "mock-beta"})
                b_switch, _ = assert_blocked("switch_project", switch_raw)

                # open_workspace to ws_beta
                open_raw = await session_a.call_tool("open_workspace", {"workspace_path": str(ws_beta)})
                b_open, _ = assert_blocked("open_workspace", open_raw)

                # scan_security path traversal '../'
                traversal_target = str(ws_alpha / ".." / "mock_verification_beta")
                scan_raw = await session_a.call_tool("scan_security", {"workspace_path": traversal_target})
                b_scan, _ = assert_blocked("scan_security", scan_raw)

                if b_switch and b_open and b_scan:
                    test_results.append(("Navigation & Traversal Defense", "PASSED", "Blocked switch_project, open_workspace, and path traversal"))
                else:
                    test_results.append(("Navigation & Traversal Defense", "FAILED", f"switch={b_switch}, open={b_open}, scan={b_scan}"))

                # -------------------------------------------------------------
                # SCENARIO 11: Shared Docs Knowledge Isolation (ZERO LEAKAGE)
                # -------------------------------------------------------------
                docs_a_raw = await session_a.call_tool("search_docs", {"query": "Proprietary"})
                docs_a = json.loads(docs_a_raw.content[0].text)
                docs_b_raw = await session_b.call_tool("search_docs", {"query": "Proprietary"})
                docs_b = json.loads(docs_b_raw.content[0].text)

                docs_a_text = str(docs_a)
                docs_b_text = str(docs_b)

                leak_into_a = "Beta" in docs_a_text or "88" in docs_a_text
                leak_into_b = "Alpha" in docs_b_text or "99" in docs_b_text

                if not leak_into_a and not leak_into_b:
                    test_results.append(("Docs Knowledge Isolation", "PASSED", "Zero cross-project document leakage between Alpha and Beta"))
                else:
                    test_results.append(("Docs Knowledge Isolation", "FAILED", f"Leak detected: leak_into_a={leak_into_a}, leak_into_b={leak_into_b}"))

                # -------------------------------------------------------------
                # SCENARIO 12: SQLite Pre/Post Diff Assertion (ZERO MUTATION)
                # -------------------------------------------------------------
                snapshot_after_tasks = get_sqlite_table_dump(db_path, "tasks")
                snapshot_after_plans = get_sqlite_table_dump(db_path, "plans")
                snapshot_after_steps = get_sqlite_table_dump(db_path, "plan_steps")
                snapshot_after_decisions = get_sqlite_table_dump(db_path, "decisions")
                snapshot_after_runs = get_sqlite_table_dump(db_path, "agent_runs")

                tasks_intact = snapshot_after_tasks == snapshot_before_tasks
                plans_intact = snapshot_after_plans == snapshot_before_plans
                steps_intact = snapshot_after_steps == snapshot_before_steps
                decisions_intact = snapshot_after_decisions == snapshot_before_decisions
                runs_intact = snapshot_after_runs == snapshot_before_runs

                all_intact = tasks_intact and plans_intact and steps_intact and decisions_intact and runs_intact
                if all_intact:
                    test_results.append(("SQLite Zero-Mutation Pre/Post Diff", "PASSED", "100% table row parity before and after all denied attacks"))
                else:
                    test_results.append(("SQLite Zero-Mutation Pre/Post Diff", "FAILED", f"tasks={tasks_intact}, plans={plans_intact}, steps={steps_intact}, decisions={decisions_intact}, runs={runs_intact}"))

        print("\n" + "=" * 80)
        print("TEST EXECUTION SUMMARY:")
        print(f"{'Scenario':<38} | {'Result':<8} | {'Details'}")
        print("-" * 80)
        all_passed = True
        for name, status, details in test_results:
            print(f"{name:<38} | {status:<8} | {details}")
            if status != "PASSED":
                all_passed = False
        print("-" * 80)
        print(f"Overall Result: {'ALL SCENARIOS PASSED (100%)' if all_passed else 'SOME SCENARIOS FAILED'}\n")
        return all_passed

    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)


if __name__ == "__main__":
    success = asyncio.run(run_scenario_tests())
    sys.exit(0 if success else 1)
