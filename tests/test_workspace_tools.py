from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.context import ExecutionContext
from core.sandbox_runtime import SandboxRuntime
from integrations.local_sandbox_backend import LocalSandboxBackend
from tools.workspace_tools import (
    make_delete_path_tool,
    make_edit_file_tool,
    make_find_files_tool,
    make_list_directory_tool,
    make_read_file_tool,
    make_rename_path_tool,
    make_run_command_tool,
    make_search_text_tool,
    make_write_file_tool,
)


@pytest.mark.asyncio
async def test_read_file_reads_only_inside_workspace(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("print('hello')\n", encoding="utf-8")

    tool = make_read_file_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {"path": "app.py"},
    )

    assert result["status"] == "ok"
    assert result["path"] == "app.py"
    assert result["content"] == "print('hello')\n"


@pytest.mark.asyncio
async def test_write_file_rejects_workspace_escape(tmp_path):
    tool = make_write_file_tool()

    with pytest.raises(ValueError, match="escapes workspace"):
        await tool.handler(
            ExecutionContext(
                workspace_id=str(tmp_path),
                user_id="user-1",
            ),
            {
                "path": "../outside.txt",
                "content": "nope",
            },
        )


@pytest.mark.asyncio
async def test_write_file_updates_workspace_file(tmp_path):
    tool = make_write_file_tool()

    result = await tool.handler(
        ExecutionContext(
            workspace_id=str(tmp_path),
            user_id="user-1",
        ),
        {
            "path": "pkg/value.txt",
            "content": "42\n",
        },
    )

    assert result["status"] == "ok"
    assert result["path"] == "pkg/value.txt"
    assert (tmp_path / "pkg" / "value.txt").read_text(encoding="utf-8") == "42\n"


@pytest.mark.asyncio
async def test_run_command_executes_in_workspace(tmp_path):
    sandbox = SandboxRuntime(
        LocalSandboxBackend(enabled=True)
    )
    tool = make_run_command_tool(sandbox)

    result = await tool.handler(
        ExecutionContext(
            workspace_id=str(tmp_path),
            session_id="session-1",
        ),
        {
            "argv": [
                ".venv-does-not-exist/python",
            ],
        },
    )

    assert result["status"] == "error"
    assert result["exit_code"] is None


@pytest.mark.asyncio
async def test_run_command_captures_output(tmp_path):
    sandbox = SandboxRuntime(
        LocalSandboxBackend(enabled=True)
    )
    tool = make_run_command_tool(sandbox)

    result: Mapping[str, object] = await tool.handler(
        ExecutionContext(
            workspace_id=str(tmp_path),
            session_id="session-1",
        ),
        {
            "argv": [
                "/usr/bin/printf",
                "hello",
            ],
        },
    )

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"
    assert result["stderr"] == ""


def test_build_coder_tool_registry_registers_default_workspace_tools():
    from tools.coder_tools import build_coder_tool_registry

    registry = build_coder_tool_registry()

    assert {tool.name for tool in registry.all()} == {
        "read_file",
        "write_file",
        "run_command",
        "list_directory",
        "find_files",
        "search_text",
        "edit_file",
        "rename_path",
        "delete_path",
    }



@pytest.mark.asyncio
async def test_list_directory_returns_workspace_relative_entries(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text(
        "VALUE = 42\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
    )

    tool = make_list_directory_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {"path": "."},
    )

    assert result["status"] == "ok"
    assert result["path"] == "."
    assert result["entries"] == [
        {"path": "README.md", "type": "file"},
        {"path": "pkg", "type": "directory"},
    ]


@pytest.mark.asyncio
async def test_find_files_matches_recursive_glob(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "service.py").write_text(
        "def run():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "service_test.py").write_text(
        "def test_run():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
    )

    tool = make_find_files_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {"pattern": "*.py"},
    )

    assert result["status"] == "ok"
    assert result["matches"] == [
        "pkg/service.py",
        "pkg/service_test.py",
    ]


