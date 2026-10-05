from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.conversation import ConversationRecord
from core.message import MessageRecord, MessageRole
from persistence.sqlite_conversation_store import SQLiteConversationStore
from persistence.sqlite_project_store import SQLiteProjectStore

DB_PATH = ROOT / "data" / "my_agent.db"
logger = logging.getLogger(__name__)


def _parse_time(value: object) -> datetime:
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            pass
    return datetime.now(UTC)


def _clean_content(row: dict[str, object]) -> str:
    content = str(row.get("content", "")).strip()
    if row.get("source") == "USER_EXPLICIT" and row.get("type") == "USER_INPUT":
        match = re.search(
            r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if match is not None:
            return match.group(1).strip()
    return content


def _message_role(row: dict[str, object]) -> MessageRole | None:
    source = row.get("source")
    kind = row.get("type")
    content = row.get("content")

    if not isinstance(content, str) or not content.strip():
        return None

    if source == "USER_EXPLICIT" and kind == "USER_INPUT":
        return MessageRole.USER

    if source == "MODEL" and kind == "PLANNER_RESPONSE":
        return MessageRole.ASSISTANT

    return None


def _message_id(
    project_id: str,
    conversation_id: str,
    row: dict[str, object],
) -> str:
    identity = "\0".join(
        (
            "antigravity",
            project_id,
            conversation_id,
            str(row.get("step_index", "")),
            str(row.get("source", "")),
            str(row.get("type", "")),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"ag-{digest}"


async def _find_project_id(
    projects: SQLiteProjectStore,
    workspace_paths: list[object],
) -> str | None:
    normalized: list[str] = []
    for value in workspace_paths:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            normalized.append(str(Path(value).expanduser().resolve()).casefold())
        except OSError:
            continue

    if not normalized:
        return None

    for project in await projects.list():
        try:
            key = str(Path(project.workspace_path).expanduser().resolve()).casefold()
        except OSError:
            continue
        if key in normalized:
            return project.project_id

    return None


async def sync(payload: dict[str, object]) -> int:
    conversation_id = payload.get("conversationId")
    transcript_path = payload.get("transcriptPath")
    workspace_paths = payload.get("workspacePaths") or []

    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return 0
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return 0
    if not isinstance(workspace_paths, list):
        return 0

    transcript = Path(transcript_path).expanduser()
    if not transcript.is_file():
        return 0

    projects = SQLiteProjectStore(DB_PATH)
    conversations = SQLiteConversationStore(DB_PATH)
    await projects.initialize()
    await conversations.initialize()

    project_id = await _find_project_id(projects, workspace_paths)
    if project_id is None:
        return 0

    rows: list[dict[str, object]] = []
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and _message_role(row) is not None:
            rows.append(row)

    rows.sort(
        key=lambda row: (
            int(row.get("step_index", 0))
            if isinstance(row.get("step_index"), int)
            else 0
        )
    )

    conversation = await conversations.get(project_id, conversation_id)
    if conversation is None:
        first_time = _parse_time(rows[0].get("created_at")) if rows else datetime.now(UTC)
        conversation = ConversationRecord(
            conversation_id=conversation_id,
            project_id=project_id,
            created_at=first_time,
            updated_at=first_time,
        )
        await conversations.create(conversation)

    existing = await conversations.history(project_id, conversation_id)
    existing_ids = {message.message_id for message in existing}

    added = 0
    for row in rows:
        role = _message_role(row)
        if role is None:
            continue

        message_id = _message_id(project_id, conversation_id, row)
        if message_id in existing_ids:
            continue

        content = _clean_content(row)
        message = MessageRecord(
            message_id=message_id,
            project_id=project_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata={
                "runtime": "antigravity",
                "step_index": row.get("step_index"),
                "source": row.get("source"),
                "type": row.get("type"),
                "status": row.get("status"),
            },
            created_at=_parse_time(row.get("created_at")),
        )
        await conversations.add_message(message)
        existing_ids.add(message_id)
        added += 1

    return added


async def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
        await sync(payload)
    except Exception:
        # History synchronization must never block Antigravity from stopping.
        logger.debug("transcript synchronization failed during shutdown", exc_info=True)

    print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    asyncio.run(main())
