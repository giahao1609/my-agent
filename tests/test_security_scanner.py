from __future__ import annotations

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


def test_is_ignored_path_distinguishes_worktrees_from_rules_and_skills(tmp_path: Path) -> None:
    """Verify that .agents/rules and .agents/skills are scanned, while .agents/worktrees is ignored."""
    root = tmp_path / "repo"
    root.mkdir()

    rule_file = root / ".agents" / "rules" / "policy.toml"
    skill_file = root / ".agents" / "skills" / "my-skill" / "SKILL.md"
    config_file = root / ".agents" / "config.json"
    nested_worktree_file = root / ".agents" / "worktrees" / "step-1" / "core" / "code.py"
    standard_venv_file = root / ".venv" / "lib" / "site.py"
    normal_source_file = root / "core" / "main.py"

    for f in (rule_file, skill_file, config_file, nested_worktree_file, standard_venv_file, normal_source_file):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# dummy", encoding="utf-8")

    # Rules, skills, and config under .agents must NOT be ignored
    assert SecurityScannerRegistry.is_ignored_path(rule_file, root) is False
    assert SecurityScannerRegistry.is_ignored_path(skill_file, root) is False
    assert SecurityScannerRegistry.is_ignored_path(config_file, root) is False
    assert SecurityScannerRegistry.is_ignored_path(normal_source_file, root) is False

    # Ephemeral worktree checkouts and .venv must be ignored from parent root
    assert SecurityScannerRegistry.is_ignored_path(nested_worktree_file, root) is True
    assert SecurityScannerRegistry.is_ignored_path(standard_venv_file, root) is True


def test_is_ignored_path_scans_worktree_when_root_is_worktree(tmp_path: Path) -> None:
    """When the scan root itself is a worktree inside .agents/worktrees, its files MUST be scanned."""
    worktree_root = tmp_path / "parent_repo" / ".agents" / "worktrees" / "step-42"
    worktree_root.mkdir(parents=True)

    src_file = worktree_root / "core" / "app.py"
    rule_file = worktree_root / ".agents" / "rules" / "rule.json"
    venv_file = worktree_root / ".venv" / "bin" / "pip"

    src_file.parent.mkdir(parents=True, exist_ok=True)
    rule_file.parent.mkdir(parents=True, exist_ok=True)
    venv_file.parent.mkdir(parents=True, exist_ok=True)

    src_file.write_text("print('hello')", encoding="utf-8")
    rule_file.write_text("{}", encoding="utf-8")
    venv_file.write_text("# pip", encoding="utf-8")

    # Relative to the worktree root, src_file and rule_file are NOT ignored
    assert SecurityScannerRegistry.is_ignored_path(src_file, worktree_root) is False
    assert SecurityScannerRegistry.is_ignored_path(rule_file, worktree_root) is False
    # .venv inside the worktree is still ignored
    assert SecurityScannerRegistry.is_ignored_path(venv_file, worktree_root) is True


def test_is_ignored_path_ancestor_agents_path_not_ignored(tmp_path: Path) -> None:
    """A project root located in an ancestor path containing '.agents' must not have its files ignored."""
    ancestor = tmp_path / "user" / ".agents" / "workspace" / "project"
    ancestor.mkdir(parents=True)

    file_p = ancestor / "src" / "index.py"
    file_p.parent.mkdir(parents=True, exist_ok=True)
    file_p.write_text("# code", encoding="utf-8")

    assert SecurityScannerRegistry.is_ignored_path(file_p, ancestor) is False


