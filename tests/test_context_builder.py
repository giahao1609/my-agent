"""Unit and integration test suite for Phase 06B — Unified Context Assembly Engine (ContextBuilder)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent_role import AgentRole
from core.code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind
from core.context_builder import (
    CodeSymbolContext,
    MemoryContextItem,
    UnifiedContext,
    UnifiedContextBuilder,
    UserPreferenceContextItem,
)
from core.durable_memory_service import DurableMemoryService, MemoryMatch
from core.handoff_contracts import ImplementationResult
from core.memory import MemoryLevel, MemoryRecord, MemoryStatus, MemoryType
from core.step_execution import CoderRuntimeStepExecutor
from core.user_model import (
    EffectiveUserProfile,
    PreferenceCategory,
    PreferenceItem,
    PreferenceSourceType,
    ProvenanceStatus,
)
from core.user_model_engine import UserModelEngine
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore
from persistence.sqlite_memory_store import SQLiteMemoryStore


# ─── 1. Validation & Safety ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_project_id_validation():
    builder = UnifiedContextBuilder()

    with pytest.raises(ValueError, match="project_id is mandatory"):
        await builder.build_context(project_id="")

    with pytest.raises(ValueError, match="project_id is mandatory"):
        await builder.build_context(project_id="   ")

    with pytest.raises(ValueError, match="RESERVED_SENTINEL"):
        await builder.build_context(project_id="__global__")


# ─── 2. UserModel Preferences Integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_user_model_preferences_and_redaction():
    mock_engine = MagicMock(spec=UserModelEngine)
    now = datetime.now(UTC)

    pref_style = PreferenceItem(
        preference_id="p1",
        logical_key="code_style",
        content="Strict typing with sk-ant-api012345678901234567890 secret",
        category=PreferenceCategory.TECHNICAL,
        scope="project",
        project_id="proj-alpha",
        source_type=PreferenceSourceType.EXPLICIT_USER,
        provenance_status=ProvenanceStatus.VERIFIED_EXPLICIT,
        confidence=1.0,
        importance=0.9,
        status=MemoryStatus.ACTIVE,
        tier=MemoryLevel.L2,
        created_at=now,
    )
    pref_framework = PreferenceItem(
        preference_id="p2",
        logical_key="framework",
        content="Prefer FastAPI over Flask",
        category=PreferenceCategory.TECHNICAL,
        scope="global",
        project_id=None,
        source_type=PreferenceSourceType.EXPLICIT_USER,
        provenance_status=ProvenanceStatus.VERIFIED_EXPLICIT,
        confidence=1.0,
        importance=0.8,
        status=MemoryStatus.ACTIVE,
        tier=MemoryLevel.L3,
        created_at=now,
    )
    pref_minor = PreferenceItem(
        preference_id="p3",
        logical_key="comments",
        content="Minimal docstrings",
        category=PreferenceCategory.TECHNICAL,
        scope="global",
        project_id=None,
        source_type=PreferenceSourceType.INFERRED,
        provenance_status=ProvenanceStatus.INFERRED_HYPOTHESIS,
        confidence=0.6,
        importance=0.5,
        status=MemoryStatus.ACTIVE,
        tier=MemoryLevel.L3,
        created_at=now,
    )

    profile = EffectiveUserProfile(
        user_id="user-1",
        project_id="proj-alpha",
        preferences_by_key={
            "code_style": pref_style,
            "framework": pref_framework,
            "comments": pref_minor,
        },
    )
    mock_engine.get_effective_profile = AsyncMock(return_value=profile)

    builder = UnifiedContextBuilder(user_model_engine=mock_engine)
    ctx = await builder.build_context(
        project_id="proj-alpha",
        step_title="Build payment gateway",
        step_instruction="Implement checkout endpoint in payment.py",
        target_role=AgentRole.BACKEND_CODER,
    )

    assert len(ctx.user_preferences) == 3
    # Check precedence ordering: Rank 5 -> Rank 4 -> Rank 2
    assert ctx.user_preferences[0].logical_key == "code_style"
    assert ctx.user_preferences[0].precedence_rank == 5
    assert ctx.user_preferences[1].logical_key == "framework"
    assert ctx.user_preferences[1].precedence_rank == 4
    assert ctx.user_preferences[2].logical_key == "comments"
    assert ctx.user_preferences[2].precedence_rank == 2

    # Verify secret was scrubbed in formatted output and preference items
    assert "[REDACTED_ANTHROPIC_KEY]" in ctx.user_preferences[0].content
    assert "sk-ant-" not in ctx.user_preferences[0].content
    assert "### Active User Preferences & Directives" in ctx.formatted_prompt_section
    assert "[Rank 5] `code_style`" in ctx.formatted_prompt_section


# ─── 3. Durable Memory Integration ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_durable_memory_retrieval():
    mock_memory = MagicMock(spec=DurableMemoryService)

    rec1 = MemoryRecord(
        memory_id="mem-1",
        project_id="proj-alpha",
        level=MemoryLevel.L2,
        memory_type=MemoryType.PROJECT,
        subject="Database ORM Policy",
        content="Always use SQLAlchemy 2.0 with async engine for auth_token='secret_pass123'",
        importance=0.9,
        status=MemoryStatus.ACTIVE,
    )
    rec2 = MemoryRecord(
        memory_id="mem-2",
        project_id=None,
        level=MemoryLevel.L3,
        memory_type=MemoryType.LESSON,
        subject="Error Handling Standard",
        content="Use domain-specific custom exceptions instead of generic RuntimeError",
        importance=0.85,
        status=MemoryStatus.ACTIVE,
    )

    match1 = MemoryMatch(record=rec1, score=1.8, reasons=("token_overlap:3",))
    match2 = MemoryMatch(record=rec2, score=1.4, reasons=("token_overlap:2",))

    mock_memory.retrieve = AsyncMock(return_value=(match1, match2))

    builder = UnifiedContextBuilder(memory_service=mock_memory)
    ctx = await builder.build_context(
        project_id="proj-alpha",
        step_title="Setup user database model",
        step_instruction="Create user.py with database models",
    )

    assert len(ctx.memories) == 2
    assert ctx.memories[0].subject == "Database ORM Policy"
    assert ctx.memories[0].importance == 0.9
    assert ctx.memories[1].subject == "Error Handling Standard"

    # Redaction check
    assert "[REDACTED_SECRET]" in ctx.memories[0].content
    assert "secret_pass123" not in ctx.memories[0].content

    # Output formatting check
    assert "### Architectural Decisions & Project Memory" in ctx.formatted_prompt_section
    assert "[L2] **Database ORM Policy**" in ctx.formatted_prompt_section


# ─── 4. CodeGraph AST Symbols Integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_codegraph_ast_symbols():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_graph.db"
        graph = SQLiteCodeGraphStore(db_path)
        await graph.initialize()

        node_func = CodeNode(
            project_id="proj-alpha",
            node_id="src/payment.py:process_payment",
            name="process_payment",
            kind=CodeNodeKind.FUNCTION,
            path="src/payment.py",
            line_start=15,
            line_end=45,
        )
        node_class = CodeNode(
            project_id="proj-alpha",
            node_id="src/payment.py:PaymentGateway",
            name="PaymentGateway",
            kind=CodeNodeKind.CLASS,
            path="src/payment.py",
            line_start=5,
            line_end=50,
        )
        node_dep = CodeNode(
            project_id="proj-alpha",
            node_id="src/stripe.py:charge_card",
            name="charge_card",
            kind=CodeNodeKind.FUNCTION,
            path="src/stripe.py",
            line_start=10,
            line_end=30,
        )

        edge_call = CodeEdge(
            project_id="proj-alpha",
            source_id="src/payment.py:process_payment",
            target_id="src/stripe.py:charge_card",
            kind=CodeEdgeKind.CALLS,
        )

        await graph.replace_project_graph(
            project_id="proj-alpha",
            nodes=[node_func, node_class, node_dep],
            edges=[edge_call],
        )

        builder = UnifiedContextBuilder(code_graph=graph)
        ctx = await builder.build_context(
            project_id="proj-alpha",
            step_title="Refactor process_payment in src/payment.py",
            step_instruction="Update error handling for process_payment",
            target_files=["src/payment.py"],
        )

        assert len(ctx.code_symbols) > 0
        names = [s.name for s in ctx.code_symbols]
        assert "process_payment" in names

        symbol_func = next(s for s in ctx.code_symbols if s.name == "process_payment")
        assert symbol_func.kind == "function"
        assert symbol_func.path == "src/payment.py"
        assert "charge_card" in symbol_func.dependencies

        assert "### Code Graph Intelligence & AST Signatures" in ctx.formatted_prompt_section
        assert "**FUNCTION** `process_payment` (src/payment.py:15-45)" in ctx.formatted_prompt_section
        assert "Dependencies: charge_card" in ctx.formatted_prompt_section


# ─── 5. UI Design Contract Role-Based Filtering ──────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_ui_design_contract():
    with tempfile.TemporaryDirectory() as tmpdir:
        design_dir = Path(tmpdir) / "designs"
        design_dir.mkdir(parents=True, exist_ok=True)

        contract_file = design_dir / "proj-edtech.design.md"
        contract_file.write_text(
            "# Design System — Proj Edtech\n"
            "- Visual Language: Cognitive Focus\n"
            "- Surface Mode: earn\n"
            "- Layout: bento_asymmetric\n"
            "- Rules: active:scale-[0.985] on buttons\n",
            encoding="utf-8",
        )

        builder = UnifiedContextBuilder(design_dir=design_dir)

        # 1. UI Coder role -> Design Contract INCLUDED
        ctx_ui = await builder.build_context(
            project_id="proj-edtech",
            step_title="Design student gradebook",
            target_role=AgentRole.UI_CODER,
        )
        assert ctx_ui.design_contract is not None
        assert "Cognitive Focus" in ctx_ui.design_contract
        assert "### UI Design System Contract" in ctx_ui.formatted_prompt_section

        # 2. Backend Coder role -> Design Contract EXCLUDED
        ctx_backend = await builder.build_context(
            project_id="proj-edtech",
            step_title="Compute student GPA",
            target_role=AgentRole.BACKEND_CODER,
        )
        assert ctx_backend.design_contract is None
        assert "### UI Design System Contract" not in ctx_backend.formatted_prompt_section


# ─── 6. Token Budgeting & Deterministic Pruning ───────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_deterministic_pruning_under_tight_budget():
    mock_engine = MagicMock(spec=UserModelEngine)
    now = datetime.now(UTC)
    prefs = {}
    for i in range(1, 10):
        is_expl = i % 2 == 0
        src = PreferenceSourceType.EXPLICIT_USER if is_expl else PreferenceSourceType.INFERRED
        prov = ProvenanceStatus.VERIFIED_EXPLICIT if is_expl else ProvenanceStatus.INFERRED_HYPOTHESIS
        prefs[f"pref_{i}"] = PreferenceItem(
            preference_id=f"p_{i}",
            logical_key=f"pref_{i}",
            content=f"Detailed engineering directive number {i} with extensive explanations and guidelines.",
            category=PreferenceCategory.TECHNICAL,
            scope="project" if i % 3 != 0 else "global",
            project_id="proj-alpha" if i % 3 != 0 else None,
            source_type=src,
            provenance_status=prov,
            confidence=1.0,
            importance=0.8,
            status=MemoryStatus.ACTIVE,
            tier=MemoryLevel.L2 if i % 3 != 0 else MemoryLevel.L3,
            created_at=now,
        )

    mock_engine.get_effective_profile = AsyncMock(
        return_value=EffectiveUserProfile(
            user_id="user-1",
            project_id="proj-alpha",
            preferences_by_key=prefs,
        )
    )

    mock_memory = MagicMock(spec=DurableMemoryService)
    matches = tuple(
        MemoryMatch(
            record=MemoryRecord(
                memory_id=f"mem-{i}",
                project_id="proj-alpha",
                level=MemoryLevel.L2,
                memory_type=MemoryType.PROJECT,
                subject=f"System Architecture Policy {i}",
                content=f"Long text describing architectural constraint {i} and implementation invariants.",
                importance=0.5 if i > 3 else 0.95,
                status=MemoryStatus.ACTIVE,
            ),
            score=2.0 - (i * 0.1),
            reasons=(),
        )
        for i in range(1, 8)
    )
    mock_memory.retrieve = AsyncMock(return_value=matches)

    builder = UnifiedContextBuilder(
        user_model_engine=mock_engine,
        memory_service=mock_memory,
    )

    # Tight budget: 256 tokens (~1024 characters)
    ctx = await builder.build_context(
        project_id="proj-alpha",
        step_title="Optimize database query latency",
        step_instruction="Refactor SQL indices and query planners",
        target_role=AgentRole.BACKEND_CODER,
        token_budget=256,
    )

    assert ctx.token_count_estimate <= 256
    assert ctx.pruned_items_count > 0
    # Higher rank preferences (Rank >= 3) should survive over rank 1-2
    for p in ctx.user_preferences:
        assert p.precedence_rank >= 3


# ─── 7. Integration with CoderRuntimeStepExecutor ─────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_wired_into_step_executor():
    mock_builder = MagicMock(spec=UnifiedContextBuilder)
    sample_context = UnifiedContext(
        project_id="proj-test",
        task_id="task-1",
        step_id="step-1",
        target_role=AgentRole.BACKEND_CODER,
        formatted_prompt_section="## Unified Context\n- [Rank 5] Use async/await\n- [L2_PROJECT] Schema: User table",
        token_count_estimate=35,
        pruned_items_count=0,
    )
    mock_builder.build_context = AsyncMock(return_value=sample_context)

    # Setup mocked coder stack and orchestrator
    mock_stack = MagicMock()
    mock_agent = MagicMock()

    # Capture prompt passed to run_session
    captured_prompts: list[str] = []

    async def fake_run_session(session_id, prompt, context):
        captured_prompts.append(prompt)
        yield MagicMock(type=MagicMock(value="stop"), payload={"reason": "completed"})

    mock_agent.run_session = fake_run_session
    mock_stack.agent = mock_agent
    mock_stack.orchestrator = MagicMock()
    mock_stack.orchestrator.start_session = AsyncMock(return_value="sess-123")

    async def fake_launcher(session_id):
        # Dummy completed worker task
        async def dummy():
            pass
        return asyncio.create_task(dummy())

    executor = CoderRuntimeStepExecutor(
        coder_stack=mock_stack,
        workspace_path="/tmp",
        project_id="proj-test",
        task_id="task-1",
        plan_id="plan-1",
        worker_task_launcher=fake_launcher,
        context_builder=mock_builder,
    )

    result = await executor.execute_step(
        step_id="step-1",
        step_title="Create User entity",
        step_role=AgentRole.BACKEND_CODER,
        step_instruction="Define fields: id, username, email",
    )

    mock_builder.build_context.assert_awaited_once_with(
        project_id="proj-test",
        task_id="task-1",
        step_id="step-1",
        step_title="Create User entity",
        step_instruction="Define fields: id, username, email",
        target_role=AgentRole.BACKEND_CODER,
        user_id="authorized-user",
    )

    assert len(captured_prompts) == 1
    prompt_delivered = captured_prompts[0]
    assert "Execute step 'Create User entity' for role 'backend_coder'." in prompt_delivered
    assert "## Unified Context" in prompt_delivered
    assert "[Rank 5] Use async/await" in prompt_delivered
    assert "[L2_PROJECT] Schema: User table" in prompt_delivered


# ─── 8. Graceful Degradation & Empty Services ────────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_graceful_degradation_without_services():
    builder = UnifiedContextBuilder()
    ctx = await builder.build_context(
        project_id="proj-minimal",
        step_title="Create basic README",
        step_instruction="Add project description and setup steps",
        target_role=AgentRole.DOCUMENTATION,
    )

    assert ctx.project_id == "proj-minimal"
    assert ctx.target_role == AgentRole.DOCUMENTATION
    assert len(ctx.user_preferences) == 0
    assert len(ctx.memories) == 0
    assert len(ctx.code_symbols) == 0
    assert ctx.design_contract is None
    assert "### Execution Scope & Role" in ctx.formatted_prompt_section
    assert "proj-minimal" in ctx.formatted_prompt_section


# ─── 9. Project Isolation Security Invariant ─────────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_strict_project_isolation():
    mock_memory = MagicMock(spec=DurableMemoryService)

    # When retrieve is called, verify that project_id is exactly passed through
    async def fake_retrieve(project_id, query, **kwargs):
        if project_id == "proj-target":
            return (
                MemoryMatch(
                    record=MemoryRecord(
                        memory_id="mem-target",
                        project_id="proj-target",
                        level=MemoryLevel.L2,
                        memory_type=MemoryType.PROJECT,
                        subject="Target Architecture",
                        content="Target isolation rules",
                        importance=0.9,
                        status=MemoryStatus.ACTIVE,
                    ),
                    score=1.0,
                    reasons=(),
                ),
            )
        return ()

    mock_memory.retrieve = AsyncMock(side_effect=fake_retrieve)

    builder = UnifiedContextBuilder(memory_service=mock_memory)
    ctx = await builder.build_context(
        project_id="proj-target",
        step_title="Audit security",
    )

    assert len(ctx.memories) == 1
    assert ctx.memories[0].subject == "Target Architecture"
    mock_memory.retrieve.assert_called_once()
    assert mock_memory.retrieve.call_args[1]["project_id"] == "proj-target"


# ─── 10. MCP assemble_context Tool Integration ──────────────────────────────


@pytest.mark.asyncio
async def test_context_builder_mcp_assemble_context_tool(monkeypatch):
    import my_agent_mcp.server as server_mod

    # Setup active project mock
    mock_project = MagicMock()
    mock_project.project_id = "proj-mcp"
    server_mod.projects.get_active = AsyncMock(return_value=mock_project)

    # Assemble context via tool
    res = await server_mod.assemble_context(
        step_title="Implement authentication",
        project_id="proj-mcp",
        target_role="backend_coder",
        step_instruction="Add JWT verification",
    )

    assert "project_id" in res
    assert res["project_id"] == "proj-mcp"
    assert res["target_role"] == "backend_coder"
    assert "formatted_prompt_section" in res
    assert "## Unified Context" in res["formatted_prompt_section"]
