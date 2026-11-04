"""Unit tests for UI Remediation Generator, Anti-Pattern Rules v2, and Sector Archetypes."""
from __future__ import annotations

import pytest

from core.ui_design_style_engine import (
    ANTI_PATTERN_RULES,
    DesignAntiPatternDetector,
    UiDesignStyleEngine,
)
from core.ui_remediation_generator import (
    UiRemediationGenerator,
    UiRemediationResult,
)
from my_agent_mcp import server


def test_thirty_five_anti_pattern_rules():
    """Verify that exactly 35 anti-pattern rules (20 generic + 15 mechanical) exist and are configured."""
    assert len(ANTI_PATTERN_RULES) == 35
    rule_ids = [r.rule_id for r in ANTI_PATTERN_RULES]
    assert len(rule_ids) == len(set(rule_ids)), "Rule IDs must be unique"
    for i in range(1, 21):
        expected_id = f"GENERIC-{i:03d}"
        assert expected_id in rule_ids, f"Expected {expected_id} in rules"
    for i in range(1, 16):
        expected_id = f"MECHANICAL-{i:03d}"
        assert expected_id in rule_ids, f"Expected {expected_id} in rules"


def test_new_anti_pattern_detections():
    """Verify detection logic for GENERIC-013 through GENERIC-020."""
    detector = DesignAntiPatternDetector()

    # GENERIC-013: Low-contrast muted text
    code_13 = '<p className="text-gray-400 text-sm">Subtitle</p>'
    v13 = detector.detect(code_13)
    assert any(v.rule_id == "GENERIC-013" for v in v13)

    # GENERIC-014: Sub-40px touch target
    code_14 = '<button className="h-6 px-2 text-xs" onClick={handleClick}>Small</button>'
    v14 = detector.detect(code_14)
    assert any(v.rule_id == "GENERIC-014" for v in v14)

    # GENERIC-015: Nested scrollbar syndrome
    code_15 = '<div className="overflow-y-auto max-h-96"><div className="overflow-y-auto h-32">Content</div></div>'
    v15 = detector.detect(code_15)
    assert any(v.rule_id == "GENERIC-015" for v in v15)

    # GENERIC-016: Generic placeholder avatar
    code_16 = '<img src="https://via.placeholder.com/150" alt="Avatar" />'
    v16 = detector.detect(code_16)
    assert any(v.rule_id == "GENERIC-016" for v in v16)

    # GENERIC-017: Inaccessible div click trap
    code_17 = '<div onClick={() => selectItem(item.id)} className="cursor-pointer">Clickable div</div>'
    v17 = detector.detect(code_17)
    assert any(v.rule_id == "GENERIC-017" for v in v17)

    # GENERIC-018: Monolithic unlayered border
    code_18 = '<div className="p-4 border border-gray-200">Panel</div>'
    v18 = detector.detect(code_18)
    assert any(v.rule_id == "GENERIC-018" for v in v18)

    # GENERIC-019: Backdrop without blur
    code_19 = '<div className="fixed inset-0 bg-black/20 z-50">Modal</div>'
    v19 = detector.detect(code_19)
    assert any(v.rule_id == "GENERIC-019" for v in v19)

    # GENERIC-020: Generic AI loading spinner
    code_20 = '<div className="animate-spin rounded-full border-b-2 border-blue-500 w-8 h-8"></div>'
    v20 = detector.detect(code_20)
    assert any(v.rule_id == "GENERIC-020" for v in v20)


