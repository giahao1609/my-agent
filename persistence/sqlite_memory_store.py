from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    are_canonically_equivalent,
    normalize_memory_status,
    normalize_memory_tier,
    normalize_memory_type,
)


@dataclass(frozen=True, slots=True)
class _ScoredMatch:
    """Internal scored result; converted to MemoryMatch by DurableMemoryService."""

    record: MemoryRecord
    score: float
    reasons: tuple[str, ...]


class SQLiteMemoryStore:
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
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Phase 04: additive columns — safe for pre-existing databases.
            for col_def in (
                "status TEXT NOT NULL DEFAULT 'active'",
                "logical_key TEXT",
            ):
                col_name = col_def.split()[0]
                try:
                    connection.execute(
                        f"ALTER TABLE memories ADD COLUMN {col_def}"
                    )
                except sqlite3.OperationalError as exc:
                    # Only ignore duplicate-column errors; propagate everything else.
                    msg = str(exc).lower()
                    if "duplicate column name" not in msg and "already exists" not in msg:
                        raise
                _ = col_name  # suppress unused-variable warning
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_project
                ON memories(project_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_lookup
                ON memories(project_id, kind, importance)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_status
                ON memories(project_id, status, importance)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_logical_key
                ON memories(project_id, logical_key)
                """
            )

    async def add_many(
        self,
        records: tuple[MemoryRecord, ...],
    ) -> None:
        await asyncio.to_thread(self._add_many_sync, records)

    @staticmethod
    def _serialize_metadata(record: MemoryRecord) -> dict[str, object]:
        if "__canonical__" in record.metadata:
            raise ValueError(
                "metadata key '__canonical__' is reserved for internal store persistence"
            )
        meta = dict(record.metadata)
        meta["__canonical__"] = {
            "memory_type": record.memory_type.value,
            "tier": record.tier.value,
            "confidence": record.confidence,
            "subject": record.subject,
            "scope": record.scope,
            "source": record.source,
            "status": record.status.value,
            "supersedes": record.supersedes,
            "project_id": record.project_id,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
            "last_accessed_at": record.last_accessed_at.isoformat() if record.last_accessed_at else None,
            "valid_from": record.valid_from.isoformat() if record.valid_from else None,
            "valid_until": record.valid_until.isoformat() if record.valid_until else None,
            "custom_kind": record._custom_kind,
        }
        return meta

    def _add_many_sync(
        self,
        records: tuple[MemoryRecord, ...],
    ) -> None:
        if not records:
            return

        with self._connect() as connection:
            for record in records:
                if record.project_id == "__global__":
                    raise ValueError(
                        "RESERVED_SENTINEL: project_id='__global__' is a reserved legacy sentinel "
                        "and cannot be used for new durable writes. Use project_id=None with "
                        "scope='global' for canonical global memories."
                    )
                try:
                    connection.execute(
                        """
                        INSERT INTO memories (
                            memory_id,
                            project_id,
                            level,
                            kind,
                            content,
                            importance,
                            metadata_json,
                            created_at,
                            status,
                            logical_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.memory_id,
                            record.project_id if record.project_id is not None else "",
                            record.level.value,
                            record.kind,
                            record.content,
                            record.importance,
                            json.dumps(
                                self._serialize_metadata(record),
                                ensure_ascii=False,
                            ),
                            record.created_at.isoformat(),
                            record.status.value,
                            record.metadata.get("logical_key"),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    err_msg = str(exc).lower()
                    if "unique constraint" in err_msg or "primary key" in err_msg:
                        # Re-read existing row and check canonical semantic equivalence
                        row = connection.execute(
                            "SELECT * FROM memories WHERE memory_id = ?",
                            (record.memory_id,),
                        ).fetchone()
                        if row is not None:
                            existing = self._row_to_memory(row)
                            if are_canonically_equivalent(existing, record):
                                # Identical canonical semantics: idempotent write accepted
                                continue
                            raise ValueError(
                                f"Conflict: memory record '{record.memory_id}' already exists with "
                                f"different canonical semantics. Re-write rejected."
                            ) from exc
                    # Unexpected IntegrityError must propagate
                    raise

    async def search(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        return await asyncio.to_thread(
            self._search_sync,
            project_id,
            query,
            limit,
        )

    def _search_sync(
        self,
        project_id: str,
        query: str,
        limit: int,
    ) -> tuple[MemoryRecord, ...]:
        if limit < 0:
            raise ValueError("memory search limit must be >= 0")
        if limit == 0:
            return ()

        pattern = f"%{query.strip()}%"

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM memories
                WHERE project_id = ?
                  AND (
                    content LIKE ?
                    OR kind LIKE ?
                  )
                ORDER BY importance DESC, created_at DESC, rowid DESC
                LIMIT ?
                """,
                (
                    project_id,
                    pattern,
                    pattern,
                    limit,
                ),
            ).fetchall()

        return tuple(self._row_to_memory(row) for row in rows)

    # ------------------------------------------------------------------
    # Phase 04: Durable Memory Pipeline — new retrieval & lifecycle API
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        project_id: str | None,
        query: str,
        *,
        limit: int = 10,
        status_filter: MemoryStatus | None = MemoryStatus.ACTIVE,
        tier_filter: MemoryLevel | None = None,
        memory_type_filter: MemoryType | None = None,
        min_importance: float = 0.0,
    ) -> tuple[MemoryRecord, ...]:
        """Filtered + scored retrieval (legacy API — returns bare MemoryRecord tuple).

        Prefer retrieve_scored() for new consumers that need score/reasons.
        """
        matches = await self.retrieve_scored(
            project_id,
            query,
            limit=limit,
            status_filter=status_filter,
            tier_filter=tier_filter,
            memory_type_filter=memory_type_filter,
            min_importance=min_importance,
        )
        return tuple(m.record for m in matches)

    async def retrieve_scored(
        self,
        project_id: str | None,
        query: str,
        *,
        limit: int = 10,
        status_filter: MemoryStatus | None = MemoryStatus.ACTIVE,
        tier_filter: MemoryLevel | None = None,
        memory_type_filter: MemoryType | None = None,
        min_importance: float = 0.0,
    ) -> tuple[_ScoredMatch, ...]:
        """Filtered + scored retrieval returning _ScoredMatch objects.

        Scoring formula per record:
            score = phrase_bonus(0.5) + term_bonus(0.1/term) +
                    importance * 0.4 + confidence * 0.1

        Candidate selection strategy:
            When the query is non-empty, a SQL LIKE pre-filter is applied
            FIRST to select all records matching exact phrase OR any query token
            (no limit), then merged with the importance-ordered fallback set.
            This guarantees that a low-importance record with strong token
            overlap is never discarded behind 200 unrelated high-importance rows.

        Zero-match policy:
            When query is non-empty, records with zero explicit lexical relevance
            (has_lexical_match is False) are excluded via fail-closed evidence check.
        """
        return await asyncio.to_thread(
            self._retrieve_scored_sync,
            project_id,
            query,
            limit,
            status_filter,
            tier_filter,
            memory_type_filter,
            min_importance,
        )

    def _retrieve_scored_sync(
        self,
        project_id: str | None,
        query: str,
        limit: int,
        status_filter: MemoryStatus | None,
        tier_filter: MemoryLevel | None,
        memory_type_filter: MemoryType | None,
        min_importance: float,
    ) -> tuple[_ScoredMatch, ...]:
        if limit < 0:
            raise ValueError("retrieve limit must be >= 0")
        if limit == 0:
            return ()

        if project_id is None or project_id == "":
            base_conditions: list[str] = [
                "(project_id = '' OR project_id IS NULL OR project_id = '__global__')"
            ]
            base_params: list[object] = []
        else:
            base_conditions = ["project_id = ?"]
            base_params = [project_id]

        if status_filter is not None:
            base_conditions.append("status = ?")
            base_params.append(status_filter.value)

        if tier_filter is not None:
            base_conditions.append("level = ?")
            base_params.append(tier_filter.value)

        if min_importance > 0.0:
            base_conditions.append("importance >= ?")
            base_params.append(min_importance)

        base_where = " AND ".join(base_conditions)
        query_lower = query.strip().lower()
        query_terms = set(query_lower.split()) if query_lower else set()

        with self._connect() as connection:
            if query_lower:
                # Candidate selection covers exact phrase OR any individual query token.
                lex_clauses = ["content LIKE ? OR kind LIKE ?"]
                lex_params: list[object] = [f"%{query_lower}%", f"%{query_lower}%"]
                for term in query_terms:
                    term_pattern = f"%{term}%"
                    lex_clauses.append("content LIKE ? OR kind LIKE ?")
                    lex_params.extend([term_pattern, term_pattern])

                lex_where = f"({' OR '.join(lex_clauses)})"
                lex_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM memories
                    WHERE {base_where}
                      AND ({lex_where})
                    ORDER BY importance DESC, created_at DESC, rowid DESC
                    """,
                    (*base_params, *lex_params),
                ).fetchall()

                # Fallback: high-importance rows up to 200 rows.
                fallback_limit = max(200, limit * 4)
                imp_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM memories
                    WHERE {base_where}
                    ORDER BY importance DESC, created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (*base_params, fallback_limit),
                ).fetchall()

                # Merge: lex_rows first (preserve order), then imp_rows not already seen.
                seen_ids: set[str] = {str(r["memory_id"]) for r in lex_rows}
                merged_rows = list(lex_rows) + [
                    r for r in imp_rows if str(r["memory_id"]) not in seen_ids
                ]
            else:
                # Browse/filter mode: no lexical filter.
                fetch_limit = min(limit * 4, 200)
                merged_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM memories
                    WHERE {base_where}
                    ORDER BY importance DESC, created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (*base_params, fetch_limit),
                ).fetchall()

        records = [self._row_to_memory(row) for row in merged_rows]

        # Post-filter by memory_type.
        if memory_type_filter is not None:
            records = [r for r in records if r.memory_type == memory_type_filter]

        def _score_and_reasons(rec: MemoryRecord) -> tuple[float, tuple[str, ...], bool]:
            content_lower = rec.content.lower()
            reasons: list[str] = []

            has_exact_phrase = bool(query_lower and query_lower in content_lower)
            phrase_bonus = 0.5 if has_exact_phrase else 0.0
            if has_exact_phrase:
                reasons.append("exact_phrase")

            matched_terms = (
                [t for t in query_terms if t in content_lower] if query_terms else []
            )
            term_bonus = len(matched_terms) * 0.1
            if matched_terms:
                reasons.append(f"token_overlap:{len(matched_terms)}")

            reasons.append(f"importance:{rec.importance:.2f}")

            score = phrase_bonus + term_bonus + rec.importance * 0.4 + rec.confidence * 0.1
            has_lexical_match = bool(has_exact_phrase or len(matched_terms) > 0)
            return score, tuple(reasons), has_lexical_match

        scored: list[_ScoredMatch] = []
        for rec in records:
            s, r, has_lexical_match = _score_and_reasons(rec)
            # Explicit lexical-match evidence: exclude records with zero lexical match
            # when query is non-empty.
            if query_lower and not has_lexical_match:
                continue
            scored.append(_ScoredMatch(record=rec, score=s, reasons=r))

        scored.sort(key=lambda m: m.score, reverse=True)
        return tuple(scored[:limit])

    async def supersede(
        self,
        old_memory_id: str,
        new_record: MemoryRecord,
    ) -> None:
        """Atomic superseding transition: marks old record SUPERSEDED and inserts new one.

        Both operations occur within a single SQLite transaction.
        The new_record.supersedes field MUST reference old_memory_id.
        """
        await asyncio.to_thread(self._supersede_sync, old_memory_id, new_record)

    def _supersede_sync(
        self,
        old_memory_id: str,
        new_record: MemoryRecord,
    ) -> None:
        if new_record.supersedes != old_memory_id:
            raise ValueError(
                f"new_record.supersedes must equal old_memory_id '{old_memory_id}', "
                f"got '{new_record.supersedes}'"
            )
        if new_record.project_id == "__global__":
            raise ValueError(
                "RESERVED_SENTINEL: project_id='__global__' is a reserved legacy sentinel "
                "and cannot be used for new durable writes. Use project_id=None with "
                "scope='global' for canonical global memories."
            )
        updated_at = datetime.now(UTC).isoformat()

        with self._connect() as connection:
            # Step 1: mark old record SUPERSEDED.
            connection.execute(
                """
                UPDATE memories
                SET status = ?, metadata_json = json_patch(
                    metadata_json,
                    json_object('__status_updated_at', ?)
                )
                WHERE memory_id = ?
                """,
                (MemoryStatus.SUPERSEDED.value, updated_at, old_memory_id),
            )
            # Step 2: insert new record.
            connection.execute(
                """
                INSERT INTO memories (
                    memory_id, project_id, level, kind, content,
                    importance, metadata_json, created_at, status, logical_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_record.memory_id,
                    new_record.project_id if new_record.project_id is not None else "",
                    new_record.level.value,
                    new_record.kind,
                    new_record.content,
                    new_record.importance,
                    json.dumps(
                        self._serialize_metadata(new_record),
                        ensure_ascii=False,
                    ),
                    new_record.created_at.isoformat(),
                    new_record.status.value,
                    new_record.metadata.get("logical_key"),
                ),
            )

    async def get_by_logical_key(
        self,
        project_id: str | None,
        logical_key: str,
        *,
        status_filter: MemoryStatus = MemoryStatus.ACTIVE,
    ) -> tuple[MemoryRecord, ...]:
        """Fetch records matching a logical_key, optionally filtered by status."""
        return await asyncio.to_thread(
            self._get_by_logical_key_sync, project_id, logical_key, status_filter
        )

    def _get_by_logical_key_sync(
        self,
        project_id: str | None,
        logical_key: str,
        status_filter: MemoryStatus,
    ) -> tuple[MemoryRecord, ...]:
        with self._connect() as connection:
            if project_id is None or project_id == "":
                rows = connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE (project_id = '' OR project_id IS NULL OR project_id = '__global__')
                      AND logical_key = ?
                      AND status = ?
                    ORDER BY created_at DESC, rowid DESC
                    """,
                    (logical_key, status_filter.value),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE project_id = ?
                      AND logical_key = ?
                      AND status = ?
                    ORDER BY created_at DESC, rowid DESC
                    """,
                    (project_id, logical_key, status_filter.value),
                ).fetchall()
        return tuple(self._row_to_memory(row) for row in rows)

    async def get_by_conflict_identity(
        self,
        project_id: str | None,
        logical_key: str,
        *,
        subject: str,
        scope: str,
        memory_type: MemoryType,
        status_filter: MemoryStatus = MemoryStatus.ACTIVE,
    ) -> tuple[MemoryRecord, ...]:
        """Fetch ACTIVE records matching the composite conflict identity.

        Conflict identity = (project_id, logical_key, subject, scope, memory_type).
        Records with the same logical_key but different subject, scope, or
        memory_type are NOT treated as conflicts.
        """
        candidates = await self.get_by_logical_key(
            project_id, logical_key, status_filter=status_filter
        )
        return tuple(
            r for r in candidates
            if r.subject == subject
            and r.scope == scope
            and r.memory_type == memory_type
        )

    async def get_by_id(self, memory_id: str) -> MemoryRecord | None:
        """Fetch a single record by memory_id, or None if not found."""
        return await asyncio.to_thread(self._get_by_id_sync, memory_id)

    def _get_by_id_sync(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return self._row_to_memory(row) if row else None

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        raw_meta = json.loads(str(row["metadata_json"]))
        canonical = raw_meta.pop("__canonical__", None) if isinstance(raw_meta, dict) else None

        row_keys = row.keys() if hasattr(row, "keys") else ()
        row_status = (
            row["status"]
            if "status" in row_keys and row["status"]
            else (canonical.get("status") if canonical else MemoryStatus.ACTIVE.value)
        )

        if canonical and isinstance(canonical, dict):
            # Canonical record reconstruction
            resolved_project_id = canonical.get("project_id")
            if resolved_project_id in ("", "__global__"):
                resolved_project_id = None
            elif (
                resolved_project_id is None
                and row["project_id"]
                and str(row["project_id"]) not in ("", "__global__")
            ):
                resolved_project_id = str(row["project_id"])

            return MemoryRecord(
                memory_id=str(row["memory_id"]),
                content=str(row["content"]),
                project_id=resolved_project_id,
                memory_type=normalize_memory_type(str(canonical.get("memory_type", row["kind"]))),
                tier=normalize_memory_tier(str(canonical.get("tier", row["level"]))),
                importance=float(row["importance"]),
                confidence=float(canonical.get("confidence", 1.0)),
                subject=str(canonical.get("subject", "project")),
                scope=str(canonical.get("scope", "project")),
                source=str(canonical.get("source", "runtime")),
                status=normalize_memory_status(str(row_status)),
                supersedes=canonical.get("supersedes"),
                metadata=raw_meta,
                created_at=datetime.fromisoformat(str(row["created_at"])),
                updated_at=datetime.fromisoformat(str(canonical["updated_at"])) if canonical.get("updated_at") else None,
                last_accessed_at=datetime.fromisoformat(str(canonical["last_accessed_at"])) if canonical.get("last_accessed_at") else None,
                valid_from=datetime.fromisoformat(str(canonical["valid_from"])) if canonical.get("valid_from") else None,
                valid_until=datetime.fromisoformat(str(canonical["valid_until"])) if canonical.get("valid_until") else None,
                _custom_kind=canonical.get("custom_kind") or str(row["kind"]),
            )

        # Legacy row reconstruction (pre-Phase-03 data):
        project_val = (
            str(row["project_id"])
            if row["project_id"] and str(row["project_id"]) not in ("", "__global__")
            else None
        )
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            project_id=project_val,
            level=MemoryLevel(str(row["level"])),
            kind=str(row["kind"]),
            content=str(row["content"]),
            importance=float(row["importance"]),
            status=normalize_memory_status(str(row_status)),
            metadata=raw_meta if isinstance(raw_meta, dict) else {},
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    async def _archive_record(
        self,
        project_id: str | None,
        memory_id: str,
        updated_at: str,
    ) -> None:
        """Mark a memory record as ARCHIVED (internal; called by DurableMemoryService)."""
        await asyncio.to_thread(
            self._archive_record_sync, project_id, memory_id, updated_at
        )

    def _archive_record_sync(
        self,
        project_id: str | None,
        memory_id: str,
        updated_at: str,
    ) -> None:
        with self._connect() as connection:
            if project_id is None or project_id == "":
                connection.execute(
                    """
                    UPDATE memories
                    SET status = ?, metadata_json = json_patch(
                        metadata_json,
                        json_object('__status_updated_at', ?)
                    )
                    WHERE memory_id = ? AND (project_id = '' OR project_id IS NULL OR project_id = '__global__')
                    """,
                    (MemoryStatus.ARCHIVED.value, updated_at, memory_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE memories
                    SET status = ?, metadata_json = json_patch(
                        metadata_json,
                        json_object('__status_updated_at', ?)
                    )
                    WHERE memory_id = ? AND project_id = ?
                    """,
                    (MemoryStatus.ARCHIVED.value, updated_at, memory_id, project_id),
                )

