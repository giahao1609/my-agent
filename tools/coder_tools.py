from __future__ import annotations

from core.protocols import MemoryBackend
from core.sandbox_runtime import SandboxRuntime
from core.tool_registry import ToolRegistry

from .memory_tools import make_capture_memory_tool
from .workspace_tools import (
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


def build_coder_tool_registry(
    memory: MemoryBackend | None = None,
    sandbox_runtime: SandboxRuntime | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(
        (
            make_read_file_tool(),
            make_write_file_tool(),
            make_run_command_tool(sandbox_runtime),
            make_list_directory_tool(),
            make_find_files_tool(),
            make_search_text_tool(),
            make_edit_file_tool(),
            make_rename_path_tool(),
            make_delete_path_tool(),
        )
    )
    if memory is not None:
        registry.register(make_capture_memory_tool(memory))
    return registry
