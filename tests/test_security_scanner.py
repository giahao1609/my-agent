from __future__ import annotations

import pytest
from pathlib import Path

from core.handoff_contracts import SecuritySeverity
from core.security_scanner import (
    SecretDetectorAdapter,
    SecretRedactor,
    SecurityScannerRegistry,
)


def test_secret_redactor() -> None:
    raw = "Found API key: sk-abcdefghijklmnopqrstuvwxyz1234567890 and AWS key AKIAIOSFODNN7EXAMPLE"
    redacted = SecretRedactor.redact(raw)
    assert "sk-" not in redacted or "[REDACTED" in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "[REDACTED_AWS_KEY]" in redacted


def test_secret_detector_scan_file(tmp_path: Path) -> None:
    test_file = tmp_path / "secret.py"
    test_file.write_text("api_key = 'sk-1234567890123456789012345'\n", encoding="utf-8")

    detector = SecretDetectorAdapter()
    findings = detector.scan_file(test_file, "secret.py")

    assert len(findings) == 1
    assert findings[0].rule_id == "SECRET-001"
    assert findings[0].severity == SecuritySeverity.CRITICAL


def test_security_scanner_registry(tmp_path: Path) -> None:
    vulnerable_file = tmp_path / "vulnerable.py"
    vulnerable_file.write_text("eval('import os; os.system(x)')\napi_key = 'sk-abcdefghijklmnopqrstuvwxyz12345'\n", encoding="utf-8")

    registry = SecurityScannerRegistry()
    res = registry.scan_workspace(tmp_path, "step-1", target_files=["vulnerable.py"])

    assert res.step_id == "step-1"
    assert res.passed_gate is False
    assert len(res.findings) >= 2
    # Ensure findings text is redacted
    for f in res.findings:
        assert "sk-abcdefghijklmnopqrstuvwxyz12345" not in f.description