def test_mechanical_anti_pattern_detections():
    """Verify detection logic for MECHANICAL-001 through MECHANICAL-015."""
    detector = DesignAntiPatternDetector()

    # MECHANICAL-001: Repetitive Card Clone Syndrome (3+ identical rounded border cards)
    code_m1 = '''
    <div className="grid grid-cols-3">
        <div className="rounded-xl border p-6">Card 1</div>
        <div className="rounded-xl border p-6">Card 2</div>
        <div className="rounded-xl border p-6">Card 3</div>
    </div>
    '''
    assert any(v.rule_id == "MECHANICAL-001" for v in detector.detect(code_m1))

    # MECHANICAL-002: Lack of Tactile Click Feedback (button without active:scale)
    code_m2 = '<button className="px-4 py-2 bg-blue-600 hover:bg-blue-700">Submit</button>'
    assert any(v.rule_id == "MECHANICAL-002" for v in detector.detect(code_m2))

    # MECHANICAL-003: Unhandled Text Overflow (heading without truncate or line-clamp)
    code_m3 = '<h3 className="text-xl font-bold">{course.title}</h3>'
    assert any(v.rule_id == "MECHANICAL-003" for v in detector.detect(code_m3))

    # MECHANICAL-004: Missing Skeleton Shimmer Loader (raw spinner for content loading)
    code_m4 = 'isLoading ? <div className="animate-spin h-8 w-8" /> : <div>Loaded</div>'
    assert any(v.rule_id == "MECHANICAL-004" for v in detector.detect(code_m4))

    # MECHANICAL-005: Lazy Raw Empty State
    code_m5 = '<p>No data found</p>'
    assert any(v.rule_id == "MECHANICAL-005" for v in detector.detect(code_m5))

    # MECHANICAL-006: Removed Focus Outline Without Ring Replacement
    code_m6 = '<button className="focus:outline-none bg-blue-500">Action</button>'
    assert any(v.rule_id == "MECHANICAL-006" for v in detector.detect(code_m6))

    # MECHANICAL-007: Stat Box Row Cliché (3+ large number + small muted label)
    code_m7 = '''
    <div>
        <div><span className="text-3xl font-bold">120</span><p className="text-xs text-gray-500">Total</p></div>
        <div><span className="text-3xl font-bold">45</span><p className="text-xs text-gray-500">Active</p></div>
        <div><span className="text-3xl font-bold">12</span><p className="text-xs text-gray-500">Pending</p></div>
    </div>
    '''
    assert any(v.rule_id == "MECHANICAL-007" for v in detector.detect(code_m7))

    # MECHANICAL-008: Unstyled Generic Input Form
    code_m8 = '<input className="px-3 py-2 border border-gray-300 rounded" />'
    assert any(v.rule_id == "MECHANICAL-008" for v in detector.detect(code_m8))

    # MECHANICAL-009: Unstructured Arbitrary Z-Index
    code_m9 = '<div className="fixed inset-0 z-[9999]">Popup</div>'
    assert any(v.rule_id == "MECHANICAL-009" for v in detector.detect(code_m9))

    # MECHANICAL-010: Missing Main Landmark or Skip Link
    code_m10 = '<div className="min-h-screen bg-slate-900"><div>Header</div><div>Content</div></div>'
    assert any(v.rule_id == "MECHANICAL-010" for v in detector.detect(code_m10))

    # MECHANICAL-011: Unstyled Native Checkbox/Radio
    code_m11 = '<input type="checkbox" name="agree" />'
    assert any(v.rule_id == "MECHANICAL-011" for v in detector.detect(code_m11))

    # MECHANICAL-012: Unresponsive Static Table Container
    code_m12 = '<table className="w-full"><thead><tr><th>Name</th></tr></thead></table>'
    assert any(v.rule_id == "MECHANICAL-012" for v in detector.detect(code_m12))

    # MECHANICAL-013: Monochromatic Overuse on Status Badges
    code_m13 = '''
    <div>
        <span className="badge bg-gray-100">Draft</span>
        <span className="badge bg-gray-100">Review</span>
        <span className="badge bg-gray-100">Archived</span>
    </div>
    '''
    assert any(v.rule_id == "MECHANICAL-013" for v in detector.detect(code_m13))

    # MECHANICAL-014: Magic Number Spacing In Inline Styles
    code_m14 = '<div style={{ marginTop: "37px" }}>Custom margin</div>'
    assert any(v.rule_id == "MECHANICAL-014" for v in detector.detect(code_m14))

    # MECHANICAL-015: Missing Active Selection State on Navigation
    code_m15 = '<nav className="flex space-x-4"><a href="/home">Home</a></nav>'
    assert any(v.rule_id == "MECHANICAL-015" for v in detector.detect(code_m15))


def test_new_sector_archetypes():
    """Verify new Sector Archetypes: edtech_ai, web3_crypto, social_community."""
    engine = UiDesignStyleEngine()

    # edtech_ai
    ctx_ed = engine.analyze_context(
        description="AI learning companion interface for students",
        sector="edtech_ai",
    )
    profile_ed = engine.generate_style_profile(ctx_ed)
    assert profile_ed.visual_language == "Cognitive Focus & Modern Clarity"
    assert profile_ed.color_temperature == "warm-focus"
    assert profile_ed.surface_primary == "#F7F5F0"
    assert profile_ed.accent == "#4361EE"
    assert profile_ed.border_radius == "8px"
    assert profile_ed.heading_font == "Plus Jakarta Sans"
    assert profile_ed.body_font == "Newsreader"
    assert profile_ed.motion_personality == "focused"

    # web3_crypto
    ctx_w3 = engine.analyze_context(
        description="DeFi analytics dashboard",
        sector="web3_crypto",
    )
    profile_w3 = engine.generate_style_profile(ctx_w3)
    assert profile_w3.visual_language == "Cyber Dark & Neon Accent"
    assert profile_w3.color_temperature == "dark-vibrant"
    assert profile_w3.surface_primary == "#0A0B0E"
    assert profile_w3.accent == "#00F5D4"
    assert profile_w3.border_radius == "2px"

    # social_community
    ctx_sc = engine.analyze_context(
        description="Community discussion forum",
        sector="social_community",
    )
    profile_sc = engine.generate_style_profile(ctx_sc)
    assert profile_sc.visual_language == "Expressive Social"
    assert profile_sc.border_radius == "14px"
    assert profile_sc.motion_personality == "expressive"


