from __future__ import annotations

from pathlib import Path

import pytest

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


def test_pytest_collection_error_never_reports_pass() -> None:
    """Pytest collection error with exit code 2 must never report pass."""
    output = "ERROR collecting tests/test_broken.py\nSyntaxError: invalid syntax\n1 error in 0.05s"
    res = PytestRunnerAdapter._parse_pytest_output("step-col", output, exit_code=2)
    assert res.success is False
    assert res.failed_tests >= 1
    assert any("ERROR" in f for f in res.failure_details)


def test_pytest_nonzero_exit_code_with_passed_tests_never_reports_pass() -> None:
    """When some tests passed but exit code != 0, it must fail closed and never report pass."""
    output = "5 passed in 0.12s\nERROR: Fixture teardown failed"
    res = PytestRunnerAdapter._parse_pytest_output("step-err", output, exit_code=1)
    assert res.success is False
    assert res.passed_tests == 5
    assert res.failed_tests >= 1
    assert any("ERROR" in f for f in res.failure_details)


def test_pytest_crash_or_usage_error_never_reports_pass() -> None:
    """Arbitrary non-zero exit code without summary line must fail closed and record exit code."""
    output = "Fatal Python error: SIGKILL\n[1] 12345 killed"
    res = PytestRunnerAdapter._parse_pytest_output("step-crash", output, exit_code=137)
    assert res.success is False
    assert res.failed_tests >= 1
    assert any("exit code 137" in f for f in res.failure_details)


@pytest.mark.asyncio
async def test_test_runner_registry_run_tests(tmp_path: Path) -> None:
    registry = TestRunnerRegistry()
    # Path without indicator must fail closed
    res = await registry.run_tests(tmp_path, "step-1")
    assert res.step_id == "step-1"
    assert res.failed_tests == 1
    assert res.execution_success is False
    assert res.success is False
    assert "No supported test runner detected" in res.failure_details[0]


def test_go_runner_pass_exit_0_success() -> None:
    """Go tests passing with exit code 0 must report success."""
    output = "--- PASS: TestAdd (0.01s)\n--- PASS: TestSub (0.02s)\nPASS\nok  \tmy/pkg\t0.035s"
    res = GoTestRunnerAdapter._parse_go_output("step-go-pass", output, exit_code=0)
    assert res.passed_tests == 2
    assert res.failed_tests == 0
    assert res.execution_success is True
    assert res.success is True


def test_go_runner_pass_with_nonzero_exit_fails() -> None:
    """Go tests reporting PASS but ending with exit code != 0 must fail closed."""
    output = "--- PASS: TestAdd (0.01s)\nFAIL"
    res = GoTestRunnerAdapter._parse_go_output("step-go-fail", output, exit_code=1)
    assert res.passed_tests == 1
    assert res.failed_tests >= 1
    assert res.execution_success is False
    assert res.success is False


def test_go_runner_build_failure_fails() -> None:
    """Go build failure with exit code 2 must fail closed without phantom passed tests."""
    output = "# my/pkg\n./app.go:5:2: undefined: nonExistentFunc\nFAIL\tmy/pkg [build failed]"
    res = GoTestRunnerAdapter._parse_go_output("step-go-build", output, exit_code=2)
    assert res.passed_tests == 0
    assert res.failed_tests >= 1
    assert res.execution_success is False
    assert res.success is False
    assert any("[build failed]" in f for f in res.failure_details)


def test_go_runner_panic_fails() -> None:
    """Go test panic must fail closed and record panic detail."""
    output = "--- PASS: TestOne (0.01s)\npanic: runtime error: index out of range\nFAIL\tmy/pkg\t0.02s"
    res = GoTestRunnerAdapter._parse_go_output("step-go-panic", output, exit_code=2)
    assert res.failed_tests >= 1
    assert res.execution_success is False
    assert res.success is False
    assert any("panic:" in f for f in res.failure_details)


def test_go_runner_no_tests_exit_0_does_not_invent_pass() -> None:
    """Go package with [no test files] and exit code 0 must have 0 passed tests, not phantom 1."""
    output = "?   \tmy/pkg\t[no test files]"
    res = GoTestRunnerAdapter._parse_go_output("step-go-empty", output, exit_code=0)
    assert res.passed_tests == 0
    assert res.failed_tests == 0
    assert res.total_tests == 0
    assert res.execution_success is True
    assert res.success is True


def test_go_runner_package_ok_exit_0_success() -> None:
    """Go package reporting ok without individual test lines must count package as passed."""
    output = "ok  \tmy/pkg\t0.045s\nok  \tmy/pkg2\t0.012s"
    res = GoTestRunnerAdapter._parse_go_output("step-go-pkg", output, exit_code=0)
    assert res.passed_tests == 2
    assert res.failed_tests == 0
    assert res.execution_success is True
    assert res.success is True


@pytest.mark.asyncio
async def test_pytest_runner_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PytestRunnerAdapter()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    import asyncio

    # Simulate timeout by having communicate sleep
    async def slow_communicate(*args, **kwargs):
        await asyncio.sleep(2.0)
        return b"", b""

    class FakeProc:
        returncode = None
        def kill(self): pass
        async def wait(self): pass
        communicate = slow_communicate

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    res = await adapter.run_tests(
        tmp_path,
        "step-timeout",
        timeout_seconds=0.05,
    )
    assert res.failed_tests == 1
    assert "timed out after" in res.failure_details[0]


@pytest.mark.asyncio
async def test_pytest_runner_with_target_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PytestRunnerAdapter()
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    import asyncio

    executed_cmd = []

    class FakeProc:
        returncode = 0
        async def communicate(self):
            return b"1 passed in 0.05s", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        executed_cmd.extend(args)
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    res = await adapter.run_tests(
        tmp_path,
        "step-target",
        target_files=["tests/test_foo.py", "tests/test_bar.py"],
    )
    assert res.passed_tests == 1
    assert "tests/test_foo.py" in executed_cmd
    assert "tests/test_bar.py" in executed_cmd
