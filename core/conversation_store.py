from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .conversation import ConversationRecord
from .message import MessageRecord


class ConversationStore(Protocol):
    async def create(self, conversation: ConversationRecord) -> None: ...

    async def get(
        self,
        project_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None: ...

    async def list_for_project(
        self,
        project_id: str,
    ) -> Sequence[ConversationRecord]: ...

    async def add_message(self, message: MessageRecord) -> None: ...

    async def history(
        self,
        project_id: str,
        conversation_id: str,
        *,
        limit: int | None = None,
    ) -> Sequence[MessageRecord]: ...

    async def clear_history(
        self,
        project_id: str,
        conversation_id: str | None = None,
    ) -> int: ...

    async def delete_conversation(
        self,
        project_id: str,
        conversation_id: str,
    ) -> bool: ...
