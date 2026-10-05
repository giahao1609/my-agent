from __future__ import annotations

from dataclasses import dataclass

from core.model import ExecutionTarget
from core.protocols import ModelBackend, ModelBackendProvider
from core.tool_registry import ToolRegistry
from integrations.runtime_bridge import RuntimeBridge

from .coder_runtime_worker import CoderRuntimeWorker
from .model_planner import ModelPlanner


@dataclass(frozen=True, slots=True)
class ModelAgentComposition:
    model: ModelBackend
    coder_worker: CoderRuntimeWorker
    planner: ModelPlanner


async def build_model_agent_composition(
    *,
    provider: ModelBackendProvider,
    runtime: RuntimeBridge,
    tools: ToolRegistry,
    planner_target: ExecutionTarget | None = None,
) -> ModelAgentComposition:
    model = await provider.get()

    coder_worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=tools,
    )

    planner = ModelPlanner(
        model=model,
        target=planner_target,
    )

    return ModelAgentComposition(
        model=model,
        coder_worker=coder_worker,
        planner=planner,
    )
