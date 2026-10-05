from __future__ import annotations

import pytest

from core.context import ExecutionContext
from core.tool_policy import ToolDecision, ToolPolicy
from core.tools import ToolDefinition, ToolPermission


@pytest.fixture
def policy() -> ToolPolicy:
    return ToolPolicy()


@pytest.fixture
def context() -> ExecutionContext:
    return ExecutionContext(workspace_id='workspace-1', user_id='user-1')


async def dummy_handler(ctx: ExecutionContext, args: dict) -> dict:
    return {}


def make_command_tool() -> ToolDefinition:
    return ToolDefinition(
        name='run_command',
        description='Run command',
        input_schema={'type': 'object'},
        handler=dummy_handler,
        permissions=frozenset({ToolPermission.EXECUTE}),
    )


def test_safe_test_and_build_commands_allowed(policy: ToolPolicy, context: ExecutionContext) -> None:
    tool = make_command_tool()
    safe_commands = [
        'pytest -v',
        'python -m unittest discover',
        'python3 -m pytest tests/test_core.py',
        'npm test',
        'npm run build',
        'npm run lint',
        'npm run typecheck',
        'cargo test',
        'cargo check',
        'cargo build',
        'go test ./...',
        'go vet ./...',
        'go build ./...',
        'gofmt -l .',
        'opa test /p/',
        'docker ps',
        'docker logs container_1',
        'git status',
        'git diff',
        'git log -n 5',
        'npx tsc --noEmit',
        'npx eslint src',
    ]
    for cmd in safe_commands:
        res = policy.evaluate(tool, context, {'command': cmd})
        assert res.decision == ToolDecision.ALLOW, f"Expected {cmd} to be ALLOW, got {res.decision}"


def test_package_install_requires_approval(policy: ToolPolicy, context: ExecutionContext) -> None:
    tool = make_command_tool()
    install_commands = [
        'npm install lodash',
        'npm i axios',
        'yarn add react-router',
        'pnpm add zustand',
        'pip install fastapi',
        'pip3 install requests',
        'go get github.com/gin-gonic/gin',
        'cargo add serde',
    ]
    for cmd in install_commands:
        res = policy.evaluate(tool, context, {'command': cmd})
        assert res.decision == ToolDecision.REQUIRE_APPROVAL, f"Expected {cmd} to REQUIRE_APPROVAL, got {res.decision}"
        assert 'Cài đặt thư viện mới' in res.reason


def test_docker_file_modification_requires_approval(policy: ToolPolicy, context: ExecutionContext) -> None:
    tool = ToolDefinition(
        name='replace_file_content',
        description='Edit file',
        input_schema={'type': 'object'},
        handler=dummy_handler,
        permissions=frozenset({ToolPermission.WRITE}),
    )
    docker_paths = [
        '/project/Dockerfile',
        '/project/Dockerfile.dev',
        '/project/docker-compose.yml',
        '/project/docker-compose.prod.yaml',
        '/project/Containerfile',
    ]
    for path in docker_paths:
        res = policy.evaluate(tool, context, {'target_file': path})
        assert res.decision == ToolDecision.REQUIRE_APPROVAL, f"Expected {path} to REQUIRE_APPROVAL, got {res.decision}"
        assert 'Docker/Container' in res.reason


def test_mass_modification_requires_approval(policy: ToolPolicy, context: ExecutionContext) -> None:
    tool = ToolDefinition(
        name='multi_replace_file_content',
        description='Multi edit file',
        input_schema={'type': 'object'},
        handler=dummy_handler,
        permissions=frozenset({ToolPermission.WRITE}),
    )
    small_chunks = [{'chunk': i} for i in range(5)]
    res_small = policy.evaluate(tool, context, {'target_file': 'src/app.ts', 'replacement_chunks': small_chunks})
    assert res_small.decision == ToolDecision.ALLOW

    large_chunks = [{'chunk': i} for i in range(15)]
    res_large = policy.evaluate(tool, context, {'target_file': 'src/app.ts', 'replacement_chunks': large_chunks})
    assert res_large.decision == ToolDecision.REQUIRE_APPROVAL
    assert 'quá nhiều khối mã nguồn' in res_large.reason


def test_read_only_tools_and_commands_always_allowed(policy: ToolPolicy, context: ExecutionContext) -> None:
    read_tool = ToolDefinition(
        name='view_file',
        description='Read file content',
        input_schema={'type': 'object'},
        handler=dummy_handler,
        permissions=frozenset({ToolPermission.READ}),
    )
    res_tool = policy.evaluate(read_tool, context, {'target_file': '/any/path/file.py'})
    assert res_tool.decision == ToolDecision.ALLOW

    cmd_tool = make_command_tool()
    read_commands = [
        'cat src/app.ts',
        'head -n 20 README.md',
        'tail -f log.txt',
        'grep -rn "TODO" .',
        'rg "function" src/',
        'find . -name "*.py"',
        'ls -la',
        'dir',
    ]
    for cmd in read_commands:
        res_cmd = policy.evaluate(cmd_tool, context, {'command': cmd})
        assert res_cmd.decision == ToolDecision.ALLOW, f"Expected {cmd} to be ALLOW, got {res_cmd.decision}"

