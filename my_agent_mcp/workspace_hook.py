from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.code_graph_sync import sync_code_graph
from core.project import ProjectRecord
from persistence.sqlite_checkpoint_store import SQLiteCheckpointStore
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore
from persistence.sqlite_project_store import SQLiteProjectStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "my_agent.db"

projects = SQLiteProjectStore(DB_PATH)
checkpoints = SQLiteCheckpointStore(DB_PATH)
code_graph = SQLiteCodeGraphStore(DB_PATH)


async def main() -> None:
    payload = json.load(sys.stdin)
    workspace_paths = payload.get("workspacePaths") or []

    if not workspace_paths:
        print(json.dumps({"injectSteps": []}))
        return

    workspace = Path(workspace_paths[0]).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        print(json.dumps({"injectSteps": []}))
        return

    await projects.initialize()
    await checkpoints.initialize()
    await code_graph.initialize()

    records = await projects.list()
    workspace_key = str(workspace).casefold()
    project = None

    for item in records:
        if str(Path(item.workspace_path).expanduser().resolve()).casefold() == workspace_key:
            project = item
            break

    registered = False
    if project is None:
        base_id = workspace.name.strip() or "project"
        project_id = base_id
        existing_ids = {item.project_id for item in records}

        if project_id in existing_ids:
            suffix = hashlib.sha1(workspace_key.encode("utf-8")).hexdigest()[:8]
            project_id = f"{base_id}-{suffix}"

        project = ProjectRecord(
            project_id=project_id,
            name=workspace.name or project_id,
            workspace_path=str(workspace),
        )
        await projects.create(project)
        registered = True

    await projects.set_active(project.project_id)

    graph_note = None
    graph_status = await code_graph.get_graph_status(project.project_id)
    if graph_status["status"] == "not_indexed":
        graph_note = "Code Graph is not indexed; do not full-scan unless the user asks."
    else:
        try:
            changes = await sync_code_graph(project.project_id, workspace, code_graph)
            changed_count = len(changes.added) + len(changes.modified) + len(changes.deleted)
            graph_note = (
                f"Code Graph synchronized with the saved filesystem; {changed_count} changed file(s) detected."
                if changed_count
                else "Code Graph is synchronized with the saved filesystem."
            )
        except Exception as exc:  # noqa: BLE001
            graph_note = f"Code Graph auto-sync failed without modifying source files: {exc}"

    inject_steps = []
    if payload.get("invocationNum") == 0:
        checkpoint = await checkpoints.latest(project.project_id)
        message = (
            "MANDATORY IDENTITY: YOU ARE ALWAYS MYAGENT (Front Agent). "
            "You must ALWAYS identify as MyAgent from the very first turn of every conversation without waiting for the user to ask or mention MyAgent. "
            "STRICT NO-EMOJI RULE: NEVER use any emojis or graphical icons in your responses (such as octopus, robot, lightbulb, checkmarks, etc.). Always use pure, clean, professional technical markdown text only. "
            f"MyAgent active project: {project.project_id}. "
            f"Workspace: {project.workspace_path}. "
            "MyAgent owns coding-task continuity across Antigravity, Codex, and Claude. "
            "When continuing existing work, use resume_project and MyAgent durable "
            "checkpoint/history instead of restarting analysis. "
            "Before starting a new coder session, call get_latest_resumable_coder_session. "
            "If a resumable session exists, use handoff_coder_session and then consume "
            "next_coder_command rather than restarting the task. "
            "When the existing coder session is still attached and only the model/runtime "
            "changes, use set_coder_execution_target and preserve the same session/task context. "
            "Opening/switching projects does not authorize installing dependencies, "
            "changing environment configuration, deleting files, pushing Git changes, "
            "or modifying source code without a user task."
        )
        if checkpoint is not None:
            message += f" Latest checkpoint: {checkpoint.summary}"
            if checkpoint.next_action:
                message += f" Next action: {checkpoint.next_action}"
        if registered:
            message += " This existing workspace was registered in MyAgent state only; no source files were created or modified."
        if graph_note:
            message += f" {graph_note}"

        inject_steps.append({"ephemeralMessage": message})

    print(json.dumps({"injectSteps": inject_steps}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:  # noqa: BLE001
        print(json.dumps({"injectSteps": []}, ensure_ascii=False))
