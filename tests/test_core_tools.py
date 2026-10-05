from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.context import ExecutionContext
from core.errors import PermissionDeniedError
from core.tool_executor import ToolExecutor
from core.tool_policy import ToolPolicy
from core.tool_registry import ToolRegistry
from core.tools import ToolDefinition, ToolPermission


async def echo_handler(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    return {'workspace_id': context.workspace_id, 'arguments': dict(arguments)}


def make_executor(tool: ToolDefinition) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, ToolPolicy())


def test_execution_context_rejects_empty_workspace() -> None:
    with pytest.raises(ValueError):
        ExecutionContext(workspace_id='   ')


@pytest.mark.asyncio
async def test_read_tool_executes() -> None:
    tool = ToolDefinition(
        name='echo',
        description='Echo arguments',
        input_schema={'type': 'object'},
        handler=echo_handler,
    )
    executor = make_executor(tool)
    context = ExecutionContext(workspace_id='workspace-1')

    result = await executor.execute('echo', {'value': 42}, context)

    assert result['workspace_id'] == 'workspace-1'
    assert result['arguments'] == {'value': 42}


@pytest.mark.asyncio
async def test_write_tool_requires_user_scope() -> None:
    tool = ToolDefinition(
        name='write_file',
        description='Write a file',
        input_schema={'type': 'object'},
        handler=echo_handler,
        permissions=frozenset({ToolPermission.WRITE}),
    )
    executor = make_executor(tool)
    context = ExecutionContext(workspace_id='workspace-1')

    with pytest.raises(PermissionDeniedError):
        await executor.execute('write_file', {}, context)


@pytest.mark.asyncio
async def test_destructive_tool_requires_approval() -> None:
    tool = ToolDefinition(
        name='delete_file',
        description='Delete a file',
        input_schema={'type': 'object'},
        handler=echo_handler,
        permissions=frozenset({ToolPermission.DESTRUCTIVE}),
    )
    executor = make_executor(tool)
    context = ExecutionContext(
        workspace_id='workspace-1',
        user_id='user-1',
    )

    result = await executor.execute('delete_file', {}, context)

    assert result['status'] == 'approval_required'
    assert result['tool'] == 'delete_file'


@pytest.mark.asyncio
async def test_destructive_tool_executes_after_explicit_approval() -> None:
    tool = ToolDefinition(
        name='delete_file',
        description='Delete a file',
        input_schema={'type': 'object'},
        handler=echo_handler,
        permissions=frozenset({ToolPermission.DESTRUCTIVE}),
    )
    executor = make_executor(tool)
    context = ExecutionContext(
        workspace_id='workspace-1',
        user_id='user-1',
    )

    result = await executor.execute(
        'delete_file',
        {'path': 'old.txt'},
        context,
        approved=True,
    )

    assert result == {
        'workspace_id': 'workspace-1',
        'arguments': {'path': 'old.txt'},
    }


def test_tool_registry_duplicate_registration_raises_error() -> None:
    tool = ToolDefinition(
        name='duplicate_tool',
        description='A test tool',
        input_schema={'type': 'object'},
        handler=echo_handler,
    )
    registry = ToolRegistry()
    registry.register(tool)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool)


def test_tool_registry_get_unknown_tool_raises_keyerror() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError, match="unknown tool"):
        registry.get("non_existent_tool")


def test_tool_registry_exposed_to_and_register_many() -> None:
    from core.tools import ToolExposure

    tool_a = ToolDefinition(
        name='tool_internal',
        description='Internal only tool',
        input_schema={'type': 'object'},
        handler=echo_handler,
        exposure=frozenset({ToolExposure.INTERNAL}),
    )
    tool_b = ToolDefinition(
        name='tool_mcp',
        description='MCP exposed tool',
        input_schema={'type': 'object'},
        handler=echo_handler,
        exposure=frozenset({ToolExposure.MCP}),
    )

    registry = ToolRegistry()
    registry.register_many([tool_a, tool_b])

    assert len(registry.all()) == 2
    assert registry.get("tool_internal") == tool_a

    internal_tools = registry.exposed_to(ToolExposure.INTERNAL)
    assert len(internal_tools) == 1
    assert internal_tools[0].name == "tool_internal"

    mcp_tools = registry.exposed_to(ToolExposure.MCP)
    assert len(mcp_tools) == 1
    assert mcp_tools[0].name == "tool_mcp"

