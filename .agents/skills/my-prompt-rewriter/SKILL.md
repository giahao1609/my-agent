---
name: my-prompt-rewriter
description: Optimize, structure, and translate raw prompts into high-performance, load-bearing Technical English prompts via the Prompt Master framework.
---

# Prompt Rewriter & Optimizer Protocol (Prompt Master)

Transforms raw, conversational user requests into **production-grade, load-bearing Technical English prompts** optimized for specific target execution runtimes with zero wasted tokens.

## Core Principles (Prompt Master Framework)

1. **Dimensional Intent Extraction**:
   - **Task**: Normalize ambiguous verbs into precise, scoped technical instructions in English.
   - **Context**: Inject project architecture, dependencies, framework hints, and workspace boundaries.
   - **Constraints**: Define non-negotiable boundaries, defensive error handling, and zero-stubbing rules.
   - **Success Criteria**: Define falsifiable, binary acceptance criteria (tests pass, typing verified).
   - **Output Format**: Establish exact deliverables (production code, schema migration, runnable test suite).

2. **Target-Specific Scaffolding**:
   - **Reasoning Models (o1/o3/o4)**: Minimalist prompt (< 150 words) with only Objective, Hard Constraints, and Definition of Done. Zero CoT scaffolding.
   - **Claude (Opus/Sonnet)**: Semantic XML tags (`<task>`, `<context>`, `<constraints>`, `<output_format>`).
   - **OpenAI GPT (GPT-4o)**: 4 compact sections (Goal, Context, Constraints, Acceptance Criteria).
   - **Coding Agents (Antigravity/Cursor/Codex)**: Strict implementation boundaries, precise diff expectations, and verification gates.

3. **Multilingual Translation to Technical English**:
   - Translates technical requests from any language (e.g. Vietnamese) into unambiguous Technical English so execution models activate their deepest pre-trained engineering weights.

## MCP Tool Invocation

Call `rewrite_prompt` on the `my-agent` server:

```python
result = await rewrite_prompt(
    raw_prompt="viết hàm tính doanh thu theo tháng từ bảng hóa đơn",
    target_ai="coder_agent",  # or 'claude', 'gpt', 'reasoning_o3', 'gemini', 'general'
    context="Laravel 11 backend with MySQL invoices table",
    language="en",
)
```

The returned payload provides:
- `rewritten_prompt`: Production-ready prompt tailored for the downstream execution model.
- `target_ai`: Normalized target architecture identifier.
- `optimization_notes`: Technical explanation of applied optimizations.
- `dimensions`: Extracted task dimensions (task, constraints, success criteria, deliverables).
- `setup_instructions`: Operational setup recommendations before sending prompt.

