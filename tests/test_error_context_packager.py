from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.context_disclosure import BridgeError, BridgeErrorCode
from tools.error_context_packager import (
    ErrorContextPackager,
    package_last_test_failure,
    package_targeted_error,
)


@pytest.fixture
def temp_workspace() -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Create a sample python file
        sample_code = '''"""Sample module for testing error packaging."""

import os

def helper_add(a: int, b: int) -> int:
    return a + b

def calculate_total(items: list[int], tax_rate: float) -> float:
    base = sum(items)
    tax = helper_add(int(base), int(tax_rate))
    return float(base + tax)
'''
        (root / "sample_service.py").write_text(sample_code, encoding="utf-8")

        # Create forbidden files
        (root / ".env").write_text("SECRET_KEY=supersecret123\n", encoding="utf-8")
        (root / "private.key").write_text("FAKE_KEY_DATA\n", encoding="utf-8")
        yield root


def test_package_targeted_error_by_symbol(temp_workspace: Path) -> None:
    packager = ErrorContextPackager(workspace_root=temp_workspace)
    md = packager.package_targeted_error(
        file_path="sample_service.py",
        symbol_name="calculate_total",
        error_description="TypeError: unsupported operand type",
    )

    assert "sample_service.py" in md
    assert "calculate_total" in md
    assert "def calculate_total(items, tax_rate)" in md
    assert "helper_add" in md  # Dependency call extracted via AST
    assert "TypeError: unsupported operand type" in md
    assert "CHỈ DẪN KỸ THUẬT CHO GEMINI PRO" in md


def test_package_targeted_error_by_line(temp_workspace: Path) -> None:
    packager = ErrorContextPackager(workspace_root=temp_workspace)
    md = packager.package_targeted_error(
        file_path="sample_service.py",
        line_number=6,
        context_lines=5,
    )

    assert "sample_service.py" in md
    assert "helper_add" in md


def test_forbidden_env_file_blocked(temp_workspace: Path) -> None:
    packager = ErrorContextPackager(workspace_root=temp_workspace)
    with pytest.raises(BridgeError) as exc_info:
        packager.package_targeted_error(file_path=".env")
    assert exc_info.value.args[0] == BridgeErrorCode.SENSITIVE_CONTEXT_DENIED


def test_forbidden_key_file_blocked(temp_workspace: Path) -> None:
    packager = ErrorContextPackager(workspace_root=temp_workspace)
    with pytest.raises(BridgeError) as exc_info:
        packager.package_targeted_error(file_path="private.key")
    assert exc_info.value.args[0] == BridgeErrorCode.SENSITIVE_CONTEXT_DENIED


def test_path_traversal_blocked(temp_workspace: Path) -> None:
    packager = ErrorContextPackager(workspace_root=temp_workspace)
    with pytest.raises(BridgeError) as exc_info:
        packager.package_targeted_error(file_path="../etc/passwd")
    assert exc_info.value.args[0] == BridgeErrorCode.PROJECT_SCOPE_DENIED


def test_sensitive_credentials_redacted_in_error_description(temp_workspace: Path) -> None:
    packager = ErrorContextPackager(workspace_root=temp_workspace)
    error_with_secret = "Failed auth: api_key='sk-ant-api03-abcdef123456789012345678' on server"
    md = packager.package_targeted_error(
        file_path="sample_service.py",
        symbol_name="helper_add",
        error_description=error_with_secret,
    )

    assert "sk-ant-" not in md
    assert "[REDACTED_CREDENTIAL]" in md or "[REDACTED_SECRET]" in md or "REDACTED" in md


def test_package_last_test_failure_from_string(temp_workspace: Path) -> None:
    packager = ErrorContextPackager(workspace_root=temp_workspace)
    mock_pytest_output = """
============================= test session starts ==============================
FAILED sample_service.py::test_calculate - AssertionError: assert 10 == 12
=========================== short test summary info ============================
1 failed in 0.05s
"""
    md = packager.package_last_test_failure(test_output=mock_pytest_output)

    assert "test_calculate" in md
    assert "sample_service.py" in md
    assert "AssertionError: assert 10 == 12" in md


def test_convenience_functions(temp_workspace: Path) -> None:
    md = package_targeted_error(
        file_path="sample_service.py",
        symbol_name="helper_add",
        workspace_root=temp_workspace,
    )
    assert "helper_add" in md

    md_test = package_last_test_failure(
        test_output="FAILED sample_service.py::test_calculate - AssertionError",
        workspace_root=temp_workspace,
    )
    assert "test_calculate" in md_test
