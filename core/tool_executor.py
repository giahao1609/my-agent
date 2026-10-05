from __future__ import annotations

from collections.abc import Mapping

from .context import ExecutionContext
from .errors import PermissionDeniedError
from .tool_policy import ToolDecision, ToolPolicy
from .tool_registry import ToolRegistry
from .tools import ToolResult


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolPolicy,
    ) -> None:
        self._registry = registry
        self._policy = policy

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, object],
        context: ExecutionContext,
        *,
        approved: bool = False,
    ) -> ToolResult:
        tool = self._registry.get(name)
        policy_result = self._policy.evaluate(tool, context, arguments=arguments)

        if policy_result.decision is ToolDecision.DENY:
            raise PermissionDeniedError(policy_result.reason)

        if (
            policy_result.decision is ToolDecision.REQUIRE_APPROVAL
            and not approved
        ):
            return {
                'status': 'approval_required',
                'tool': tool.name,
                'reason': policy_result.reason,
                'risk_explanation': policy_result.risk_explanation,
            }

        return await tool.handler(context, arguments)
