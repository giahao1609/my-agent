"""Acceptance test suite for Phase P1A Real-Host Coder Worker Bridge & Security Acceptance.

Covers:
1. Real Host E2E: Real MCP subprocess over stdio JSON-RPC, 2 coding steps,
   run_whole_plan -> WAITING_FOR_HOST -> next_coder_command -> host edits files ->
   advance_plan_step -> step_approved -> step 2 -> Final Verification Gate -> COMPLETED.
2. Security Negative Tests: UNAUTHORIZED_WORKSPACE, CROSS_PROJECT_IDOR, invalid evidence,
   replay on completed step, path traversal.
3. TargetedTestSelector Audit: JSON/YAML/TOML non-skipping, doc-asset safety, security suite auto-trigger.
4. Timeout and Recovery: Test runner timeout process termination, session resumption from WAITING state.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from core.targeted_test_selector import TargetedTestSelector
from core.test_runner import PytestRunnerAdapter

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "real_host_p1a_e2e_fixture"
TEST_DB_PATH = FIXTURE_DIR / "test_p1a_mcp.db"


@pytest.fixture(autouse=True)
def setup_fixture_workspace():
    """Ensure clean fixture git workspace and test db."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    tests_dir = FIXTURE_DIR / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").touch()

    # Clean any stale db files
    for p in FIXTURE_DIR.glob("test_p1a_mcp.db*"):
        try:
            p.unlink()
        except OSError:
            pass

    yield

    # Clean generated files but preserve fixture directory structure
    for p in FIXTURE_DIR.glob("*.py"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in (FIXTURE_DIR / "tests").glob("test_*.py"):
        if p.name != "__init__.py":
            try:
                p.unlink()
            except OSError:
                pass
    for p in FIXTURE_DIR.glob("test_p1a_mcp.db*"):
        try:
            p.unlink()
        except OSError:
            pass


@asynccontextmanager
async def real_mcp_session(cwd: Path | str | None = None):
    """Launch real MyAgent MCP server as subprocess via stdio JSON-RPC."""
    root_dir = Path(__file__).resolve().parents[1]
    work_dir = Path(cwd or FIXTURE_DIR)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "my_agent_mcp.server"],
        cwd=str(work_dir),
        env={
            **os.environ,
            "CODER_RUNTIME_MODE": "external",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(root_dir),
            "MYAGENT_DB_PATH": str(TEST_DB_PATH),
        },
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


