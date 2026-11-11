from __future__ import annotations

import asyncio
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .handoff_contracts import TestResult


class TestRunnerAdapter(Protocol):
    def detect(self, workspace_path: Path) -> bool: ...

    async def run_tests(
        self,
        workspace_path: Path,
        step_id: str,
        target_files: Sequence[str] | None = None,
        timeout_seconds: float = 120.0,
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
        target_files: Sequence[str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> TestResult:
        local_pytest = workspace_path / ".venv" / "bin" / "pytest"
        which_pytest = shutil.which("pytest")
        if local_pytest.exists():
            cmd = [str(local_pytest)]
        elif which_pytest:
            cmd = [which_pytest]
        else:
            cmd = [sys.executable, "-m", "pytest"]
        if target_files:
            cmd.extend(target_files)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
            output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
            return self._parse_pytest_output(step_id, output, proc.returncode or 0)
        except TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:  # noqa: BLE001, S110
                    pass
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Pytest execution timed out after {timeout_seconds}s",),
                execution_success=False,
            )
        except Exception as e:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Failed to execute pytest: {e}",),
                execution_success=False,
            )

    @staticmethod
    def _parse_pytest_output(step_id: str, output: str, exit_code: int) -> TestResult:
        # Match passed, failed, and error counts in pytest summary
        passed_match = re.search(r"(\d+)\s+passed\b", output)
        failed_match = re.search(r"(\d+)\s+failed\b", output)
        errors_match = re.search(r"(\d+)\s+error(?:s)?\b", output)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0

        total_failed = failed + errors

        # Strict invariant: any non-zero exit code or execution/collection error MUST NOT report PASS
        if exit_code != 0 and total_failed == 0:
            total_failed = 1

        total = passed + total_failed

        failures: list[str] = []
        if total_failed > 0:
            for line in output.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if (
                    "FAILED" in stripped
                    or "ERROR" in stripped
                    or "Error:" in stripped
                    or "E   " in stripped
                    or "INTERNALERROR" in stripped
                    or "collection error" in stripped.lower()
                ):
                    failures.append(stripped)

            if not failures and exit_code != 0:
                failures.append(f"Pytest exited with non-zero exit code {exit_code}")
                tail_lines = [l.strip() for l in output.splitlines() if l.strip()][-3:]
                failures.extend(tail_lines)

        cov_match = re.search(r"TOTAL\s+.*\s+(\d+(?:\.\d+)?)%", output)
        if not cov_match:
            cov_match = re.search(r"(\d+(?:\.\d+)?)%\s+coverage", output, re.IGNORECASE)
        coverage = float(cov_match.group(1)) if cov_match else None

        execution_success = exit_code == 0 and total_failed == 0

        return TestResult(
            step_id=step_id,
            total_tests=total,
            passed_tests=passed,
            failed_tests=total_failed,
            failure_details=tuple(failures[:10]),
            coverage_percentage=coverage,
            execution_success=execution_success,
        )


class NpmTestRunnerAdapter:
    def detect(self, workspace_path: Path) -> bool:
        return (workspace_path / "package.json").exists()

    async def run_tests(
        self,
        workspace_path: Path,
        step_id: str,
        target_files: Sequence[str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> TestResult:
        cmd = ["npm", "test", "--", "--run"]
        if target_files:
            cmd.extend(target_files)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
            output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
            return self._parse_npm_output(step_id, output, proc.returncode or 0)
        except TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:  # noqa: BLE001, S110
                    pass
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Npm test execution timed out after {timeout_seconds}s",),
                execution_success=False,
            )
        except Exception as e:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Failed to execute npm test: {e}",),
                execution_success=False,
            )

    @staticmethod
    def _parse_npm_output(step_id: str, output: str, exit_code: int) -> TestResult:
        passed_match = re.search(r"(\d+)\s+passed", output, re.IGNORECASE)
        failed_match = re.search(r"(\d+)\s+failed", output, re.IGNORECASE)
        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        if exit_code != 0 and failed == 0:
            failed = 1
        total = passed + failed

        cov_match = re.search(r"All files\s*\|\s*(\d+(?:\.\d+)?)", output)
        if not cov_match:
            cov_match = re.search(r"Coverage:\s*(\d+(?:\.\d+)?)%", output, re.IGNORECASE)
        coverage = float(cov_match.group(1)) if cov_match else None

        execution_success = exit_code == 0 and failed == 0

        return TestResult(
            step_id=step_id,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            failure_details=tuple(line.strip() for line in output.splitlines() if "FAIL" in line or "Error:" in line)[:10],
            coverage_percentage=coverage,
            execution_success=execution_success,
        )


