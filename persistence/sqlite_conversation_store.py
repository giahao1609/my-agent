from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from core.conversation import ConversationRecord
from core.message import MessageRecord, MessageRole


class SQLiteConversationStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "conversation_id TEXT NOT NULL, "
                "project_id TEXT NOT NULL, "
                "title TEXT, "
                "summary TEXT, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, "
                "PRIMARY KEY (project_id, conversation_id)"
                ")"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "message_id TEXT PRIMARY KEY, "
                "project_id TEXT NOT NULL, "
                "conversation_id TEXT NOT NULL, "
                "role TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "metadata_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL"
                ")"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_project "
                "ON conversations(project_id, updated_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_history "
                "ON messages(project_id, conversation_id, created_at)"
            )

    async def create(self, conversation: ConversationRecord) -> None:
        await asyncio.to_thread(self._create_sync, conversation)

    def _create_sync(self, conversation: ConversationRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations (
                    conversation_id,
                    project_id,
                    title,
                    summary,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation.conversation_id,
                    conversation.project_id,
                    conversation.title,
                    conversation.summary,
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                ),
            )

    async def get(
        self,
        project_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        return await asyncio.to_thread(
            self._get_sync,
            project_id,
            conversation_id,
        )

    def _get_sync(
        self,
        project_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE project_id = ? AND conversation_id = ?
                """,
                (project_id, conversation_id),
            ).fetchone()
        return self._row_to_conversation(row) if row is not None else None

    async def list_for_project(
        self,
        project_id: str,
    ) -> tuple[ConversationRecord, ...]:
        return await asyncio.to_thread(self._list_for_project_sync, project_id)

    def _list_for_project_sync(
        self,
        project_id: str,
    ) -> tuple[ConversationRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE project_id = ?
                ORDER BY updated_at DESC
                """,
                (project_id,),
            ).fetchall()
        return tuple(self._row_to_conversation(row) for row in rows)

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> ConversationRecord:
        from datetime import datetime

        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            project_id=str(row["project_id"]),
            title=row["title"],
            summary=row["summary"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    async def add_message(self, message: MessageRecord) -> None:
        await asyncio.to_thread(self._add_message_sync, message)

    def _add_message_sync(self, message: MessageRecord) -> None:
        conversation = self._get_sync(message.project_id, message.conversation_id)
        if conversation is None:
            raise KeyError(
                f"unknown conversation: {message.project_id}/{message.conversation_id}"
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    message_id,
                    project_id,
                    conversation_id,
                    role,
                    content,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.project_id,
                    message.conversation_id,
                    message.role.value,
                    message.content,
                    json.dumps(dict(message.metadata), ensure_ascii=False),
                    message.created_at.isoformat(),
                ),
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE project_id = ? AND conversation_id = ?
                """,
                (
                    message.created_at.isoformat(),
                    message.project_id,
                    message.conversation_id,
                ),
            )

    async def history(
        self,
        project_id: str,
        conversation_id: str,
        *,
        limit: int | None = None,
    ) -> tuple[MessageRecord, ...]:
        return await asyncio.to_thread(
            self._history_sync,
            project_id,
            conversation_id,
            limit,
        )

    def _history_sync(
        self,
        project_id: str,
        conversation_id: str,
        limit: int | None,
    ) -> tuple[MessageRecord, ...]:
        if self._get_sync(project_id, conversation_id) is None:
            raise KeyError(f"unknown conversation: {project_id}/{conversation_id}")

        with self._connect() as connection:
            if limit is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM messages
                    WHERE project_id = ? AND conversation_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (project_id, conversation_id),
                ).fetchall()
            else:
                if limit < 0:
                    raise ValueError("history limit must be >= 0")
                rows = connection.execute(
                    """
                    SELECT *
                    FROM messages
                    WHERE project_id = ? AND conversation_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (project_id, conversation_id, limit),
                ).fetchall()
                rows = list(reversed(rows))

        return tuple(self._row_to_message(row) for row in rows)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> MessageRecord:
        from datetime import datetime

        return MessageRecord(
            message_id=str(row["message_id"]),
            project_id=str(row["project_id"]),
            conversation_id=str(row["conversation_id"]),
            role=MessageRole(str(row["role"])),
            content=str(row["content"]),
            metadata=json.loads(str(row["metadata_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    async def clear_history(
        self,
        project_id: str,
        conversation_id: str | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._clear_history_sync,
            project_id,
            conversation_id,
        )

    def _clear_history_sync(
        self,
        project_id: str,
        conversation_id: str | None,
    ) -> int:
        with self._connect() as connection:
            if conversation_id is None:
                cursor = connection.execute(
                    "DELETE FROM messages WHERE project_id = ?",
                    (project_id,),
                )
            else:
                if self._get_sync(project_id, conversation_id) is None:
                    raise KeyError(
                        f"unknown conversation: {project_id}/{conversation_id}"
                    )
                cursor = connection.execute(
                    """
                    DELETE FROM messages
                    WHERE project_id = ? AND conversation_id = ?
                    """,
                    (project_id, conversation_id),
                )
        return cursor.rowcount

    async def delete_conversation(
        self,
        project_id: str,
        conversation_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._delete_conversation_sync,
            project_id,
            conversation_id,
        )

    def _delete_conversation_sync(
        self,
        project_id: str,
        conversation_id: str,
    ) -> bool:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM messages
                WHERE project_id = ? AND conversation_id = ?
                """,
                (project_id, conversation_id),
            )
            cursor = connection.execute(
                """
                DELETE FROM conversations
                WHERE project_id = ? AND conversation_id = ?
                """,
                (project_id, conversation_id),
            )
        return cursor.rowcount > 0