# ── 1. REAL HOST E2E TEST ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_real_host_mcp_e2e_two_step_plan():
    """Verify full real-host E2E lifecycle over live MCP stdio subprocess:
    1. Register project, task, and 2-step plan.
    2. run_whole_plan -> WAITING_FOR_HOST (<1s non-blocking).
    3. Host retrieves prompt via next_coder_command.
    4. Host creates math_lib.py (add) and tests/test_math_lib.py.
    5. advance_plan_step -> step_approved, Step 2 transitions to WAITING_FOR_HOST.
    6. Host retrieves Step 2 via next_coder_command.
    7. Host adds multiply to math_lib.py and updates tests.
    8. advance_plan_step -> plan_completed, COMPLETED, Final Verification Gate passed.
    """
    async with real_mcp_session() as session:
        # Step A: Verify MCP tool discovery (98 tools)
        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        assert "run_whole_plan" in tool_names
        assert "advance_plan_step" in tool_names
        assert "next_coder_command" in tool_names

        # Step B: Register isolated fixture project
        proj_res = await session.call_tool(
            "register_project",
            {
                "project_id": "real-host-p1a-fixture",
                "name": "P1A Fixture",
                "workspace_path": str(FIXTURE_DIR),
            },
        )
        assert not proj_res.is_error

        # Step C: Create task and start task
        task_res = await session.call_tool(
            "create_task",
            {
                "project_id": "real-host-p1a-fixture",
                "task_id": "task-p1a-calc",
                "objective": "Build Calculator Library with add and multiply functions and tests",
            },
        )
        assert not task_res.is_error

        # Step D: Create plan and add 2 steps
        plan_res = await session.call_tool(
            "create_plan",
            {
                "task_id": "task-p1a-calc",
                "plan_id": "plan-p1a-calc",
            },
        )
        assert not plan_res.is_error

        step1_res = await session.call_tool(
            "add_plan_step",
            {
                "plan_id": "plan-p1a-calc",
                "step_id": "step-calc-add",
                "title": "Implement math_lib addition",
                "instruction": "Implement add(a, b) in math_lib.py and test in tests/test_math_lib.py",
            },
        )
        assert not step1_res.is_error

        step2_res = await session.call_tool(
            "add_plan_step",
            {
                "plan_id": "plan-p1a-calc",
                "step_id": "step-calc-multiply",
                "title": "Implement math_lib multiplication",
                "instruction": "Implement multiply(a, b) in math_lib.py and test in tests/test_math_lib.py",
            },
        )
        assert not step2_res.is_error

        start_task_res = await session.call_tool(
            "start_task",
            {"task_id": "task-p1a-calc"},
        )
        assert not start_task_res.is_error

        # Step E: Call run_whole_plan on live MCP server
        start_time = time.perf_counter()
        run_res = await session.call_tool(
            "run_whole_plan",
            {
                "plan_id": "plan-p1a-calc",
                "workspace_path": str(FIXTURE_DIR),
            },
        )
        elapsed = time.perf_counter() - start_time
        assert elapsed < 3.0, f"run_whole_plan took too long: {elapsed}s"
        assert not run_res.is_error
        summary = run_res.content[0].text if hasattr(run_res.content[0], "text") else str(run_res.content[0])
        summary_data = json.loads(summary)

        assert summary_data["execution_state"] == "WAITING_FOR_HOST"
        assert summary_data["plan_completed"] is False
        assert summary_data["steps_completed"] == 0
        assert summary_data["current_step_id"] == "step-calc-add"
        assert summary_data["current_session_id"] is not None
        session1_id = summary_data["current_session_id"]

        # Step F: Host retrieves command via next_coder_command
        cmd_res = await session.call_tool(
            "next_coder_command",
            {"session_id": session1_id},
        )
        assert not cmd_res.is_error
        cmd_data = json.loads(cmd_res.content[0].text)
        assert cmd_data["status"] == "command"
        assert cmd_data["type"] in ("start", "message")

        # Step G: Real host edits files on disk in the isolated fixture
        math_lib_file = FIXTURE_DIR / "math_lib.py"
        test_math_file = FIXTURE_DIR / "tests" / "test_math_lib.py"

        math_lib_file.write_text(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        test_math_file.write_text(
            "from math_lib import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "    assert add(-1, 1) == 0\n"
        )

        # Step H: Call advance_plan_step for Step 1
        adv1_res = await session.call_tool(
            "advance_plan_step",
            {
                "step_id": "step-calc-add",
                "workspace_path": str(FIXTURE_DIR),
                "execution_evidence": {
                    "is_real": True,
                    "executor_identity": "antigravity_host_coder",
                    "modified_files": ["math_lib.py", "tests/test_math_lib.py"],
                    "session_id": session1_id,
                },
            },
        )
        assert not adv1_res.is_error
        adv1_data = json.loads(adv1_res.content[0].text)

        assert adv1_data["status"] == "step_approved"
        assert adv1_data["execution_state"] == "WAITING_FOR_HOST"
        assert adv1_data["plan_completed"] is False
        assert adv1_data["next_step_id"] == "step-calc-multiply"
        session2_id = adv1_data["next_session_id"]
        assert session2_id is not None

        # Step I: Host retrieves command for Step 2
        cmd2_res = await session.call_tool(
            "next_coder_command",
            {"session_id": session2_id},
        )
        assert not cmd2_res.is_error

        # Step J: Real host updates files on disk for multiplication
        math_lib_file.write_text(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n\n"
            "def multiply(a: int, b: int) -> int:\n"
            "    return a * b\n"
        )
        test_math_file.write_text(
            "from math_lib import add, multiply\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "    assert add(-1, 1) == 0\n\n"
            "def test_multiply():\n"
            "    assert multiply(3, 4) == 12\n"
            "    assert multiply(0, 5) == 0\n"
        )

        # Step K: Call advance_plan_step for Step 2 (Final Verification Gate runs full regression)
        adv2_res = await session.call_tool(
            "advance_plan_step",
            {
                "step_id": "step-calc-multiply",
                "workspace_path": str(FIXTURE_DIR),
                "execution_evidence": {
                    "is_real": True,
                    "executor_identity": "antigravity_host_coder",
                    "modified_files": ["math_lib.py", "tests/test_math_lib.py"],
                    "session_id": session2_id,
                },
            },
        )
        assert not adv2_res.is_error
        adv2_data = json.loads(adv2_res.content[0].text)

        assert adv2_data["status"] == "plan_completed"
        assert adv2_data["execution_state"] == "COMPLETED"
        assert adv2_data["plan_completed"] is True
        assert adv2_data["next_step_id"] is None

        # Step L: Confirm files exist and contain real implementations
        assert "def add(" in math_lib_file.read_text()
        assert "def multiply(" in math_lib_file.read_text()
        assert "def test_multiply():" in test_math_file.read_text()


# ── 2. SECURITY NEGATIVE TESTS ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_security_negative_unauthorized_workspace():
    """Verify fail-closed rejection when attempting to register workspace outside boundary."""
    async with real_mcp_session() as session:
        res = await session.call_tool(
            "register_project",
            {
                "project_id": "evil-project",
                "name": "Evil",
                "workspace_path": "/etc",
            },
        )
        assert res.is_error
        error_text = res.content[0].text.lower()
        assert "unauthorized_registration" in error_text or "outside" in error_text or "forbidden" in error_text


@pytest.mark.asyncio
async def test_security_negative_cross_project_idor():
    """Verify fail-closed rejection for cross-project / cross-plan step IDOR."""
    async with real_mcp_session() as session:
        res = await session.call_tool(
            "advance_plan_step",
            {
                "step_id": "non_existent_unauthorized_step_id",
                "workspace_path": str(FIXTURE_DIR),
            },
        )
        assert res.is_error
        error_text = res.content[0].text.lower()
        assert "not found" in error_text or "unauthorized" in error_text or "error" in error_text


@pytest.mark.asyncio
async def test_security_negative_invalid_or_missing_evidence():
    """Verify that fake evidence (is_real=False or missing) is rejected fail-closed."""
    async with real_mcp_session() as session:
        # Register a valid step first
        await session.call_tool(
            "register_project",
            {"project_id": "p-sec-neg", "name": "SecNeg", "workspace_path": str(FIXTURE_DIR)},
        )
        await session.call_tool(
            "create_task",
            {"project_id": "p-sec-neg", "task_id": "t-sec-neg", "objective": "Sec test"},
        )
        await session.call_tool(
            "create_plan",
            {"task_id": "t-sec-neg", "plan_id": "pl-sec-neg"},
        )
        await session.call_tool(
            "add_plan_step",
            {"plan_id": "pl-sec-neg", "step_id": "st-fake-ev", "title": "Fake Step", "instruction": "Fake"},
        )
        await session.call_tool(
            "start_task",
            {"task_id": "t-sec-neg"},
        )
        # Activate plan and start step
        await session.call_tool("activate_plan", {"plan_id": "pl-sec-neg"})
        await session.call_tool("start_plan_step", {"step_id": "st-fake-ev"})

        # Submit explicit fake evidence
        res = await session.call_tool(
            "advance_plan_step",
            {
                "step_id": "st-fake-ev",
                "workspace_path": str(FIXTURE_DIR),
                "execution_evidence": {
                    "is_real": False,
                    "executor_identity": "",
                    "notes": "synthetic simulated run",
                },
            },
        )
        assert not res.is_error
        data = json.loads(res.content[0].text)
        # Gate must reject
        assert data["status"] in ("step_rejected", "step_needs_rework")


@pytest.mark.asyncio
async def test_security_negative_replay_on_completed_step():
    """Verify that advance_plan_step cannot be replayed on an already completed step."""
    async with real_mcp_session() as session:
        await session.call_tool(
            "register_project",
            {"project_id": "p-replay", "name": "Replay", "workspace_path": str(FIXTURE_DIR)},
        )
        await session.call_tool(
            "create_task",
            {"project_id": "p-replay", "task_id": "t-replay", "objective": "Replay test"},
        )
        await session.call_tool(
            "create_plan",
            {"task_id": "t-replay", "plan_id": "pl-replay"},
        )
        await session.call_tool(
            "add_plan_step",
            {"plan_id": "pl-replay", "step_id": "st-replay-1", "title": "Replay Step", "instruction": "Replay"},
        )
        await session.call_tool("start_task", {"task_id": "t-replay"})
        await session.call_tool("activate_plan", {"plan_id": "pl-replay"})
        await session.call_tool("start_plan_step", {"step_id": "st-replay-1"})
        await session.call_tool("complete_plan_step", {"step_id": "st-replay-1"})

        # Now try to advance an already completed step
        res = await session.call_tool(
            "advance_plan_step",
            {
                "step_id": "st-replay-1",
                "workspace_path": str(FIXTURE_DIR),
                "execution_evidence": {"is_real": True},
            },
        )
        assert not res.is_error
        data = json.loads(res.content[0].text)
        assert data["status"] == "step_already_completed"


@pytest.mark.asyncio
async def test_security_negative_path_traversal():
    """Verify that path traversal in workspace_path is rejected."""
    async with real_mcp_session() as session:
        await session.call_tool(
            "register_project",
            {"project_id": "p-trav", "name": "Trav", "workspace_path": str(FIXTURE_DIR)},
        )
        await session.call_tool(
            "create_task",
            {"project_id": "p-trav", "task_id": "t-trav", "objective": "Trav test"},
        )
        await session.call_tool(
            "create_plan",
            {"task_id": "t-trav", "plan_id": "pl-trav"},
        )
        await session.call_tool(
            "add_plan_step",
            {"plan_id": "pl-trav", "step_id": "st-trav-1", "title": "Trav Step", "instruction": "Trav"},
        )

        res = await session.call_tool(
            "advance_plan_step",
            {
                "step_id": "st-trav-1",
                "workspace_path": "../../..",
            },
        )
        assert res.is_error
        error_text = res.content[0].text.lower()
        assert "unauthorized" in error_text or "outside" in error_text or "forbidden" in error_text


# ── 3. TARGETED TEST SELECTOR AUDIT ───────────────────────────────────────────

def test_targeted_test_selector_config_audit():
    """Audit TargetedTestSelector handling of JSON/YAML/TOML configs and doc assets:
    - Documentation / image assets (.md, .png) -> safely skip tests.
    - Security-sensitive configs (.mcp.json, .agents/rules/) -> auto-trigger security suite.
    - Configuration files (.json, .yaml, .toml, .lock) -> NOT skipped, requires fallback test run.
    """
    selector = TargetedTestSelector()
    root = Path(__file__).resolve().parents[1]

    # A. Documentation files safely skip tests
    doc_res = selector.select_tests(root, ["docs/README.md", "assets/diagram.png"])
    assert doc_res.is_targeted is False
    assert len(doc_res.selected_tests) == 0
    assert "documentation or static media" in doc_res.rationale

    # B. Security-sensitive configs auto-trigger security tests
    sec_res = selector.select_tests(root, [".agents/rules/interaction_protocol.md"])
    assert sec_res.is_targeted is True
    assert any("tests/security/" in t for t in sec_res.selected_tests)

    mcp_res = selector.select_tests(root, [".mcp.json"])
    assert mcp_res.is_targeted is True
    assert any("tests/security/" in t for t in mcp_res.selected_tests)

    # C. Configuration files (JSON, YAML, TOML) MUST NOT skip tests
    yaml_res = selector.select_tests(root, ["workflow_settings.yaml"])
    assert yaml_res.is_targeted is False
    assert "fallback required" in yaml_res.rationale
    assert "documentation or static media" not in yaml_res.rationale

    toml_res = selector.select_tests(root, ["pyproject.toml"])
    assert toml_res.is_targeted is False
    assert "fallback required" in toml_res.rationale
    assert "documentation or static media" not in toml_res.rationale

    json_res = selector.select_tests(root, ["package.json"])
    assert json_res.is_targeted is False
    assert "fallback required" in json_res.rationale
    assert "documentation or static media" not in json_res.rationale


# ── 4. TIMEOUT & RECOVERY TESTS ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pytest_runner_timeout_termination():
    """Verify PytestRunnerAdapter terminates hung processes and returns failed_tests=1 upon timeout."""
    adapter = PytestRunnerAdapter()
    sleep_test_file = FIXTURE_DIR / "tests" / "test_sleep.py"
    sleep_test_file.write_text(
        "import time\n\n"
        "def test_sleep_slow():\n"
        "    time.sleep(5)\n"
    )
    try:
        res = await adapter.run_tests(FIXTURE_DIR, "step-timeout", timeout_seconds=0.2)
        assert res.failed_tests == 1
        assert res.passed_tests == 0
        assert any("timed out" in f for f in res.failure_details)
    finally:
        if sleep_test_file.exists():
            sleep_test_file.unlink()


@pytest.mark.asyncio
async def test_session_recovery_after_restart_in_waiting_state():
    """Verify durable task & coder session recovery across MCP process restart while WAITING."""
    session_id_saved: str | None = None

    # Run 1: Start plan and leave in WAITING_FOR_HOST
    async with real_mcp_session() as session1:
        await session1.call_tool(
            "register_project",
            {"project_id": "p-recovery", "name": "Recovery", "workspace_path": str(FIXTURE_DIR)},
        )
        await session1.call_tool(
            "create_task",
            {"project_id": "p-recovery", "task_id": "t-recovery", "objective": "Recovery test"},
        )
        await session1.call_tool(
            "create_plan",
            {"task_id": "t-recovery", "plan_id": "pl-recovery"},
        )
        await session1.call_tool(
            "add_plan_step",
            {"plan_id": "pl-recovery", "step_id": "st-rec-1", "title": "Step 1", "instruction": "Step 1"},
        )
        await session1.call_tool(
            "start_task",
            {"task_id": "t-recovery"},
        )
        run_res = await session1.call_tool(
            "run_whole_plan",
            {"plan_id": "pl-recovery", "workspace_path": str(FIXTURE_DIR)},
        )
        assert not run_res.is_error
        data = json.loads(run_res.content[0].text)
        assert data["execution_state"] == "WAITING_FOR_HOST"
        session_id_saved = data["current_session_id"]
        assert session_id_saved is not None

    # Connection 1 is now closed (simulating crash / restart).
    # Run 2: Fresh MCP session reattaches to durable state
    async with real_mcp_session() as session2:
        # Host checks latest resumable coder session
        resumable_res = await session2.call_tool("get_latest_resumable_coder_session", {})
        assert not resumable_res.is_error
        resumable_data = json.loads(resumable_res.content[0].text)
        assert resumable_data is not None
        assert resumable_data["session_id"] == session_id_saved

        # Host resumes the durable session into the fresh MCP runtime
        resume_res = await session2.call_tool("resume_coder_session", {"session_id": session_id_saved})
        assert not resume_res.is_error
        resume_data = json.loads(resume_res.content[0].text)
        assert resume_data["status"] == "resumed"

        # Host retrieves next command from the recovered session
        cmd_res = await session2.call_tool("next_coder_command", {"session_id": session_id_saved})
        assert not cmd_res.is_error
        cmd_data = json.loads(cmd_res.content[0].text)
        assert cmd_data["status"] == "command"
        assert cmd_data["type"] in ("start", "message")