def test_scan_workspace_full_e2e_worktree_and_rules_coverage(tmp_path: Path) -> None:
    """E2E verification that scan_workspace scans .agents/rules but ignores nested worktrees."""
    repo_root = tmp_path / "e2e_repo"
    repo_root.mkdir()

    # 1. Clean app file
    clean_app = repo_root / "app.py"
    clean_app.write_text("def hello(): pass\n", encoding="utf-8")

    # 2. Insecure rule in .agents/rules/ -> MUST be detected
    bad_rule = repo_root / ".agents" / "rules" / "custom_rule.py"
    bad_rule.parent.mkdir(parents=True, exist_ok=True)
    bad_rule.write_text("eval('os.system(cmd)')\n", encoding="utf-8")

    # 3. Insecure file in nested .agents/worktrees/ -> MUST be ignored from parent scan
    worktree_bad = repo_root / ".agents" / "worktrees" / "step-test" / "leaked.py"
    worktree_bad.parent.mkdir(parents=True, exist_ok=True)
    worktree_bad.write_text("aws_key = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")

    scanner = SecurityScannerRegistry()

    # Scan parent repo: should detect bad_rule in .agents/rules, but ignore worktree_bad
    res_parent = scanner.scan_workspace(repo_root, step_id="parent_scan")
    file_paths = [f.file_path for f in res_parent.findings]

    # bad_rule in .agents/rules was scanned and detected
    assert any("custom_rule.py" in fp for fp in file_paths)
    # worktree_bad in .agents/worktrees was skipped
    assert not any("leaked.py" in fp for fp in file_paths)

    # Now scan the worktree itself as root: leaked.py MUST be detected
    res_worktree = scanner.scan_workspace(worktree_bad.parent, step_id="worktree_scan")
    wt_files = [f.file_path for f in res_worktree.findings]
    assert any("leaked.py" in fp for fp in wt_files)


def test_scan_workspace_target_file_escape_fails_gate(tmp_path: Path) -> None:
    """Path traversal or symlink escape in target_files must emit CRITICAL finding and fail gate."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("# outside", encoding="utf-8")

    scanner = SecurityScannerRegistry()
    res = scanner.scan_workspace(root, step_id="escape_test", target_files=["../outside.py"])

    assert res.passed_gate is False
    assert any(f.rule_id == "ESCAPE-001" and f.severity == SecuritySeverity.CRITICAL for f in res.findings)


def test_is_scannable_file_includes_skill_md_and_configs() -> None:
    """Verify is_scannable_file correctly identifies skills, rules, configs, and scripts."""
    assert SecurityScannerRegistry.is_scannable_file(Path("SKILL.md")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path("skill.md")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path(".agents/skills/my-agent/SKILL.md")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path(".agents/rules/branch_policy.md")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path("config.yaml")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path("pipeline.yml")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path("settings.toml")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path(".env")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path("deploy.sh")) is True
    assert SecurityScannerRegistry.is_scannable_file(Path("app.py")) is True

    # General documentation outside .agents/skills/ and .agents/rules/ is not scannable code
    assert SecurityScannerRegistry.is_scannable_file(Path("docs/README.md")) is False
    assert SecurityScannerRegistry.is_scannable_file(Path("notes/architecture.md")) is False


def test_scan_workspace_detects_secrets_in_skill_md_and_configs(tmp_path: Path) -> None:
    """Workspace scan must inspect SKILL.md and configs and detect hardcoded credentials."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # 1. SKILL.md containing a leaked OpenAI token
    skill_file = repo / ".agents" / "skills" / "deploy-tool" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Deploy Skill\nUse key: sk-abcdefghijklmnopqrstuvwxyz12345\n", encoding="utf-8")

    # 2. Config YAML containing a leaked AWS Access Key
    config_file = repo / "deploy" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("aws_key: AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")

    # 3. Standard markdown doc without secrets
    doc_file = repo / "docs" / "user_guide.md"
    doc_file.parent.mkdir(parents=True)
    doc_file.write_text("# User Guide\nThis is a harmless documentation file.\n", encoding="utf-8")

    scanner = SecurityScannerRegistry()
    res = scanner.scan_workspace(repo, step_id="skill_config_scan")

    assert res.passed_gate is False
    findings_files = [f.file_path for f in res.findings]
    assert any("SKILL.md" in fp for fp in findings_files)
    assert any("config.yaml" in fp for fp in findings_files)
    # user_guide.md was not scanned
    assert not any("user_guide.md" in fp for fp in findings_files)
