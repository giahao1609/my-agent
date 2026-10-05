from __future__ import annotations

from pathlib import Path
from core.handoff_contracts import SecuritySeverity
from core.security_scanner import (
    SecretDetectorAdapter,
    SecretRedactor,
    SastScannerAdapter,
    SecurityScannerRegistry,
)


def test_secret_redactor_extended() -> None:
    raw = (
        "Google key AIzaSyD9x8K1m7n2p5q8r0s3t6u9v2w5x8y1z4 and "
        "webhook https://discord.com/api/webhooks/123456789/abcdef-ghijkl and "
        "redis redis://user:supersecretpass@localhost:6379/0 and "
        f"square {'sq0csp'}-{('dummysecret' * 5)[:43]}"
    )
    redacted = SecretRedactor.redact(raw)
    assert "[REDACTED_GOOGLE_API_KEY]" in redacted
    assert "[REDACTED_DISCORD_WEBHOOK]" in redacted
    assert "redis://[REDACTED_REDIS_PASSWORD]@" in redacted
    assert "[REDACTED_SQUARE_SECRET]" in redacted
    assert "supersecretpass" not in redacted


def test_secret_detector_extended_patterns(tmp_path: Path) -> None:
    f = tmp_path / "app_config.py"
    f.write_text(
        "google_key = 'AIzaSyD9x8K1m7n2p5q8r0s3t6u9v2w5x8y1z4'\n"
        "discord_hook = 'https://discord.com/api/webhooks/123456/abcdef'\n"
        "redis_url = 'redis://:mypassword@redis-cluster:6379'\n",
        encoding="utf-8",
    )
    detector = SecretDetectorAdapter()
    findings = detector.scan_file(f, "app_config.py")
    descriptions = [finding.description for finding in findings]
    assert any("Google API Key" in d for d in descriptions)
    assert any("Discord Webhook URL" in d for d in descriptions)
    assert any("Redis Connection Password" in d for d in descriptions)


def test_sast_scanner_extended_rules(tmp_path: Path) -> None:
    f = tmp_path / "vulnerable_new.py"
    f.write_text(
        "import yaml\n"
        "data = yaml.load(user_input)\n"
        "import hashlib\n"
        "token = hashlib.md5(b'pw').hexdigest()\n"
        "requests.get('https://example.com', verify=False)\n",
        encoding="utf-8",
    )
    sast = SastScannerAdapter()
    findings = sast.scan_file(f, "vulnerable_new.py")
    rule_ids = {f.rule_id for f in findings}
    assert "SAST-007" in rule_ids
    assert "SAST-008" in rule_ids
    assert "SAST-009" in rule_ids


def test_secret_redactor_and_detector_new_vendors(tmp_path: Path) -> None:
    raw = (
        "gitlab: glpat-abcdef12345678901234\n"
        "hf: hf_0123456789abcdef0123456789abcdef01\n"
        f"supabase: {'sbp'}_{('dummytoken' * 5)[:40]}\n"
        "anthropic: sk-ant-api03-abcdef12345678901234567890\n"
    )
    redacted = SecretRedactor.redact(raw)
    assert "[REDACTED_GITLAB_TOKEN]" in redacted
    assert "[REDACTED_HUGGINGFACE_TOKEN]" in redacted
    assert "[REDACTED_SUPABASE_KEY]" in redacted
    assert "[REDACTED_ANTHROPIC_KEY]" in redacted

    f = tmp_path / "credentials.env"
    f.write_text(raw, encoding="utf-8")
    detector = SecretDetectorAdapter()
    findings = detector.scan_file(f, "credentials.env")
    assert len(findings) == 4
    descriptions = [finding.description for finding in findings]
    assert any("GitLab Personal Access Token" in d for d in descriptions)
    assert any("Hugging Face API Token" in d for d in descriptions)
    assert any("Supabase API Key" in d for d in descriptions)
    assert any("Anthropic API Key" in d for d in descriptions)


def test_sast_scanner_rules_sast_010_to_017(tmp_path: Path) -> None:
    f = tmp_path / "advanced_vulns.py"
    f.write_text(
        "# SAST-010: SSRF\n"
        "requests.get(request.args.get('target_url'))\n"
        "# SAST-011: CORS\n"
        "headers['Access-Control-Allow-Origin'] = '*'\n"
        "# SAST-012: ReDoS\n"
        "re.compile(r'([a-zA-Z0-9]+)+')\n"
        "# SAST-013: Crypto ECB\n"
        "cipher = AES.new(key, AES.MODE_ECB)\n"
        "# SAST-014: Hardcoded JWT Secret\n"
        "jwt.encode(payload, 'super_jwt_secret_key')\n"
        "# SAST-015: Prototype Pollution\n"
        "obj.__proto__[key] = value\n"
        "# SAST-016: Insecure XML\n"
        "tree = xml.etree.ElementTree.parse(user_file)\n"
        "# SAST-017: Static IV\n"
        "iv = b'1234567890123456'\n",
        encoding="utf-8",
    )
    sast = SastScannerAdapter()
    findings = sast.scan_file(f, "advanced_vulns.py")
    rule_ids = {f.rule_id for f in findings}
    assert "SAST-010" in rule_ids
    assert "SAST-011" in rule_ids
    assert "SAST-012" in rule_ids
    assert "SAST-013" in rule_ids
    assert "SAST-014" in rule_ids
    assert "SAST-015" in rule_ids
    assert "SAST-016" in rule_ids
    assert "SAST-017" in rule_ids


def test_dependency_sca_scanner(tmp_path: Path) -> None:
    from core.security_scanner import DependencyScaScannerAdapter

    # Test requirements.txt
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(
        "requests==2.28.0\n"
        "urllib3==1.26.14\n"
        "PyYAML==6.0.1\n",
        encoding="utf-8",
    )
    sca = DependencyScaScannerAdapter()
    findings = sca.scan_file(req_file, "requirements.txt")
    assert len(findings) == 2
    assert all(f.rule_id == "SCA-001" for f in findings)
    assert any("requests==2.28.0" in f.description for f in findings)
    assert any("urllib3==1.26.14" in f.description for f in findings)

    # Test package.json
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(
        '{\n  "dependencies": {\n    "lodash": "4.17.15",\n    "axios": "0.21.1"\n  }\n}\n',
        encoding="utf-8",
    )
    findings_json = sca.scan_file(pkg_file, "package.json")
    assert len(findings_json) == 2
    assert any("lodash@4.17.15" in f.description for f in findings_json)
    assert any("axios@0.21.1" in f.description for f in findings_json)


def test_security_scanner_registry_with_sca(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("requests==2.25.0\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")

    registry = SecurityScannerRegistry()
    result = registry.scan_workspace(tmp_path, step_id="test-sca")
    assert result.passed_gate is False
    assert len(result.findings) >= 1
    assert any(f.rule_id == "SCA-001" for f in result.findings)
