from __future__ import annotations

from dataclasses import dataclass

from core.error_reflexion import ErrorCategory, ErrorReflexionEngine, ReflexionDiagnosis


@dataclass
class DummySecurityFinding:
    file_path: str
    line_number: int
    rule_id: str
    description: str


def test_diagnose_assertion_failure() -> None:
    traceback_sample = """
Traceback (most recent call last):
  File "core/calculator.py", line 42, in add
    return a - b
  File "tests/test_calculator.py", line 15, in test_add
    assert add(2, 3) == 5
AssertionError: assert -1 == 5
"""
    diag = ErrorReflexionEngine.diagnose_test_failure(traceback_sample)
    assert diag.category == ErrorCategory.ASSERTION_FAILURE
    assert "core/calculator.py" in diag.target_file or "tests/test_calculator.py" in diag.target_file
    assert diag.line_number in (42, 15)
    assert "add" in diag.actionable_instruction or "calculator.py" in diag.actionable_instruction
    assert "ASSERTION_FAILURE" in diag.format_brief()


def test_diagnose_import_error() -> None:
    sample = """
Traceback (most recent call last):
  File "core/worker.py", line 5, in <module>
    from core.missing_module import MissingClass
ModuleNotFoundError: No module named 'core.missing_module'
"""
    diag = ErrorReflexionEngine.diagnose_test_failure(sample)
    assert diag.category == ErrorCategory.IMPORT_ERROR
    assert "core/worker.py" in diag.target_file
    assert diag.line_number == 5
    assert "core.missing_module" in diag.actionable_instruction


def test_diagnose_type_attribute_error() -> None:
    sample = """
Traceback (most recent call last):
  File "core/task.py", line 88, in execute
    self.non_existent_method()
AttributeError: 'Task' object has no attribute 'non_existent_method'
"""
    diag = ErrorReflexionEngine.diagnose_test_failure(sample)
    assert diag.category == ErrorCategory.TYPE_ATTRIBUTE_ERROR
    assert "core/task.py" in diag.target_file
    assert diag.line_number == 88
    assert "non_existent_method" in diag.actionable_instruction


def test_diagnose_security_finding() -> None:
    finding = DummySecurityFinding(
        file_path="config/settings.py",
        line_number=10,
        rule_id="AWS_SECRET_KEY",
        description="Hardcoded AWS Secret Key detected",
    )
    diag = ErrorReflexionEngine.diagnose_security_finding(finding)
    assert diag.category == ErrorCategory.SECURITY_VIOLATION
    assert diag.target_file == "config/settings.py"
    assert diag.line_number == 10
    assert "AWS_SECRET_KEY" in diag.actionable_instruction


def test_build_repair_prompt() -> None:
    d1 = ReflexionDiagnosis(
        category=ErrorCategory.ASSERTION_FAILURE,
        target_file="core/math.py",
        line_number=20,
        root_cause_summary="Expected 10, got 20",
        failed_assertion_detail="assert 20 == 10",
        actionable_instruction="Fix calculation logic",
    )
    d2 = ReflexionDiagnosis(
        category=ErrorCategory.SECURITY_VIOLATION,
        target_file="core/auth.py",
        line_number=5,
        root_cause_summary="API Key exposed",
        failed_assertion_detail="Secret in source",
        actionable_instruction="Move secret to env",
    )
    prompt = ErrorReflexionEngine.build_repair_prompt([d1, d2])
    assert "### [Error Reflexion & Actionable Repair Guide]" in prompt
    assert "core/math.py:20" in prompt
    assert "core/auth.py:5" in prompt
    assert "Fix calculation logic" in prompt
