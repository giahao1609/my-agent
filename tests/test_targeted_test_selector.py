from __future__ import annotations

from pathlib import Path

from core.targeted_test_selector import TargetedTestSelector


def test_select_tests_empty_changes():
    selector = TargetedTestSelector()
    res = selector.select_tests(Path.cwd(), [])
    assert not res.is_targeted
    assert res.selected_tests == ()
    assert "No changed files" in res.rationale


def test_select_tests_documentation_only():
    selector = TargetedTestSelector()
    res = selector.select_tests(
        Path.cwd(),
        ["README.md", "docs/architecture.md", "data/notes.txt"],
    )
    assert not res.is_targeted
    assert res.selected_tests == ()
    assert "documentation" in res.rationale.lower()


def test_select_tests_direct_test_file_modified():
    selector = TargetedTestSelector()
    res = selector.select_tests(
        Path.cwd(),
        ["tests/test_planner.py"],
    )
    assert res.is_targeted
    assert "tests/test_planner.py" in res.selected_tests
    assert "Direct test file modified" in res.rationale


def test_select_tests_direct_source_mapping():
    selector = TargetedTestSelector()
    res = selector.select_tests(
        Path.cwd(),
        ["core/planner.py"],
    )
    assert res.is_targeted
    assert "tests/test_planner.py" in res.selected_tests
    assert "Mapped source" in res.rationale


def test_select_tests_security_critical_module_trigger():
    selector = TargetedTestSelector()
    res = selector.select_tests(
        Path.cwd(),
        ["core/security_authorization.py"],
    )
    assert res.is_targeted
    # Must include security suite tests
    assert any("tests/security/" in t for t in res.selected_tests)
    assert "Security-critical module modified" in res.rationale


def test_select_tests_composite_multiple_sources():
    selector = TargetedTestSelector()
    res = selector.select_tests(
        Path.cwd(),
        ["core/planner.py", "core/agent_role.py"],
    )
    assert res.is_targeted
    assert "tests/test_planner.py" in res.selected_tests


def test_select_tests_config_files_not_skipped():
    selector = TargetedTestSelector()
    res = selector.select_tests(
        Path.cwd(),
        ["config.yaml", "settings.json"],
    )
    # Must NOT skip suites for configuration changes
    assert res.skipped_suites != ("all",)
    assert "fallback required" in res.rationale.lower()


def test_select_tests_security_rule_config_trigger():
    selector = TargetedTestSelector()
    res = selector.select_tests(
        Path.cwd(),
        [".agents/rules/interaction_protocol.md"],
    )
    assert res.is_targeted
    assert any("tests/security/" in t for t in res.selected_tests)
