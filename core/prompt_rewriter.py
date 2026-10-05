"""Prompt Rewriter and Optimizer Module for MyAgent.

Inspired by the prompt-master framework, this module extracts intent across
key dimensions and generates production-grade, load-bearing prompts tailored
to specific AI models and agentic runtimes without wasting tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TargetAI(str, Enum):
    """Target AI system or execution surface."""

    CLAUDE = "claude"
    GPT = "gpt"
    REASONING_O3 = "reasoning_o3"
    GEMINI = "gemini"
    CODER_AGENT = "coder_agent"
    GENERAL = "general"

    @classmethod
    def from_str(cls, value: str) -> TargetAI:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if "claude" in normalized or "anthropic" in normalized:
            return cls.CLAUDE
        if "o1" in normalized or "o3" in normalized or "o4" in normalized or "reason" in normalized:
            return cls.REASONING_O3
        if "gpt" in normalized or "openai" in normalized or "chatgpt" in normalized:
            return cls.GPT
        if "gemini" in normalized or "google" in normalized:
            return cls.GEMINI
        if "coder" in normalized or "agent" in normalized or "codex" in normalized or "cursor" in normalized:
            return cls.CODER_AGENT
        return cls.GENERAL


@dataclass(frozen=True)
class PromptRewriteRequest:
    """Input specification for prompt rewriting."""

    raw_prompt: str
    target_ai: str = "general"
    context: str = ""
    language: str = "vi"


@dataclass(frozen=True)
class PromptRewriteResult:
    """Optimized prompt result with extracted dimensions and metadata."""

    rewritten_prompt: str
    target_ai: str
    optimization_notes: str
    dimensions: dict[str, str] = field(default_factory=dict)
    setup_instructions: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rewritten_prompt": self.rewritten_prompt,
            "target_ai": self.target_ai,
            "optimization_notes": self.optimization_notes,
            "dimensions": self.dimensions,
            "setup_instructions": self.setup_instructions,
        }


class PromptRewriter:
    """Core Prompt Rewriting and Optimization Engine."""

    def __init__(self) -> None:
        pass

    def rewrite(
        self,
        raw_prompt: str,
        target_ai: str = "general",
        context: str = "",
        language: str = "vi",
    ) -> PromptRewriteResult:
        """Rewrite and optimize a raw prompt according to target AI capabilities."""
        cleaned_raw = raw_prompt.strip()
        if not cleaned_raw:
            return PromptRewriteResult(
                rewritten_prompt="",
                target_ai=target_ai,
                optimization_notes="Input prompt was empty.",
                dimensions={},
                setup_instructions="",
            )

        target = TargetAI.from_str(target_ai)
        dimensions = self._extract_dimensions(cleaned_raw, context, language)

        if target == TargetAI.CLAUDE:
            rewritten, notes, setup = self._format_claude(dimensions, language)
        elif target == TargetAI.GPT:
            rewritten, notes, setup = self._format_gpt(dimensions, language)
        elif target == TargetAI.REASONING_O3:
            rewritten, notes, setup = self._format_reasoning(dimensions, language)
        elif target == TargetAI.GEMINI:
            rewritten, notes, setup = self._format_gemini(dimensions, language)
        elif target == TargetAI.CODER_AGENT:
            rewritten, notes, setup = self._format_coder_agent(dimensions, language)
        else:
            rewritten, notes, setup = self._format_general(dimensions, language)

        return PromptRewriteResult(
            rewritten_prompt=rewritten,
            target_ai=target.value,
            optimization_notes=notes,
            dimensions=dimensions,
            setup_instructions=setup,
        )

    def _extract_dimensions(self, raw: str, context: str, language: str) -> dict[str, str]:
        """Extract key intent dimensions from raw input and normalize to technical English."""
        task_action = self._derive_task_action(raw)
        constraints = self._derive_constraints(raw)
        success_criteria = self._derive_success_criteria(raw)
        output_format = self._derive_output_format(raw)

        return {
            "task": task_action,
            "raw_input": raw,
            "context": context.strip(),
            "constraints": constraints,
            "success_criteria": success_criteria,
            "output_format": output_format,
            "language": language,
        }

    _VI_EN_PHRASE_MAPPINGS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"(?i)\bviết\s+hàm\s+export\s+dữ\s+liệu\s+ra\s+excel\b"), "Implement data export function to Excel format (.xlsx)"),
        (re.compile(r"(?i)\btạo\s+schema\s+migration\s+cho\s+bảng\s+(\w+)\b"), r"Create database schema migration for the \1 table"),
        (re.compile(r"(?i)\bsửa\s+lỗi\s+test\s+flaky\s+trong\s+(\S+)\b"), r"Fix flaky test failure in \1"),
        (re.compile(r"(?i)\btối\s+ưu\s+thuật\s+toán\s+tìm\s+đường\s+đi\s+ngắn\s+nhất\b"), "Optimize shortest-path algorithm for execution latency and memory efficiency"),
        (re.compile(r"(?i)\bviết\s+API\s+đăng\s+ký\s+tài\s+khoản\s+có\s+hash\s+mật\s+khẩu\s+bcrypt\b"), "Implement account registration API with bcrypt password hashing"),
        (re.compile(r"(?i)\bviết\s+api\s+đăng\s+nhập\b"), "Implement authentication/login API endpoint"),
        (re.compile(r"(?i)\bviết\s+api\s+đăng\s+ký\b"), "Implement user registration API endpoint"),
        (re.compile(r"(?i)\bthiết\s+kế\s+giao\s+diện\b"), "Design and implement user interface"),
        (re.compile(r"(?i)\btối\s+ưu\s+hiệu\s+năng\b"), "Optimize runtime performance and resource utilization"),
        (re.compile(r"(?i)\btái\s+cấu\s+trúc\b"), "Refactor code module for clean architecture"),
        (re.compile(r"(?i)\bviết\s+unit\s+test\b"), "Author comprehensive unit test suite"),
        (re.compile(r"(?i)\bviết\s+(?:cho\s+tôi\s+)?hàm\s+"), "Implement function: "),
        (re.compile(r"(?i)\bviết\s+api\s+"), "Implement API: "),
        (re.compile(r"(?i)\btạo\s+bảng\s+"), "Create table schema: "),
        (re.compile(r"(?i)\btạo\s+trang\s+"), "Build page: "),
        (re.compile(r"(?i)\bsửa\s+lỗi\s+"), "Fix bug: "),
        (re.compile(r"(?i)\bquét\s+bảo\s+mật\s*"), "Run security vulnerability scan"),
        (re.compile(r"(?i)\bchạy\s+test\s*"), "Execute test suite"),
    )

    def _derive_task_action(self, raw: str) -> str:
        """Normalize vague verbs to precise technical instructions in English."""
        text = raw.strip()
        # Clean common conversational openers
        text = re.sub(
            r"^(hãy|hãy giúp tôi|giúp em|giúp a|làm cho tôi|viết cho tôi|hãy viết|please|write a|can you)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        )

        for pattern, replacement in self._VI_EN_PHRASE_MAPPINGS:
            if pattern.search(text):
                text = pattern.sub(replacement, text)
                break

        text = text[0].upper() + text[1:] if text else raw
        return text

    def _derive_constraints(self, raw: str) -> str:
        """Identify constraints or establish essential guardrails in Technical English."""
        negative_rules = []
        if re.search(r"\b(không|không được|đừng|tránh|never|do not|no)\b", raw, re.IGNORECASE):
            matches = re.findall(r"(?:không|không được|đừng|never|do not)\s+[^,.;\n]+", raw, re.IGNORECASE)
            for m in matches:
                r_clean = re.sub(r"^(không được|không|đừng)\s+", "Do not ", m.strip(), flags=re.IGNORECASE)
                negative_rules.append(r_clean)

        base_constraints = [
            "Ensure complete, production-ready implementation with defensive error handling.",
            "Strictly adhere to existing architectural conventions; do not introduce unnecessary abstractions.",
            "Zero placeholders, dummy mock objects, or incomplete TODOs.",
        ]
        if negative_rules:
            base_constraints.extend([f"Mandatory constraint: {r}" for r in negative_rules])
        return "\n".join(f"- {c}" for c in base_constraints)

    def _derive_success_criteria(self, raw: str) -> str:
        """Derive falsifiable verification criteria in Technical English."""
        return (
            "- All unit and integration tests pass cleanly with zero regressions.\n"
            "- Code satisfies strict type checking and exhibits zero syntax or runtime errors."
        )

    def _derive_output_format(self, raw: str) -> str:
        """Determine expected output shape in Technical English."""
        if re.search(r"\b(test|kiểm thử)\b", raw, re.IGNORECASE):
            return "Self-contained, runnable test suite with explicit assertions and verification logic."
        if re.search(r"\b(api|controller|endpoint|route)\b", raw, re.IGNORECASE):
            return "Production-ready API implementation with request validation, schemas, and typed responses."
        return "Complete, production-ready source code with necessary unit tests and type annotations."

    def _format_claude(self, dims: dict[str, str], lang: str) -> tuple[str, str, str]:
        """Format for Claude (Opus/Sonnet) using semantic XML tags."""
        context_block = f"\n<context>\n{dims['context']}\n</context>" if dims["context"] else ""
        prompt = (
            f"<task>\n{dims['task']}\n</task>"
            f"{context_block}\n"
            f"<constraints>\n{dims['constraints']}\n</constraints>\n"
            f"<verification>\n{dims['success_criteria']}\n</verification>\n"
            f"<output_format>\n{dims['output_format']}\n</output_format>"
        )
        notes = "Structured with semantic Claude XML tags in Technical English for optimal reasoning and prompt grounding."
        setup = "Paste directly into Claude (Opus/Sonnet) or send via Anthropic Messages API."
        return prompt, notes, setup

    def _format_gpt(self, dims: dict[str, str], lang: str) -> tuple[str, str, str]:
        """Format for OpenAI GPT (GPT-4o/GPT-5) using 4 compact sections."""
        ctx = f"\n### Context\n{dims['context']}\n" if dims["context"] else ""
        prompt = (
            f"### Goal\n{dims['task']}\n"
            f"{ctx}"
            f"### Constraints\n{dims['constraints']}\n\n"
            f"### Acceptance Criteria (Done)\n{dims['success_criteria']}\n\n"
            f"### Output Contract\n{dims['output_format']}"
        )
        notes = "Formatted into 4 core blocks (Goal, Context, Constraints, Acceptance Criteria) in Technical English."
        setup = "Paste into ChatGPT or execute via Chat Completions API."
        return prompt, notes, setup

    def _format_reasoning(self, dims: dict[str, str], lang: str) -> tuple[str, str, str]:
        """Format for reasoning models (o1, o3, o4-mini) - minimalist, no CoT scaffolding."""
        ctx_line = f" Context: {dims['context']}." if dims["context"] else ""
        prompt = (
            f"Task: {dims['task']}.{ctx_line}\n\n"
            f"Hard Constraints:\n{dims['constraints']}\n\n"
            f"Definition of Done:\n{dims['success_criteria']}"
        )
        notes = "Ultra-concise (< 150 words) Technical English prompt without CoT scaffolding, maximizing reasoning performance."
        setup = "Directly supply to OpenAI o1/o3/o4 reasoning models."
        return prompt, notes, setup

    def _format_gemini(self, dims: dict[str, str], lang: str) -> tuple[str, str, str]:
        """Format for Google Gemini models - grounded, directive markdown."""
        ctx = f"\n### System Context\n{dims['context']}\n" if dims["context"] else ""
        prompt = (
            f"### Objective\n{dims['task']}\n"
            f"{ctx}"
            f"### Technical Constraints\n{dims['constraints']}\n\n"
            f"### Expected Deliverables\n{dims['output_format']}\n\n"
            f"### Definition of Done\n{dims['success_criteria']}"
        )
        notes = "Optimized for Gemini with directive, grounded technical English instructions."
        setup = "Paste into Gemini Pro or invoke via Gemini SDK/API."
        return prompt, notes, setup

    def _format_coder_agent(self, dims: dict[str, str], lang: str) -> tuple[str, str, str]:
        """Format for Coding Agents (Antigravity, Cursor, Codex, Specialist Coders)."""
        ctx = f"\n### Project & Workspace Context\n{dims['context']}\n" if dims["context"] else ""
        prompt = (
            f"### Actionable Objective\n{dims['task']}\n"
            f"{ctx}"
            f"### Strict Implementation Boundaries\n"
            f"{dims['constraints']}\n"
            f"- Preserve all existing unrelated comments and docstrings.\n"
            f"- Apply precise, surgical diffs without uncontrolled whole-file overwrites.\n"
            f"- Execute automated test suites and verify zero regressions before concluding.\n\n"
            f"### Expected Deliverables\n{dims['output_format']}\n\n"
            f"### Verification Gate\n{dims['success_criteria']}"
        )
        notes = "Deeply tailored for Coding Agents with explicit boundary constraints, diff fidelity, and verification gates."
        setup = "Dispatch to Coder Session or subagent execution loop."
        return prompt, notes, setup

    def _format_general(self, dims: dict[str, str], lang: str) -> tuple[str, str, str]:
        """Format for general AI assistants."""
        ctx = f"\n### Context\n{dims['context']}\n" if dims["context"] else ""
        prompt = (
            f"### Primary Objective\n{dims['task']}\n"
            f"{ctx}"
            f"### Technical Constraints\n{dims['constraints']}\n\n"
            f"### Deliverables\n{dims['output_format']}\n\n"
            f"### Acceptance Criteria\n{dims['success_criteria']}"
        )
        notes = "Standardized general prompt structure following Prompt Master principles in Technical English."
        setup = "Ready to supply to any downstream model."
        return prompt, notes, setup
