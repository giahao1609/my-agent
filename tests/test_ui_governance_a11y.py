from core.ui_governance import AccessibilityValidator, UiGovernancePolicy, UiComponentCatalog


def test_accessibility_validator():
    html_with_violations = """
    <div>
        <img src="/logo.png">
        <button></button>
        <div onClick={handleClick}>Click me</div>
        <input type="text">
    </div>
    """
    violations = AccessibilityValidator.validate_content(html_with_violations, file_path="Button.tsx")
    assert len(violations) == 4
    rule_ids = [v.split()[0] for v in violations]
    assert "[A11Y-001]" in rule_ids  # Missing img alt
    assert "[A11Y-002]" in rule_ids  # Icon/empty button
    assert "[A11Y-003]" in rule_ids  # Clickable div without role/tabIndex
    assert "[A11Y-004]" in rule_ids  # Input without label/id


def test_accessibility_validator_clean():
    clean_html = """
    <div>
        <img src="/logo.png" alt="Company Logo">
        <button aria-label="Close modal"><CloseIcon /></button>
        <div role="button" tabIndex={0} onClick={handleClick}>Click me</div>
        <input id="email-field" name="email" type="email">
    </div>
    """
    violations = AccessibilityValidator.validate_content(clean_html, file_path="CleanComponent.tsx")
    assert len(violations) == 0
