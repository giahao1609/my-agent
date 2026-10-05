from __future__ import annotations

from pathlib import Path
import pytest

from core.goal_drift_monitor import GoalDriftMonitor
from core.repo_map import RepoMapCompactor
from my_agent_mcp import server


def test_repo_map_extraction(tmp_path: Path) -> None:
    # Create sample python file
    py_code = (
        "class UserService:\n"
        "    def __init__(self, db_conn):\n"
        "        pass\n"
        "    def get_user(self, user_id: str) -> dict:\n"
        "        return {}\n"
        "\n"
        "def compute_hash(data: str) -> str:\n"
        "    return 'hash'\n"
    )
    (tmp_path / "user_service.py").write_text(py_code, encoding="utf-8")

    # Create ignored file
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "ignored.py").write_text("def should_not_appear(): pass", encoding="utf-8")

    summary = RepoMapCompactor.generate_repo_map(tmp_path, max_tokens=500)

    assert summary.files_indexed == 1
    assert "user_service.py" in summary.repo_map
    assert "class UserService" in summary.repo_map
    assert "def get_user" in summary.repo_map
    assert "def compute_hash" in summary.repo_map
    assert "should_not_appear" not in summary.repo_map
    assert summary.token_count_estimate > 0


def test_repo_map_token_budget_truncation(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"module_{i}.py").write_text(
            f"class Service{i}:\n    def do_work_{i}(self):\n        pass\n",
            encoding="utf-8",
        )

    # Budget of only 50 tokens (~200 chars) -> should truncate
    summary = RepoMapCompactor.generate_repo_map(tmp_path, max_tokens=50)
    assert summary.token_count_estimate <= 60  # close to budget
    assert "truncated" in summary.repo_map.lower() or "..." in summary.repo_map


def test_goal_drift_monitor_valid_scope() -> None:
    modified = ["core/app.py", "core/utils.py"]
    allowed = ["core/*"]

    res = GoalDriftMonitor.evaluate_drift("step-1", modified, allowed_paths=allowed)
    assert res.passed is True
    assert res.drift_detected is False
    assert len(res.out_of_scope_files) == 0
    assert len(res.violations) == 0


def test_goal_drift_monitor_out_of_scope() -> None:
    modified = ["core/app.py", "backend/unrelated.py"]
    allowed = ["core/*"]

    res = GoalDriftMonitor.evaluate_drift("step-2", modified, allowed_paths=allowed)
    assert res.passed is False
    assert res.drift_detected is True
    assert "backend/unrelated.py" in res.out_of_scope_files


def test_goal_drift_monitor_invariant_violations() -> None:
    # 1. Modifying .env file
    res_env = GoalDriftMonitor.evaluate_drift("step-3", ["config/.env.production"])
    assert res_env.passed is False
    assert res_env.drift_detected is True
    assert any(".env" in v for v in res_env.violations)

    # 2. Modifying upstream reference repo
    res_upstream = GoalDriftMonitor.evaluate_drift("step-4", ["/upstreams/reference-repo/main.go"])
    assert res_upstream.passed is False
    assert res_upstream.drift_detected is True
    assert any("upstream" in v.lower() for v in res_upstream.violations)


@pytest.mark.asyncio
async def test_mcp_repo_map_and_drift_tools(tmp_path: Path) -> None:
    # 1. get_repo_map tool
    (tmp_path / "main.py").write_text("def start_app(): pass\n", encoding="utf-8")
    map_res = await server.get_repo_map(str(tmp_path), max_tokens=100)
    assert "repo_map" in map_res
    assert "start_app" in str(map_res["repo_map"])

    # 2. check_goal_drift tool
    drift_res = await server.check_goal_drift(
        step_id="step-mcp",
        modified_files=["core/allowed.py", "secrets.key"],
        allowed_paths=["core/*"],
    )
    assert drift_res["passed"] is False
    assert drift_res["drift_detected"] is True