@pytest.mark.asyncio
async def test_search_text_returns_file_line_and_text(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "service.py").write_text(
        "def run():\n"
        "    return build_result()\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "other.py").write_text(
        "BUILD_RESULT = True\n",
        encoding="utf-8",
    )

    tool = make_search_text_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {
            "query": "build_result",
            "path": "pkg",
        },
    )

    assert result["status"] == "ok"
    assert result["matches"] == [
        {
            "path": "pkg/other.py",
            "line": 1,
            "text": "BUILD_RESULT = True",
        },
        {
            "path": "pkg/service.py",
            "line": 2,
            "text": "    return build_result()",
        },
    ]


@pytest.mark.asyncio
async def test_navigation_tools_reject_workspace_escape(tmp_path):
    context = ExecutionContext(workspace_id=str(tmp_path))

    with pytest.raises(ValueError, match="escapes workspace"):
        await make_list_directory_tool().handler(
            context,
            {"path": "../outside"},
        )

    with pytest.raises(ValueError, match="escapes workspace"):
        await make_find_files_tool().handler(
            context,
            {
                "pattern": "*.py",
                "path": "../outside",
            },
        )

    with pytest.raises(ValueError, match="escapes workspace"):
        await make_search_text_tool().handler(
            context,
            {
                "query": "secret",
                "path": "../outside",
            },
        )


@pytest.mark.asyncio
async def test_read_file_supports_line_range(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        "line 1\n"
        "line 2\n"
        "line 3\n"
        "line 4\n",
        encoding="utf-8",
    )

    tool = make_read_file_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {
            "path": "app.py",
            "start_line": 2,
            "end_line": 3,
        },
    )

    assert result["status"] == "ok"
    assert result["path"] == "app.py"
    assert result["content"] == "line 2\nline 3\n"
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["total_lines"] == 4


@pytest.mark.asyncio
async def test_read_file_rejects_invalid_line_range(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("one\ntwo\n", encoding="utf-8")

    tool = make_read_file_tool()
    context = ExecutionContext(workspace_id=str(tmp_path))

    with pytest.raises(ValueError, match="start_line"):
        await tool.handler(
            context,
            {
                "path": "app.py",
                "start_line": 0,
            },
        )

    with pytest.raises(ValueError, match="end_line"):
        await tool.handler(
            context,
            {
                "path": "app.py",
                "start_line": 2,
                "end_line": 1,
            },
        )



@pytest.mark.asyncio
async def test_edit_file_replaces_unique_text(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        "def greet():\n"
        "    return \"hello\"\n",
        encoding="utf-8",
    )

    tool = make_edit_file_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {
            "path": "app.py",
            "old_text": '    return "hello"\n',
            "new_text": '    return "hello world"\n',
        },
    )

    assert result["status"] == "ok"
    assert result["path"] == "app.py"
    assert result["replacements"] == 1
    assert source.read_text(encoding="utf-8") == (
        "def greet():\n"
        '    return "hello world"\n'
    )


@pytest.mark.asyncio
async def test_edit_file_rejects_missing_text(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    tool = make_edit_file_tool()

    with pytest.raises(ValueError, match="old_text was not found"):
        await tool.handler(
            ExecutionContext(workspace_id=str(tmp_path)),
            {
                "path": "app.py",
                "old_text": "VALUE = 2",
                "new_text": "VALUE = 3",
            },
        )

    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_edit_file_rejects_ambiguous_text(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        "VALUE = 1\n"
        "VALUE = 1\n",
        encoding="utf-8",
    )

    tool = make_edit_file_tool()

    with pytest.raises(ValueError, match="old_text matched 2 times"):
        await tool.handler(
            ExecutionContext(workspace_id=str(tmp_path)),
            {
                "path": "app.py",
                "old_text": "VALUE = 1",
                "new_text": "VALUE = 2",
            },
        )

    assert source.read_text(encoding="utf-8") == (
        "VALUE = 1\n"
        "VALUE = 1\n"
    )


@pytest.mark.asyncio
async def test_edit_file_supports_multiple_atomic_edits(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        "NAME = \"demo\"\n"
        "VERSION = 1\n"
        "\n"
        "def run():\n"
        "    return NAME\n",
        encoding="utf-8",
    )

    tool = make_edit_file_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {
            "path": "app.py",
            "edits": [
                {
                    "old_text": 'NAME = "demo"',
                    "new_text": 'NAME = "my-agent"',
                },
                {
                    "old_text": "VERSION = 1",
                    "new_text": "VERSION = 2",
                },
            ],
        },
    )

    assert result["status"] == "ok"
    assert result["replacements"] == 2
    assert source.read_text(encoding="utf-8") == (
        'NAME = "my-agent"\n'
        "VERSION = 2\n"
        "\n"
        "def run():\n"
        "    return NAME\n"
    )