def test_ui_remediation_generator():
    """Test generating a structured remediation prompt from generic AI code."""
    generator = UiRemediationGenerator()
    slop_code = """
    <div className="bg-white min-h-screen text-center">
        <h1 className="text-4xl font-bold">Welcome</h1>
        <p className="text-gray-400">Manage your courses</p>
        <div onClick={() => openModal()} className="border border-gray-200">
            <button className="h-6 px-2 rounded-full bg-blue-500 text-white">Action 1</button>
            <button className="h-6 px-2 rounded-full bg-blue-500 text-white">Action 2</button>
        </div>
        <div className="animate-spin rounded-full border-b-2 border-blue-500"></div>
    </div>
    """
    result = generator.generate(
        code=slop_code,
        sector="edtech_ai",
        project_name="Teachora AI",
        target_file="components/CourseCard.tsx",
    )

    assert isinstance(result, UiRemediationResult)
    assert result.originality_score < 0.70
    assert result.verdict in ("GENERIC", "AI_SLOP")
    assert result.violations_count >= 4
    assert result.sector == "edtech_ai"
    assert result.visual_language == "Cognitive Focus & Modern Clarity"

    prompt = result.remediation_prompt
    assert "UI REMEDIATION SPECIFICATION — FILE: COMPONENTS/COURSECARD.TSX" in prompt
    assert "Cognitive Focus & Modern Clarity" in prompt
    assert "#F7F5F0" in prompt
    assert "#4361EE" in prompt
    assert "WCAG AA" in prompt
    assert "44px" in prompt
    assert "[GENERIC-001]" in prompt or "[GENERIC-013]" in prompt or "[GENERIC-017]" in prompt

    d = result.to_dict()
    assert d["sector"] == "edtech_ai"
    assert len(d["violations"]) == result.violations_count
    assert "surface_primary" in d["prescribed_tokens"]


@pytest.mark.asyncio
async def test_mcp_ui_remediation_tool():
    """Verify MCP tools audit_ui_design and generate_ui_remediation_prompt."""
    test_code = """
    <div className="bg-white min-h-screen">
        <div onClick={() => doSomething()} className="p-4 border border-gray-200">
            <button className="rounded-full bg-blue-500 text-white">Click</button>
            <p className="text-gray-400">Secondary text</p>
        </div>
    </div>
    """
    # Test audit_ui_design with new rules
    audit_res = await server.audit_ui_design(test_code)
    assert audit_res["verdict"] in ("GENERIC", "AI_SLOP", "ACCEPTABLE")
    assert audit_res["score"] < 0.75
    v_ids = [v["rule_id"] for v in audit_res["violations"]]
    assert "GENERIC-017" in v_ids
    assert "GENERIC-013" in v_ids
    assert "GENERIC-018" in v_ids
    assert "GENERIC-001" in v_ids
    assert "GENERIC-003" in v_ids

    # Test generate_ui_remediation_prompt MCP tool
    rem_res = await server.generate_ui_remediation_prompt(
        code=test_code,
        sector="edtech_ai",
        project_name="Teachora",
        target_file="src/components/Item.tsx",
    )
    assert rem_res["sector"] == "edtech_ai"
    assert rem_res["visual_language"] == "Cognitive Focus & Modern Clarity"
    assert rem_res["violations_count"] >= 4
    assert "REMEDIATION SPECIFICATION" in rem_res["remediation_prompt"]
    assert rem_res["prescribed_tokens"]["surface_primary"] == "#F7F5F0"
    assert rem_res["prescribed_tokens"]["accent"] == "#4361EE"


