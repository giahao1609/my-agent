from __future__ import annotations

from pathlib import Path
from core.ui_governance import (
    AccessibilityValidator,
    DesignTokenValidator,
    UiComponentCatalog,
    UiGovernancePolicy,
)


def test_accessibility_validator_extended_rules() -> None:
    html_violations = """
    <div>
        <!-- A11Y-005: Empty anchor link without text or label -->
        <a href="/profile"></a>

        <!-- A11Y-006: SVG graphic without aria-hidden or role/title -->
        <svg viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>

        <!-- A11Y-007: Table lacking header cell th or scope -->
        <table>
            <tr><td>Data 1</td><td>Data 2</td></tr>
        </table>
    </div>
    """
    violations = AccessibilityValidator.validate_content(html_violations, file_path="Extended.tsx")
    assert len(violations) == 3
    rule_ids = [v.split()[0] for v in violations]
    assert "[A11Y-005]" in rule_ids
    assert "[A11Y-006]" in rule_ids
    assert "[A11Y-007]" in rule_ids


def test_accessibility_validator_extended_clean() -> None:
    clean_html = """
    <div>
        <a href="/profile" aria-label="User Profile">View Profile</a>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M0 0h24v24H0z"/></svg>
        <table>
            <tr><th scope="col">Header 1</th><th scope="col">Header 2</th></tr>
            <tr><td>Data 1</td><td>Data 2</td></tr>
        </table>
    </div>
    """
    violations = AccessibilityValidator.validate_content(clean_html, file_path="CleanExtended.tsx")
    assert len(violations) == 0


def test_design_token_validator() -> None:
    dirty_content = """
    export const BadWidget = () => (
        <div style={{ backgroundColor: "#ff0055", border: "1px solid rgb(255, 0, 0)" }}>
            <span style={{ color: "#333333" }}>Text</span>
        </div>
    );
    """
    violations = DesignTokenValidator.validate_content(dirty_content, file_path="BadWidget.tsx")
    assert len(violations) >= 2
    rule_ids = [v.split()[0] for v in violations]
    assert "[TOKEN-001]" in rule_ids or "[TOKEN-002]" in rule_ids

    # Excluded files (tailwind.config, theme, globals.css) should not trigger violations
    assert len(DesignTokenValidator.validate_content(dirty_content, file_path="tailwind.config.js")) == 0
    assert len(DesignTokenValidator.validate_content(dirty_content, file_path="theme.ts")) == 0
    assert len(DesignTokenValidator.validate_content(dirty_content, file_path="globals.css")) == 0


def test_ui_component_catalog_polyglot_scanning(tmp_path: Path) -> None:
    comp_dir = tmp_path / "components"
    comp_dir.mkdir(parents=True, exist_ok=True)

    (comp_dir / "Modal.vue").write_text("<template><div class='modal'></div></template>", encoding="utf-8")
    (comp_dir / "Drawer.svelte").write_text("<div class='drawer'></div>", encoding="utf-8")
    (comp_dir / "Card.tsx").write_text("export const Card = () => <div />;", encoding="utf-8")

    catalog = UiComponentCatalog(tmp_path)
    count = catalog.scan_catalog()
    assert count == 3
    assert catalog.get_component("Modal") is not None
    assert catalog.get_component("Drawer") is not None
    assert catalog.get_component("Card") is not None


def test_ui_governance_policy_with_design_tokens(tmp_path: Path) -> None:
    comp_file = tmp_path / "TokenAlert.tsx"
    comp_file.write_text(
        "export const Alert = () => <div style={{ color: '#ff1100' }}>Warning</div>;",
        encoding="utf-8",
    )

    catalog = UiComponentCatalog(tmp_path)
    policy = UiGovernancePolicy(catalog)
    res = policy.evaluate_implementation(
        created_components=["Alert"],
        used_components=[],
        source_files=[comp_file],
    )

    assert res.passed is False
    assert len(res.design_token_violations) > 0
    assert any("[TOKEN-001]" in v for v in res.design_token_violations)
