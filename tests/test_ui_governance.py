from __future__ import annotations

import pytest
from pathlib import Path

from agents.ui_coder_agent import UiCoderAgent
from core.agent_role import AgentRole
from core.plan import PlanStepRecord
from core.ui_governance import OriginalityGate, OriginalityGateResult, UiComponentCatalog, UiGovernancePolicy


def test_ui_component_catalog_scanning_and_search(tmp_path: Path) -> None:
    comp_dir = tmp_path / "components" / "ui"
    comp_dir.mkdir(parents=True, exist_ok=True)

    (comp_dir / "Button.tsx").write_text("export const Button = () => <button />;", encoding="utf-8")
    (comp_dir / "Input.tsx").write_text("export const Input = () => <input />;", encoding="utf-8")

    catalog = UiComponentCatalog(tmp_path)
    count = catalog.scan_catalog()
    assert count == 2

    matches = catalog.search_components("button")
    assert len(matches) == 1
    assert matches[0].name == "Button"


def test_ui_governance_policy_evaluation(tmp_path: Path) -> None:
    comp_dir = tmp_path / "components" / "ui"
    comp_dir.mkdir(parents=True, exist_ok=True)
    (comp_dir / "Button.tsx").write_text("export const Button = () => <button />;", encoding="utf-8")

    catalog = UiComponentCatalog(tmp_path)
    policy = UiGovernancePolicy(catalog)

    # Reusing existing component
    res_pass = policy.evaluate_implementation(created_components=[], used_components=["Button"])
    assert res_pass.passed is True
    assert "Button" in res_pass.components_reused

    # Re-creating an existing component
    res_warn = policy.evaluate_implementation(created_components=["Button"], used_components=[])
    assert res_warn.passed is False
    assert len(res_warn.a11y_violations) > 0


@pytest.mark.asyncio
async def test_ui_coder_agent_execution(tmp_path: Path) -> None:
    agent = UiCoderAgent(workspace_root=tmp_path)
    assert agent.role == AgentRole.UI_CODER

    step = PlanStepRecord(
        step_id="step-ui-1",
        plan_id="plan-1",
        step_index=0,
        title="Build Payment UI",
        instruction="Create payment button",
        assigned_role=AgentRole.UI_CODER,
    )

    res = await agent.execute_ui_step(
        step=step,
        modified_files=["PaymentPage.tsx"],
        components_reused=["Button"],
        components_created=["PaymentCard"],
    )

    assert res.step_id == "step-ui-1"
    assert res.a11y_status == "passed"
    assert res.success is True


# ---------------------------------------------------------------------------
# OriginalityGate tests
# ---------------------------------------------------------------------------

GENERIC_CODE = "className='rounded-full bg-blue-500 transition-all duration-200 ease-in-out'"
ORIGINAL_CODE = "style={{background:'#F2EDE4', fontFamily:'Cormorant Garamond', borderRadius:'2px'}}"


def test_originality_gate_passes_above_threshold() -> None:
    gate = OriginalityGate(threshold=0.5)
    result = gate.check(ORIGINAL_CODE)
    assert isinstance(result, OriginalityGateResult)
    assert result.passed is True
    assert result.score >= 0.5


def test_originality_gate_rejects_below_threshold() -> None:
    gate = OriginalityGate(threshold=0.9)  # very strict
    result = gate.check(GENERIC_CODE)
    assert isinstance(result, OriginalityGateResult)
    assert result.passed is False
    assert result.score < 0.9
    assert "below threshold" in result.reason


def test_originality_gate_check_score_directly() -> None:
    gate = OriginalityGate(threshold=0.5)
    result = gate.check_score(score=0.8, verdict="ORIGINAL")
    assert result.passed is True
    assert result.verdict == "ORIGINAL"
    assert result.threshold == 0.5


def test_originality_gate_reason_contains_score() -> None:
    gate = OriginalityGate(threshold=0.5)
    result = gate.check(ORIGINAL_CODE)
    assert str(result.score)[:3] in result.reason


# ---------------------------------------------------------------------------
# UiCoderAgent design intelligence integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ui_coder_agent_with_design_profile_scores_originality(tmp_path: Path) -> None:
    agent = UiCoderAgent(workspace_root=tmp_path)
    profile = agent.get_or_create_design_profile(
        workspace=tmp_path,
        description="Portfolio site",
        sector="portfolio",
    )

    step = PlanStepRecord(
        step_id="step-ui-design",
        plan_id="plan-1",
        step_index=0,
        title="Hero Section",
        instruction="Build hero section",
        assigned_role=AgentRole.UI_CODER,
    )

    res = await agent.execute_ui_step(
        step=step,
        modified_files=["HeroSection.tsx"],
        components_reused=[],
        components_created=["HeroSection"],
        generated_code=ORIGINAL_CODE,
        design_profile=profile,
    )

    assert res.visual_language == profile.visual_language
    assert res.design_originality_score >= 0.0
    assert isinstance(res.anti_patterns_avoided, tuple)
    assert isinstance(res.anti_patterns_detected, tuple)


@pytest.mark.asyncio
async def test_ui_coder_agent_design_md_written_on_first_run(tmp_path: Path) -> None:
    agent = UiCoderAgent(workspace_root=tmp_path)
    profile = agent.get_or_create_design_profile(
        workspace=tmp_path,
        description="Fintech dashboard",
        sector="fintech",
    )

    # DESIGN.md should be created
    design_md_path = tmp_path / "DESIGN.md"
    assert design_md_path.exists()
    content = design_md_path.read_text()
    assert "Design System" in content
    assert profile.visual_language in content


@pytest.mark.asyncio
async def test_ui_coder_agent_reads_existing_design_md(tmp_path: Path) -> None:
    # Write a DESIGN.md with known visual language
    agent = UiCoderAgent(workspace_root=tmp_path)
    profile1 = agent.get_or_create_design_profile(
        workspace=tmp_path,
        description="Creative studio",
        sector="creative_agency",
    )

    # Create new agent instance (simulates restart)
    agent2 = UiCoderAgent(workspace_root=tmp_path)
    profile2 = agent2.get_or_create_design_profile(workspace=tmp_path)

    # Should read existing DESIGN.md and return consistent visual language
    assert profile2 is not None
    assert profile2.visual_language == profile1.visual_language

