from __future__ import annotations

import pytest
from pathlib import Path

from core.test_runner import (
    GoTestRunnerAdapter,
    NpmTestRunnerAdapter,
    PytestRunnerAdapter,
    TestRunnerRegistry,
)


def test_test_runner_detection(tmp_path: Path) -> None:
    registry = TestRunnerRegistry()

    # Pytest detection
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    adapter = registry.detect_adapter(tmp_path)
    assert isinstance(adapter, PytestRunnerAdapter)

    # Clean up pytest indicator
    (tmp_path / "pyproject.toml").unlink()

    # Npm detection
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    adapter_npm = registry.detect_adapter(tmp_path)
    assert isinstance(adapter_npm, NpmTestRunnerAdapter)

    (tmp_path / "package.json").unlink()

    # Go detection
    (tmp_path / "go.mod").write_text("module test", encoding="utf-8")
    adapter_go = registry.detect_adapter(tmp_path)
    assert isinstance(adapter_go, GoTestRunnerAdapter)


def test_pytest_output_parsing() -> None:
    output_pass = "340 passed in 6.04s"
    res_pass = PytestRunnerAdapter._parse_pytest_output("step-1", output_pass, 0)
    assert res_pass.passed_tests == 340
    assert res_pass.failed_tests == 0
    assert res_pass.success is True

    output_fail = "FAILED tests/test_foo.py::test_bar - ValueError: bad input\n1 failed, 338 passed in 6.24s"
    res_fail = PytestRunnerAdapter._parse_pytest_output("step-1", output_fail, 1)
    assert res_fail.passed_tests == 338
    assert res_fail.failed_tests == 1
    assert res_fail.success is False
    assert len(res_fail.failure_details) > 0


@pytest.mark.asyncio
async def test_test_runner_registry_run_tests(tmp_path: Path) -> None:
    registry = TestRunnerRegistry()
    # Path without indicator returns empty test result with details
    res = await registry.run_tests(tmp_path, "step-1")
    assert res.step_id == "step-1"
    assert "No supported test runner detected" in res.failure_details[0]