class GoTestRunnerAdapter:
    def detect(self, workspace_path: Path) -> bool:
        return (workspace_path / "go.mod").exists()

    async def run_tests(
        self,
        workspace_path: Path,
        step_id: str,
        target_files: Sequence[str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> TestResult:
        cmd = ["go", "test"]
        if target_files:
            cmd.extend(target_files)
        else:
            cmd.append("./...")

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
            output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
            return self._parse_go_output(step_id, output, proc.returncode or 0)
        except TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:  # noqa: BLE001, S110
                    pass
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Go test execution timed out after {timeout_seconds}s",),
                execution_success=False,
            )
        except Exception as e:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=(f"Failed to execute go test: {e}",),
                execution_success=False,
            )

    @staticmethod
    def _parse_go_output(step_id: str, output: str, exit_code: int) -> TestResult:
        passed_count = len(re.findall(r"^--- PASS:", output, re.MULTILINE))
        failed_count = len(re.findall(r"^--- FAIL:", output, re.MULTILINE))

        pkg_ok_count = len(re.findall(r"^ok\s+", output, re.MULTILINE))
        pkg_fail_count = len(re.findall(r"^FAIL\s+", output, re.MULTILINE))

        has_no_test_files = bool(re.search(r"\[no test files\]|\[no tests to run\]", output))

        if passed_count == 0 and failed_count == 0:
            if pkg_ok_count > 0 and exit_code == 0:
                passed_count = pkg_ok_count
            elif exit_code == 0 and has_no_test_files:
                passed_count = 0
                failed_count = 0
            elif exit_code != 0:
                failed_count = max(1, pkg_fail_count)
            else:
                passed_count = 0
                failed_count = 0

        # Strict invariant for F02: exit_code != 0 MUST ALWAYS result in failed_count >= 1
        if exit_code != 0 and failed_count == 0:
            failed_count = max(1, pkg_fail_count)

        total = passed_count + failed_count

        failures: list[str] = []
        if failed_count > 0 or exit_code != 0:
            for line in output.splitlines():
                stripped = line.strip()
                if (
                    stripped.startswith("--- FAIL:")
                    or stripped.startswith("FAIL\t")
                    or stripped.startswith("FAIL ")
                    or "panic:" in stripped
                    or "[build failed]" in stripped
                    or "[setup failed]" in stripped
                    or "cannot find package" in stripped
                ):
                    failures.append(stripped)
            if not failures and exit_code != 0:
                failures.append(f"Go test command failed with exit code {exit_code}")
                tail_lines = [l.strip() for l in output.splitlines() if l.strip()][-3:]
                failures.extend(tail_lines)

        cov_match = re.search(r"coverage:\s*(\d+(?:\.\d+)?)%", output)
        coverage = float(cov_match.group(1)) if cov_match else None

        execution_success = exit_code == 0 and failed_count == 0

        return TestResult(
            step_id=step_id,
            total_tests=total,
            passed_tests=passed_count,
            failed_tests=failed_count,
            failure_details=tuple(failures[:10]),
            coverage_percentage=coverage,
            execution_success=execution_success,
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

    async def run_tests(
        self,
        workspace_path: Path | str,
        step_id: str,
        target_files: Sequence[str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> TestResult:
        path = Path(workspace_path)
        adapter = self.detect_adapter(path)
        if adapter is None:
            return TestResult(
                step_id=step_id,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                failure_details=("No supported test runner detected",),
                execution_success=False,
            )
        return await adapter.run_tests(
            path,
            step_id,
            target_files=target_files,
            timeout_seconds=timeout_seconds,
        )


default_test_runner_registry = TestRunnerRegistry()