@pytest.mark.asyncio
async def test_edit_file_multiple_edits_are_atomic(tmp_path):
    source = tmp_path / "app.py"
    original = (
        "VALUE = 1\n"
        "ENABLED = False\n"
    )
    source.write_text(original, encoding="utf-8")

    tool = make_edit_file_tool()

    with pytest.raises(ValueError, match="old_text was not found"):
        await tool.handler(
            ExecutionContext(workspace_id=str(tmp_path)),
            {
                "path": "app.py",
                "edits": [
                    {
                        "old_text": "VALUE = 1",
                        "new_text": "VALUE = 2",
                    },
                    {
                        "old_text": "MISSING = True",
                        "new_text": "ENABLED = True",
                    },
                ],
            },
        )

    assert source.read_text(encoding="utf-8") == original



@pytest.mark.asyncio
async def test_rename_path_moves_file_inside_workspace(tmp_path):
    source = tmp_path / "old.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    tool = make_rename_path_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {
            "source": "old.py",
            "destination": "pkg/new.py",
        },
    )

    assert result["status"] == "ok"
    assert result["source"] == "old.py"
    assert result["destination"] == "pkg/new.py"
    assert not source.exists()
    assert (tmp_path / "pkg" / "new.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_rename_path_rejects_existing_destination(tmp_path):
    (tmp_path / "old.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "new.py").write_text("new\n", encoding="utf-8")

    tool = make_rename_path_tool()

    with pytest.raises(FileExistsError):
        await tool.handler(
            ExecutionContext(workspace_id=str(tmp_path)),
            {
                "source": "old.py",
                "destination": "new.py",
            },
        )

    assert (tmp_path / "old.py").read_text(encoding="utf-8") == "old\n"
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_delete_path_deletes_file(tmp_path):
    source = tmp_path / "obsolete.py"
    source.write_text("unused\n", encoding="utf-8")

    tool = make_delete_path_tool()
    result = await tool.handler(
        ExecutionContext(workspace_id=str(tmp_path)),
        {"path": "obsolete.py"},
    )

    assert result["status"] == "ok"
    assert result["path"] == "obsolete.py"
    assert not source.exists()


@pytest.mark.asyncio
async def test_delete_path_requires_recursive_for_nonempty_directory(tmp_path):
    directory = tmp_path / "pkg"
    directory.mkdir()
    (directory / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    tool = make_delete_path_tool()
    context = ExecutionContext(workspace_id=str(tmp_path))

    with pytest.raises(ValueError, match="recursive"):
        await tool.handler(
            context,
            {"path": "pkg"},
        )

    result = await tool.handler(
        context,
        {
            "path": "pkg",
            "recursive": True,
        },
    )

    assert result["status"] == "ok"
    assert not directory.exists()


@pytest.mark.asyncio
async def test_rename_and_delete_reject_workspace_escape(tmp_path):
    context = ExecutionContext(workspace_id=str(tmp_path))

    with pytest.raises(ValueError, match="escapes workspace"):
        await make_rename_path_tool().handler(
            context,
            {
                "source": "../outside.py",
                "destination": "inside.py",
            },
        )

    with pytest.raises(ValueError, match="escapes workspace"):
        await make_delete_path_tool().handler(
            context,
            {"path": "../outside.py"},
        )


@pytest.mark.asyncio
async def test_run_command_requires_explicit_sandbox_runtime(tmp_path):
    from core.errors import CapabilityUnavailableError

    tool = make_run_command_tool()

    with pytest.raises(
        CapabilityUnavailableError,
        match="sandbox runtime is required",
    ):
        await tool.handler(
            ExecutionContext(
                workspace_id=str(tmp_path),
                session_id="session-1",
            ),
            {
                "argv": ["/usr/bin/printf", "hello"],
            },
        )
