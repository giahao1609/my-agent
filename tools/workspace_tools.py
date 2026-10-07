from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.context import ExecutionContext
from core.errors import CapabilityUnavailableError
from core.sandbox_runtime import SandboxRuntime
from core.tools import ToolDefinition, ToolPermission


def _workspace_root(context: ExecutionContext) -> Path:
    root = Path(context.workspace_id).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace does not exist: {root}")
    return root


def _workspace_path(
    context: ExecutionContext,
    value: object,
    *,
    must_exist: bool = False,
) -> tuple[Path, Path]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty string")

    root = _workspace_root(context)
    candidate = Path(value).expanduser()
    path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (root / candidate).resolve()
    )

    if not path.is_relative_to(root):
        raise ValueError("path escapes workspace")
    if must_exist and not path.exists():
        raise FileNotFoundError(path)

    return root, path


async def _read_file(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    root, path = _workspace_path(
        context,
        arguments.get("path"),
        must_exist=True,
    )
    if not path.is_file():
        raise ValueError("path is not a file")

    start_value = arguments.get("start_line", 1)
    end_value = arguments.get("end_line")

    if not isinstance(start_value, int) or isinstance(start_value, bool):
        raise TypeError("start_line must be an integer")
    if start_value < 1:
        raise ValueError("start_line must be >= 1")

    if end_value is not None:
        if not isinstance(end_value, int) or isinstance(end_value, bool):
            raise TypeError("end_line must be an integer")
        if end_value < start_value:
            raise ValueError("end_line must be >= start_line")

    max_chars = arguments.get("max_chars")
    if max_chars is not None:
        if type(max_chars) is not int or max_chars < 1 or end_value is None:
            raise ValueError("bounded read requires positive max_chars and end_line")

        def read_bounded() -> Mapping[str, object]:
            # Stop at the requested range; never materialize the whole file.
            # Whole lines only: a character cutoff could expose a partial secret.
            selected_lines: list[str] = []
            selected_chars = 0
            actual_end = start_value - 1
            with path.open(encoding="utf-8") as stream:
                for _ in range(1, start_value):
                    line = stream.readline()
                    if not line:
                        return {
                            "status": "ok",
                            "path": path.relative_to(root).as_posix(),
                            "content": "",
                            "start_line": start_value,
                            "end_line": actual_end,
                            "total_lines": None,
                        }
                for number in range(start_value, end_value + 1):
                    remaining = max_chars - selected_chars
                    line = stream.readline(remaining + 1)
                    if not line:
                        break
                    selected_chars += len(line)
                    if selected_chars > max_chars:
                        raise ValueError("bounded source read exceeded scan budget")
                    selected_lines.append(line)
                    actual_end = number
            return {
                "status": "ok",
                "path": path.relative_to(root).as_posix(),
                "content": "".join(selected_lines),
                "start_line": start_value,
                "end_line": actual_end,
                "total_lines": None,  # A bounded read does not count the file.
            }

        return await asyncio.to_thread(read_bounded)

    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    if total_lines == 0:
        selected = ""
        actual_end = 0
    else:
        actual_end = total_lines if end_value is None else min(
            end_value,
            total_lines,
        )
        selected = "".join(lines[start_value - 1 : actual_end])

    return {
        "status": "ok",
        "path": path.relative_to(root).as_posix(),
        "content": selected,
        "start_line": start_value,
        "end_line": actual_end,
        "total_lines": total_lines,
    }


async def _write_file(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    root, path = _workspace_path(context, arguments.get("path"))

    content = arguments.get("content")
    if not isinstance(content, str):
        raise TypeError("content must be a string")

    await asyncio.to_thread(
        path.parent.mkdir,
        parents=True,
        exist_ok=True,
    )
    await asyncio.to_thread(
        path.write_text,
        content,
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "path": path.relative_to(root).as_posix(),
        "bytes_written": len(content.encode("utf-8")),
    }


def _command_argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("argv must be a sequence of strings")

    argv = tuple(value)
    if not argv:
        raise ValueError("argv must not be empty")
    if not all(isinstance(item, str) and item for item in argv):
        raise TypeError("argv must contain non-empty strings")

    return argv



async def _list_directory(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    root, directory = _workspace_path(
        context,
        arguments.get("path", "."),
        must_exist=True,
    )
    if not directory.is_dir():
        raise ValueError("path is not a directory")

    entries: list[dict[str, str]] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            entry_type = "symlink"
        elif entry.is_dir():
            entry_type = "directory"
        elif entry.is_file():
            entry_type = "file"
        else:
            entry_type = "other"

        entries.append(
            {
                "path": entry.relative_to(root).as_posix(),
                "type": entry_type,
            }
        )

    relative = directory.relative_to(root).as_posix()
    return {
        "status": "ok",
        "path": relative if relative != "" else ".",
        "entries": entries,
    }


def _search_root(
    context: ExecutionContext,
    value: object,
) -> tuple[Path, Path]:
    root, search_root = _workspace_path(
        context,
        "." if value is None else value,
        must_exist=True,
    )
    if not search_root.is_dir():
        raise ValueError("path is not a directory")
    return root, search_root


def _safe_workspace_file(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(root) and resolved.is_file()


async def _find_files(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("pattern must be a non-empty string")

    root, search_root = _search_root(
        context,
        arguments.get("path"),
    )

    def find() -> list[str]:
        matches = [
            candidate.relative_to(root).as_posix()
            for candidate in search_root.rglob(pattern)
            if _safe_workspace_file(root, candidate)
        ]
        return sorted(matches)

    matches = await asyncio.to_thread(find)
    return {
        "status": "ok",
        "matches": matches,
    }


async def _search_text(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")

    root, search_root = _search_root(
        context,
        arguments.get("path"),
    )
    needle = query.casefold()

    def search() -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []

        candidates = sorted(
            (
                candidate
                for candidate in search_root.rglob("*")
                if _safe_workspace_file(root, candidate)
            ),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        )

        for candidate in candidates:
            try:
                content = candidate.read_text(
                    encoding="utf-8",
                    errors="strict",
                )
            except (UnicodeDecodeError, OSError):
                continue

            relative = candidate.relative_to(root).as_posix()
            for line_number, line in enumerate(content.splitlines(), start=1):
                if needle in line.casefold():
                    matches.append(
                        {
                            "path": relative,
                            "line": line_number,
                            "text": line,
                        }
                    )

        return matches

    matches = await asyncio.to_thread(search)
    return {
        "status": "ok",
        "matches": matches,
    }


def make_list_directory_tool() -> ToolDefinition:
    return ToolDefinition(
        name="list_directory",
        description="List direct entries inside a workspace directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
        },
        handler=_list_directory,
        permissions=frozenset({ToolPermission.READ}),
    )


def make_find_files_tool() -> ToolDefinition:
    return ToolDefinition(
        name="find_files",
        description="Recursively find workspace files matching a glob pattern.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["pattern"],
        },
        handler=_find_files,
        permissions=frozenset({ToolPermission.READ}),
    )


def make_search_text_tool() -> ToolDefinition:
    return ToolDefinition(
        name="search_text",
        description="Recursively search UTF-8 workspace files for text.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["query"],
        },
        handler=_search_text,
        permissions=frozenset({ToolPermission.READ}),
    )


async def _edit_file(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    root, path = _workspace_path(
        context,
        arguments.get("path"),
        must_exist=True,
    )
    if not path.is_file():
        raise ValueError("path is not a file")

    edits_value = arguments.get("edits")

    if edits_value is None:
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")

        if not isinstance(old_text, str) or not old_text:
            raise ValueError("old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise TypeError("new_text must be a string")

        edits = (
            {
                "old_text": old_text,
                "new_text": new_text,
            },
        )
    else:
        if (
            not isinstance(edits_value, Sequence)
            or isinstance(edits_value, (str, bytes))
            or not edits_value
        ):
            raise ValueError("edits must be a non-empty sequence")

        normalized: list[dict[str, str]] = []

        for edit in edits_value:
            if not isinstance(edit, Mapping):
                raise TypeError("each edit must be an object")

            old_text = edit.get("old_text")
            new_text = edit.get("new_text")

            if not isinstance(old_text, str) or not old_text:
                raise ValueError("old_text must be a non-empty string")
            if not isinstance(new_text, str):
                raise TypeError("new_text must be a string")

            normalized.append(
                {
                    "old_text": old_text,
                    "new_text": new_text,
                }
            )

        edits = tuple(normalized)

    content = await asyncio.to_thread(
        path.read_text,
        encoding="utf-8",
    )

    updated = content

    for edit in edits:
        old_text = edit["old_text"]
        new_text = edit["new_text"]

        matches = updated.count(old_text)

        if matches == 0:
            raise ValueError("old_text was not found")
        if matches != 1:
            raise ValueError(f"old_text matched {matches} times")

        updated = updated.replace(old_text, new_text, 1)

    await asyncio.to_thread(
        path.write_text,
        updated,
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "path": path.relative_to(root).as_posix(),
        "replacements": len(edits),
    }


def make_edit_file_tool() -> ToolDefinition:
    return ToolDefinition(
        name="edit_file",
        description=(
            "Replace one uniquely matching text block inside a workspace file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                        },
                        "required": [
                            "old_text",
                            "new_text",
                        ],
                    },
                },
            },
            "required": ["path"],
        },
        handler=_edit_file,
        permissions=frozenset({ToolPermission.WRITE}),
    )


async def _rename_path(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    root, source = _workspace_path(
        context,
        arguments.get("source"),
        must_exist=True,
    )
    _, destination = _workspace_path(
        context,
        arguments.get("destination"),
    )

    if source == root:
        raise ValueError("cannot rename workspace root")
    if destination.exists():
        raise FileExistsError(destination)

    await asyncio.to_thread(
        destination.parent.mkdir,
        parents=True,
        exist_ok=True,
    )
    await asyncio.to_thread(source.rename, destination)

    return {
        "status": "ok",
        "source": source.relative_to(root).as_posix(),
        "destination": destination.relative_to(root).as_posix(),
    }


async def _delete_path(
    context: ExecutionContext,
    arguments: Mapping[str, object],
) -> Mapping[str, object]:
    root, path = _workspace_path(
        context,
        arguments.get("path"),
        must_exist=True,
    )

    if path == root:
        raise ValueError("cannot delete workspace root")

    recursive = arguments.get("recursive", False)
    if not isinstance(recursive, bool):
        raise TypeError("recursive must be a boolean")

    relative = path.relative_to(root).as_posix()

    if path.is_dir():
        if recursive:
            await asyncio.to_thread(shutil.rmtree, path)
        else:
            try:
                await asyncio.to_thread(path.rmdir)
            except OSError as exc:
                if any(path.iterdir()):
                    raise ValueError(
                        "directory is not empty; recursive=True is required"
                    ) from exc
                raise
    else:
        await asyncio.to_thread(path.unlink)

    return {
        "status": "ok",
        "path": relative,
    }


def make_rename_path_tool() -> ToolDefinition:
    return ToolDefinition(
        name="rename_path",
        description=(
            "Rename or move a file or directory inside the current workspace."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": [
                "source",
                "destination",
            ],
        },
        handler=_rename_path,
        permissions=frozenset({ToolPermission.WRITE}),
    )


def make_delete_path_tool() -> ToolDefinition:
    return ToolDefinition(
        name="delete_path",
        description=(
            "Delete a file or directory inside the current workspace."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
            "required": ["path"],
        },
        handler=_delete_path,
        permissions=frozenset({ToolPermission.WRITE}),
    )

def make_read_file_tool() -> ToolDefinition:
    return ToolDefinition(
        name="read_file",
        description="Read a UTF-8 text file inside the current workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                },
                "end_line": {
                    "type": "integer",
                    "minimum": 1,
                },
                "max_chars": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
        handler=_read_file,
        permissions=frozenset({ToolPermission.READ}),
    )


def make_write_file_tool() -> ToolDefinition:
    return ToolDefinition(
        name="write_file",
        description="Write a UTF-8 text file inside the current workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=_write_file,
        permissions=frozenset({ToolPermission.WRITE}),
    )


def make_run_command_tool(
    sandbox_runtime: SandboxRuntime | None = None,
) -> ToolDefinition:
    async def run_command(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        if sandbox_runtime is None:
            raise CapabilityUnavailableError(
                "sandbox runtime is required for command execution"
            )

        argv = _command_argv(arguments.get("argv"))

        cwd_value = arguments.get("cwd")
        if cwd_value is not None and (
            not isinstance(cwd_value, str) or not cwd_value.strip()
        ):
            raise ValueError("cwd must be a non-empty string")

        timeout_value = arguments.get("timeout", 120)
        if not isinstance(timeout_value, int | float):
            raise TypeError("timeout must be a number")

        timeout = float(timeout_value)
        if timeout <= 0:
            raise ValueError("timeout must be > 0")

        return await sandbox_runtime.exec(
            context,
            argv,
            cwd=cwd_value,
            timeout=timeout,
        )

    return ToolDefinition(
        name="run_command",
        description="Run an argv command inside the active session sandbox.",
        input_schema={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "cwd": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["argv"],
        },
        handler=run_command,
        permissions=frozenset({ToolPermission.EXECUTE}),
    )
