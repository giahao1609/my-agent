import pytest

from core.prompt_rewriter import PromptRewriter, TargetAI
from my_agent_mcp import server


def test_target_ai_normalization():
    assert TargetAI.from_str("claude-opus-5") == TargetAI.CLAUDE
    assert TargetAI.from_str("anthropic") == TargetAI.CLAUDE
    assert TargetAI.from_str("gpt-4o") == TargetAI.GPT
    assert TargetAI.from_str("o3-mini") == TargetAI.REASONING_O3
    assert TargetAI.from_str("o1") == TargetAI.REASONING_O3
    assert TargetAI.from_str("gemini-2.0-flash") == TargetAI.GEMINI
    assert TargetAI.from_str("coder_agent") == TargetAI.CODER_AGENT
    assert TargetAI.from_str("cursor") == TargetAI.CODER_AGENT
    assert TargetAI.from_str("unknown_target") == TargetAI.GENERAL


def test_rewrite_empty_prompt():
    rewriter = PromptRewriter()
    result = rewriter.rewrite("")
    assert result.rewritten_prompt == ""
    assert "empty" in result.optimization_notes.lower()


def test_rewrite_for_claude():
    rewriter = PromptRewriter()
    result = rewriter.rewrite(
        raw_prompt="hãy viết cho tôi hàm export dữ liệu ra excel",
        target_ai="claude",
        context="Dự án backend FastAPI",
    )
    assert result.target_ai == TargetAI.CLAUDE.value
    assert "<task>" in result.rewritten_prompt
    assert "</task>" in result.rewritten_prompt
    assert "<context>" in result.rewritten_prompt
    assert "FastAPI" in result.rewritten_prompt
    assert "<constraints>" in result.rewritten_prompt
    assert "<output_format>" in result.rewritten_prompt


def test_rewrite_for_gpt():
    rewriter = PromptRewriter()
    result = rewriter.rewrite(
        raw_prompt="tạo schema migration cho bảng users",
        target_ai="gpt-4o",
        context="PostgreSQL 16",
    )
    assert result.target_ai == TargetAI.GPT.value
    assert "### Goal" in result.rewritten_prompt
    assert "### Context" in result.rewritten_prompt
    assert "PostgreSQL 16" in result.rewritten_prompt
    assert "### Constraints" in result.rewritten_prompt
    assert "### Acceptance Criteria (Done)" in result.rewritten_prompt


def test_rewrite_for_reasoning_models():
    rewriter = PromptRewriter()
    result = rewriter.rewrite(
        raw_prompt="tối ưu thuật toán tìm đường đi ngắn nhất",
        target_ai="o3-mini",
    )
    assert result.target_ai == TargetAI.REASONING_O3.value
    assert "Task:" in result.rewritten_prompt
    assert "Hard Constraints:" in result.rewritten_prompt
    assert "Definition of Done:" in result.rewritten_prompt
    # Must NOT have CoT scaffolding
    assert "think step by step" not in result.rewritten_prompt.lower()
    # Must be compact
    assert len(result.rewritten_prompt.split()) < 150


def test_rewrite_for_coder_agent():
    rewriter = PromptRewriter()
    result = rewriter.rewrite(
        raw_prompt="sửa lỗi test flaky trong test_billing.py",
        target_ai="coder_agent",
        context="Repo my-agent test suite",
    )
    assert result.target_ai == TargetAI.CODER_AGENT.value
    assert "### Actionable Objective" in result.rewritten_prompt
    assert "### Strict Implementation Boundaries" in result.rewritten_prompt
    assert "### Verification Gate" in result.rewritten_prompt


@pytest.mark.asyncio
async def test_mcp_rewrite_prompt():
    res = await server.rewrite_prompt(
        raw_prompt="hãy viết API đăng ký tài khoản có hash mật khẩu bcrypt",
        target_ai="coder_agent",
        context="Laravel 11 backend",
    )
    assert isinstance(res, dict)
    assert "rewritten_prompt" in res
    assert "target_ai" in res
    assert res["target_ai"] == "coder_agent"
    assert "dimensions" in res
    assert "optimization_notes" in res
    assert "Actionable Objective" in str(res["rewritten_prompt"])
