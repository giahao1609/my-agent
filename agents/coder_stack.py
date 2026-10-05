from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.orchestrator import Orchestrator
from core.sandbox_runtime import SandboxRuntime
from core.tool_executor import ToolExecutor
from core.tool_policy import ToolPolicy
from core.tool_registry import ToolRegistry
from integrations.code_graph_knowledge import CodeGraphKnowledgeBackend
from integrations.runtime_bridge import RuntimeBridge
from integrations.sqlite_memory_backend import SQLiteMemoryBackend
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore
from persistence.sqlite_coder_session_store import SQLiteCoderSessionStore
from persistence.sqlite_memory_store import SQLiteMemoryStore
from tools.coder_tools import build_coder_tool_registry

from .coder_agent import CoderAgent


@dataclass(frozen=True, slots=True)
class CoderAgentStack:
    agent: CoderAgent
    runtime: RuntimeBridge
    memory: SQLiteMemoryBackend
    knowledge: CodeGraphKnowledgeBackend
    session_store: SQLiteCoderSessionStore
    tool_registry: ToolRegistry
    orchestrator: Orchestrator


async def build_coder_agent_stack(
    database_path: str | Path,
    *,
    sandbox_runtime: SandboxRuntime | None = None,
) -> CoderAgentStack:
    memory_store = SQLiteMemoryStore(database_path)
    code_graph_store = SQLiteCodeGraphStore(database_path)
    session_store = SQLiteCoderSessionStore(database_path)

    await memory_store.initialize()
    await code_graph_store.initialize()
    await session_store.initialize()

    runtime = RuntimeBridge()
    memory = SQLiteMemoryBackend(memory_store)
    knowledge = CodeGraphKnowledgeBackend(code_graph_store)
    tool_registry = build_coder_tool_registry(
        memory,
        sandbox_runtime,
    )

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=memory,
        knowledge=knowledge,
        tools=ToolExecutor(
            tool_registry,
            ToolPolicy(),
        ),
        session_store=session_store,
        sandbox_runtime=sandbox_runtime,
    )

    agent = CoderAgent(orchestrator)

    return CoderAgentStack(
        agent=agent,
        runtime=runtime,
        memory=memory,
        knowledge=knowledge,
        session_store=session_store,
        tool_registry=tool_registry,
        orchestrator=orchestrator,
    )
