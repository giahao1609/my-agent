from __future__ import annotations

import asyncio
import re

from pathlib import Path
from typing import Protocol

from .handoff_contracts import TestResult


class TestRunnerAdapter(Protocol):
    def detect(self, workspace_path: Path) -> bool: ...

    async def run_tests(
        self,
        workspace_path: Path,
        step_id: str,
    ) -> TestResult: ...


class PytestRunnerAdapter:
    def detect(self, workspace_path: Path) -> bool:
        return (
            (workspace_path / "pytest.ini").exists()
            or (workspace_path / "pyproject.toml").exists()
            or (workspace_path / "conftest.py").exists()
            or (workspace_path / "tests").exists()
        )

    async def run_tests(
        self,
        workspace_path: Path,
        step_id: str,
    ) -> TestResult:
        python_bin = workspace_path / ".venv" / "bin" / "pytest"
        cmd = [str(python_bin) if python_bin.exists() else "pytest"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
            return self._parse_pytest_output(step_id, output, proc.returncode or 0)
        except Exception as e:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Failed to execute pytest: {e}",),
            )

    @staticmethod
    def _parse_pytest_output(step_id: str, output: str, exit_code: int) -> TestResult:
        # Match lines like "340 passed in 6.04s" or "1 failed, 338 passed in 6.24s"
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else (1 if exit_code != 0 and passed == 0 else 0)
        total = passed + failed

        failures = []
        if failed > 0:
            for line in output.splitlines():
                if "FAILED" in line or "Error:" in line or "E   " in line:
                    failures.append(line.strip())

        cov_match = re.search(r"TOTAL\s+.*\s+(\d+(?:\.\d+)?)%", output)
        if not cov_match:
            cov_match = re.search(r"(\d+(?:\.\d+)?)%\s+coverage", output, re.IGNORECASE)
        coverage = float(cov_match.group(1)) if cov_match else None

        return TestResult(
            step_id=step_id,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            failure_details=tuple(failures[:10]),
            coverage_percentage=coverage,
        )


class NpmTestRunnerAdapter:
    def detect(self, workspace_path: Path) -> bool:
        return (workspace_path / "package.json").exists()

    async def run_tests(
        self,
        workspace_path: Path,
        step_id: str,
    ) -> TestResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "test", "--", "--run",
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
            return self._parse_npm_output(step_id, output, proc.returncode or 0)
        except Exception as e:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Failed to execute npm test: {e}",),
            )

    @staticmethod
    def _parse_npm_output(step_id: str, output: str, exit_code: int) -> TestResult:
        passed_match = re.search(r"(\d+)\s+passed", output, re.IGNORECASE)
        failed_match = re.search(r"(\d+)\s+failed", output, re.IGNORECASE)
        passed = int(passed_match.group(1)) if passed_match else (1 if exit_code == 0 else 0)
        failed = int(failed_match.group(1)) if failed_match else (1 if exit_code != 0 else 0)
        total = passed + failed

        cov_match = re.search(r"All files\s*\|\s*(\d+(?:\.\d+)?)", output)
        if not cov_match:
            cov_match = re.search(r"Coverage:\s*(\d+(?:\.\d+)?)%", output, re.IGNORECASE)
        coverage = float(cov_match.group(1)) if cov_match else None

        return TestResult(
            step_id=step_id,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            failure_details=tuple(line.strip() for line in output.splitlines() if "FAIL" in line or "Error:" in line)[:10],
            coverage_percentage=coverage,
        )


class GoTestRunnerAdapter:
    def detect(self, workspace_path: Path) -> bool:
        return (workspace_path / "go.mod").exists()

    async def run_tests(
        self,
        workspace_path: Path,
        step_id: str,
    ) -> TestResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "go", "test", "./...",
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
            return self._parse_go_output(step_id, output, proc.returncode or 0)
        except Exception as e:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Failed to execute go test: {e}",),
            )

    @staticmethod
    def _parse_go_output(step_id: str, output: str, exit_code: int) -> TestResult:
        passed_count = len(re.findall(r"--- PASS:", output))
        failed_count = len(re.findall(r"--- FAIL:", output))

        if passed_count == 0 and failed_count == 0:
            if exit_code == 0:
                passed_count, failed_count = 1, 0
            else:
                passed_count, failed_count = 0, 1

        total = passed_count + failed_count
        failures = tuple(line.strip() for line in output.splitlines() if "--- FAIL:" in line or "FAIL\t" in line)[:10]

        cov_match = re.search(r"coverage:\s*(\d+(?:\.\d+)?)%", output)
        coverage = float(cov_match.group(1)) if cov_match else None

        return TestResult(
            step_id=step_id,
            total_tests=total,
            passed_tests=passed_count,
            failed_tests=failed_count,
            failure_details=failures,
            coverage_percentage=coverage,
        )


class TestRunnerRegistry:
    __test__ = False

    def __init__(self) -> None:
        self._adapters: list[TestRunnerAdapter] = [
            PytestRunnerAdapter(),
            NpmTestRunnerAdapter(),
            GoTestRunnerAdapter(),
        ]

    def register(self, adapter: TestRunnerAdapter) -> None:
        self._adapters.insert(0, adapter)

    def detect_adapter(self, workspace_path: Path | str) -> TestRunnerAdapter | None:
        path = Path(workspace_path)
        for adapter in self._adapters:
            if adapter.detect(path):
                return adapter
        return None

    async def run_tests(self, workspace_path: Path | str, step_id: str) -> TestResult:
        path = Path(workspace_path)
        adapter = self.detect_adapter(path)
        if adapter is None:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                failure_details=("No supported test runner detected",),
            )
        return await adapter.run_tests(path, step_id)


default_test_runner_registry = TestRunnerRegistry()