def test_surface_modes_and_layout_paradigms():
    """Verify Surface Modes and Spatial Layout Paradigms resolution."""
    engine = UiDesignStyleEngine()

    # 1. Earn mode (workspace/dashboard) default layout is bento_asymmetric
    ctx_earn = engine.analyze_context(
        description="Learning progress tracker",
        sector="edtech_ai",
        surface_mode="earn",
    )
    prof_earn = engine.generate_style_profile(ctx_earn)
    assert prof_earn.surface_mode == "earn"
    assert prof_earn.layout_paradigm == "bento_asymmetric"
    assert prof_earn.border_highlight != ""
    assert prof_earn.surface_elevated != ""
    assert prof_earn.text_secondary != ""
    assert prof_earn.accent_secondary != ""
    assert prof_earn.glow_ambient != ""

    # 2. Persuade mode (landing page) default layout is split_hero
    ctx_persuade = engine.analyze_context(
        description="Landing page for platform",
        sector="saas_b2b",
        surface_mode="persuade",
    )
    prof_persuade = engine.generate_style_profile(ctx_persuade)
    assert prof_persuade.surface_mode == "persuade"
    assert prof_persuade.layout_paradigm == "split_hero"

    # 3. Operate mode (data grid / complex tools) default layout is master_detail
    ctx_operate = engine.analyze_context(
        description="Analytics console",
        sector="data_analytics",
        surface_mode="operate",
    )
    prof_operate = engine.generate_style_profile(ctx_operate)
    assert prof_operate.surface_mode == "operate"
    assert prof_operate.layout_paradigm == "master_detail"

    # 4. Explicit override of layout paradigm
    ctx_custom = engine.analyze_context(
        description="Documentation reader",
        sector="developer_tool",
        surface_mode="read",
        layout_paradigm="feed_stream",
    )
    prof_custom = engine.generate_style_profile(ctx_custom)
    assert prof_custom.surface_mode == "read"
    assert prof_custom.layout_paradigm == "feed_stream"

    # Check to_dict contains new layout and surface tokens
    d = prof_custom.to_dict()
    assert d["surface_mode"] == "read"
    assert d["layout_paradigm"] == "feed_stream"
    assert "surface_elevated" in d
    assert "text_secondary" in d
    assert "accent_secondary" in d
    assert "border_highlight" in d
    assert "glow_ambient" in d


def test_design_dials():
    """Verify that design dials influence the remediation prompt synthesis."""
    generator = UiRemediationGenerator()
    code = '<div className="p-4 border"><span>Sample</span></div>'

    # 1. diversify dial
    res_div = generator.generate(code=code, dial="diversify", surface_mode="earn")
    assert "DIVERSIFY" in res_div.remediation_prompt
    assert "Bento" in res_div.remediation_prompt or "asymmetric" in res_div.remediation_prompt.lower()

    # 2. harden dial
    res_hard = generator.generate(code=code, dial="harden", surface_mode="operate")
    assert "HARDEN" in res_hard.remediation_prompt
    assert "skeleton" in res_hard.remediation_prompt.lower() or "line-clamp" in res_hard.remediation_prompt.lower()

    # 3. polish dial
    res_pol = generator.generate(code=code, dial="polish")
    assert "POLISH" in res_pol.remediation_prompt

    # 4. bolder dial
    res_bold = generator.generate(code=code, dial="bolder")
    assert "BOLDER" in res_bold.remediation_prompt

    # 5. quieter dial
    res_quiet = generator.generate(code=code, dial="quieter")
    assert "QUIETER" in res_quiet.remediation_prompt


@pytest.mark.asyncio
async def test_mcp_ui_advanced_features():
    """Verify generate_ui_design_profile and generate_ui_remediation_prompt with new surface_mode and dials."""
    # Test generate_ui_design_profile with surface_mode and layout_paradigm
    profile_res = await server.generate_ui_design_profile(
        project_description="Interactive tutoring platform",
        sector="edtech_ai",
        surface_mode="earn",
        layout_paradigm="bento_asymmetric",
    )
    prof_dict = profile_res["profile"]
    assert prof_dict["surface_mode"] == "earn"
    assert prof_dict["layout_paradigm"] == "bento_asymmetric"
    assert "surface_elevated" in prof_dict
    assert "Cognitive Focus" in profile_res["visual_language"]

    # Test generate_ui_remediation_prompt with surface_mode and dial
    code = '<div className="grid grid-cols-3"><div className="p-4 border">Item</div></div>'
    rem_res = await server.generate_ui_remediation_prompt(
        code=code,
        sector="edtech_ai",
        surface_mode="earn",
        dial="diversify",
        project_name="Teachora",
    )
    assert rem_res["sector"] == "edtech_ai"
    assert "Bento" in rem_res["remediation_prompt"] or "DIVERSIFY" in rem_res["remediation_prompt"]
