from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MemoryType(StrEnum):
    """Canonical semantic memory type vocabulary.
    
    Decoupled completely from retention tier / storage level.
    """
    EPISODIC = "episodic"      # Specific event or interaction that occurred
    SEMANTIC = "semantic"      # Generalized factual or conceptual memory
    PREFERENCE = "preference"  # Durable preference attributed to a subject
    PROJECT = "project"        # Project-specific decisions, constraints, or state
    LESSON = "lesson"          # Conclusion learned from an outcome or mistake
    PROCEDURE = "procedure"    # Reusable way of performing a task


class MemoryLevel(StrEnum):
    """Canonical memory retention / storage tier.
    
    Decoupled from semantic memory type.
    """
    L0 = "l0"  # Short-term / session context
    L1 = "l1"  # Working / task-scoped context
    L2 = "l2"  # Project rules, conventions, and architectural context
    L3 = "l3"  # System-wide durable learned knowledge


# Canonical alias for clarity:
MemoryTier = MemoryLevel


class MemoryStatus(StrEnum):
    """Lifecycle status of a canonical memory record."""
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


def normalize_memory_type(val: str | MemoryType) -> MemoryType:
    """Normalize input string or enum into canonical MemoryType."""
    if isinstance(val, MemoryType):
        return val
    raw = str(val).strip().lower()
    for m in MemoryType:
        if m.value == raw:
            return m
    if raw in {"decision", "constraint", "architecture", "convention", "state"}:
        return MemoryType.PROJECT
    if raw in {"note", "fact", "concept", "knowledge"}:
        return MemoryType.SEMANTIC
    if raw in {"preference", "user_preference", "user_choice"}:
        return MemoryType.PREFERENCE
    if raw in {"lesson", "mistake", "learned", "insight"}:
        return MemoryType.LESSON
    if raw in {"procedure", "workflow", "routine", "how_to"}:
        return MemoryType.PROCEDURE
    if raw in {"episodic", "event", "session", "chat", "interaction"}:
        return MemoryType.EPISODIC
    return MemoryType.SEMANTIC


def normalize_memory_tier(val: str | MemoryLevel) -> MemoryLevel:
    """Normalize input string or enum into canonical MemoryLevel / MemoryTier."""
    if isinstance(val, MemoryLevel):
        return val
    raw = str(val).strip().lower()
    for m in MemoryLevel:
        if m.value == raw:
            return m
    legacy_map = {
        "l0_episodic": MemoryLevel.L0,
        "l1_working": MemoryLevel.L1,
        "l2_semantic": MemoryLevel.L2,
        "l3_long_term": MemoryLevel.L3,
    }
    if raw in legacy_map:
        return legacy_map[raw]
    return MemoryLevel.L1


def normalize_memory_status(val: str | MemoryStatus) -> MemoryStatus:
    """Normalize input string or enum into canonical MemoryStatus."""
    if isinstance(val, MemoryStatus):
        return val
    raw = str(val).strip().lower()
    for s in MemoryStatus:
        if s.value == raw:
            return s
    return MemoryStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Canonical memory domain model for MyAgent / Houhou cognitive architecture.
    
    Preserves strict separation between:
    - Semantic MemoryType (what kind of knowledge this is)
    - Retention MemoryTier / MemoryLevel (where/how long this memory persists)
    """
    memory_id: str
    content: str
    project_id: str | None = None
    memory_type: MemoryType = MemoryType.SEMANTIC
    tier: MemoryLevel = MemoryLevel.L1
    importance: float = 0.5
    confidence: float = 1.0
    subject: str = "project"
    scope: str = "project"
    source: str = "runtime"
    status: MemoryStatus = MemoryStatus.ACTIVE
    supersedes: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    last_accessed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    _custom_kind: str | None = field(default=None, compare=False)

    def __init__(
        self,
        memory_id: str,
        project_id: str | None = None,
        level: MemoryLevel | str | None = None,
        kind: str | MemoryType | None = None,
        content: str = "",
        importance: float = 0.5,
        metadata: Mapping[str, object] | None = None,
        created_at: datetime | str | None = None,
        *,
        memory_type: MemoryType | str | None = None,
        tier: MemoryLevel | str | None = None,
        confidence: float = 1.0,
        subject: str = "project",
        scope: str = "project",
        source: str = "runtime",
        status: MemoryStatus | str = MemoryStatus.ACTIVE,
        supersedes: str | None = None,
        updated_at: datetime | str | None = None,
        last_accessed_at: datetime | str | None = None,
        valid_from: datetime | str | None = None,
        valid_until: datetime | str | None = None,
        _custom_kind: str | None = None,
    ) -> None:
        if not memory_id or not str(memory_id).strip():
            raise ValueError("memory_id must not be empty")
        if project_id is not None and not str(project_id).strip():
            raise ValueError("project_id must not be empty string if provided")
        if not content or not str(content).strip():
            raise ValueError("memory content must not be empty")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        # Tier / level resolution
        if tier is not None:
            resolved_tier = normalize_memory_tier(tier)
        elif level is not None:
            resolved_tier = normalize_memory_tier(level)
        else:
            resolved_tier = MemoryLevel.L1

        # MemoryType / kind resolution
        resolved_custom_kind = _custom_kind
        if memory_type is not None:
            resolved_type = normalize_memory_type(memory_type)
        elif kind is not None:
            resolved_type = normalize_memory_type(kind)
            if not isinstance(kind, MemoryType) and str(kind) != resolved_type.value:
                resolved_custom_kind = str(kind)
        else:
            resolved_type = MemoryType.SEMANTIC

        resolved_status = normalize_memory_status(status)

        # Datetime normalization
        if created_at is None:
            resolved_created = datetime.now(UTC)
        elif isinstance(created_at, str):
            resolved_created = datetime.fromisoformat(created_at)
        else:
            resolved_created = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)

        resolved_updated = datetime.fromisoformat(updated_at) if isinstance(updated_at, str) else updated_at
        resolved_accessed = datetime.fromisoformat(last_accessed_at) if isinstance(last_accessed_at, str) else last_accessed_at
        resolved_from = datetime.fromisoformat(valid_from) if isinstance(valid_from, str) else valid_from
        resolved_until = datetime.fromisoformat(valid_until) if isinstance(valid_until, str) else valid_until

        resolved_meta = dict(metadata) if metadata is not None else {}
        if "__canonical__" in resolved_meta:
            raise ValueError("metadata key '__canonical__' is reserved for internal store persistence")

        resolved_project_id = str(project_id).strip() if project_id else None

        # Scope resolution with consistency invariant:
        # If project_id is None, scope must not falsely imply a specific project.
        if scope is None or scope == "project":
            if resolved_project_id is None:
                resolved_scope = "global"
            else:
                resolved_scope = "project"
        else:
            resolved_scope = str(scope).strip()
            if resolved_project_id is None and resolved_scope == "project":
                resolved_scope = "global"

        object.__setattr__(self, "memory_id", str(memory_id).strip())
        object.__setattr__(self, "content", str(content).strip())
        object.__setattr__(self, "project_id", resolved_project_id)
        object.__setattr__(self, "memory_type", resolved_type)
        object.__setattr__(self, "tier", resolved_tier)
        object.__setattr__(self, "importance", float(importance))
        object.__setattr__(self, "confidence", float(confidence))
        object.__setattr__(self, "subject", str(subject).strip())
        object.__setattr__(self, "scope", resolved_scope)
        object.__setattr__(self, "source", str(source).strip())
        object.__setattr__(self, "status", resolved_status)
        object.__setattr__(self, "supersedes", str(supersedes).strip() if supersedes else None)
        object.__setattr__(self, "metadata", resolved_meta)
        object.__setattr__(self, "created_at", resolved_created)
        object.__setattr__(self, "updated_at", resolved_updated)
        object.__setattr__(self, "last_accessed_at", resolved_accessed)
        object.__setattr__(self, "valid_from", resolved_from)
        object.__setattr__(self, "valid_until", resolved_until)
        object.__setattr__(self, "_custom_kind", resolved_custom_kind)

    @property
    def level(self) -> MemoryLevel:
        """Backward-compatible alias for retention tier."""
        return self.tier

    @property
    def kind(self) -> str:
        """Backward-compatible alias for memory kind / type string."""
        return self._custom_kind or self.memory_type.value

    def to_dict(self) -> dict[str, object]:
        """Serialize canonical MemoryRecord to dict."""
        return {
            "memory_id": self.memory_id,
            "project_id": self.project_id,
            "memory_type": self.memory_type.value,
            "tier": self.tier.value,
            "level": self.tier.value,
            "kind": self.kind,
            "content": self.content,
            "importance": self.importance,
            "confidence": self.confidence,
            "subject": self.subject,
            "scope": self.scope,
            "source": self.source,
            "status": self.status.value,
            "supersedes": self.supersedes,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> MemoryRecord:
        """Deserialize dict to canonical MemoryRecord."""
        tier = data.get("tier") or data.get("level") or MemoryLevel.L1
        memory_type = data.get("memory_type") or data.get("kind") or MemoryType.SEMANTIC
        custom_kind = str(data["kind"]) if "kind" in data and str(data["kind"]) != str(memory_type) else None

        created_at_val = data.get("created_at")
        created_at = datetime.fromisoformat(str(created_at_val)) if created_at_val else None

        updated_at_val = data.get("updated_at")
        updated_at = datetime.fromisoformat(str(updated_at_val)) if updated_at_val else None

        last_accessed_at_val = data.get("last_accessed_at")
        last_accessed_at = datetime.fromisoformat(str(last_accessed_at_val)) if last_accessed_at_val else None

        valid_from_val = data.get("valid_from")
        valid_from = datetime.fromisoformat(str(valid_from_val)) if valid_from_val else None

        valid_until_val = data.get("valid_until")
        valid_until = datetime.fromisoformat(str(valid_until_val)) if valid_until_val else None

        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}

        return cls(
            memory_id=str(data["memory_id"]),
            content=str(data["content"]),
            project_id=str(data["project_id"]) if data.get("project_id") is not None else None,
            memory_type=normalize_memory_type(str(memory_type)),
            tier=normalize_memory_tier(str(tier)),
            importance=float(data.get("importance", 0.5)),
            confidence=float(data.get("confidence", 1.0)),
            subject=str(data.get("subject", "project")),
            scope=str(data.get("scope", "project")),
            source=str(data.get("source", "runtime")),
            status=normalize_memory_status(str(data.get("status", MemoryStatus.ACTIVE.value))),
            supersedes=str(data["supersedes"]) if data.get("supersedes") is not None else None,
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
            last_accessed_at=last_accessed_at,
            valid_from=valid_from,
            valid_until=valid_until,
            _custom_kind=custom_kind,
        )


def legacy_entry_to_memory_record(
    entry: Any,
    project_id: str | None = None,
) -> MemoryRecord:
    """Explicit compatibility adapter: maps legacy MemoryEntry to canonical MemoryRecord.
    
    Preserves both semantic type and retention tier without loss.
    Does NOT infer unsupported PROJECT semantics merely from L1 retention tier.
    """
    level_raw = str(entry.level.value if hasattr(entry.level, "value") else entry.level).lower()
    key_lower = str(entry.key or "").lower()

    is_fallback = False

    # Check for explicit semantic evidence in key if available
    if "decision" in key_lower or "constraint" in key_lower or "architecture" in key_lower:
        m_type = MemoryType.PROJECT
    elif "preference" in key_lower:
        m_type = MemoryType.PREFERENCE
    elif "lesson" in key_lower or "mistake" in key_lower:
        m_type = MemoryType.LESSON
    elif "procedure" in key_lower or "workflow" in key_lower:
        m_type = MemoryType.PROCEDURE
    elif "chat" in key_lower or "event" in key_lower or "episodic" in level_raw or "l0" in level_raw:
        m_type = MemoryType.EPISODIC
    elif "l2" in level_raw or "l3" in level_raw or "semantic" in level_raw or "long_term" in level_raw:
        m_type = MemoryType.SEMANTIC
    elif "l1" in level_raw or "working" in level_raw:
        # Conservative compatibility fallback: do not infer PROJECT solely from working retention level
        m_type = MemoryType.SEMANTIC
        is_fallback = True
    else:
        m_type = MemoryType.SEMANTIC
        is_fallback = True

    # Tier resolution
    if "l0" in level_raw or "episodic" in level_raw:
        tier = MemoryLevel.L0
    elif "l1" in level_raw or "working" in level_raw:
        tier = MemoryLevel.L1
    elif "l2" in level_raw or "semantic" in level_raw:
        tier = MemoryLevel.L2
    elif "l3" in level_raw or "long_term" in level_raw:
        tier = MemoryLevel.L3
    else:
        tier = MemoryLevel.L1

    created_at = datetime.fromisoformat(entry.created_at) if isinstance(entry.created_at, str) else entry.created_at
    updated_at = datetime.fromisoformat(entry.updated_at) if isinstance(entry.updated_at, str) else entry.updated_at

    meta: dict[str, object] = {
        "key": entry.key,
        "source": entry.source,
        "recall_count": entry.recall_count,
        "legacy_level": level_raw,
    }
    if is_fallback:
        meta["legacy_compatibility_fallback"] = True

    return MemoryRecord(
        memory_id=entry.memory_id,
        content=entry.content,
        project_id=project_id,
        memory_type=m_type,
        tier=tier,
        importance=0.5,
        confidence=1.0,
        subject=entry.key or "project",
        scope=f"project:{project_id}" if project_id else "global",
        source=entry.source or "runtime",
        status=MemoryStatus.ACTIVE,
        metadata=meta,
        created_at=created_at,
        updated_at=updated_at,
        _custom_kind=entry.key,
    )


def memory_record_to_legacy_entry(record: MemoryRecord) -> Any:
    """Explicit compatibility adapter: maps canonical MemoryRecord to legacy MemoryEntry."""
    from core.memory_consolidation import MemoryEntry, MemoryLevel as LegacyMemoryLevel

    # Restore original legacy level from provenance metadata when available
    legacy_level: LegacyMemoryLevel | None = None
    legacy_level_val = record.metadata.get("legacy_level")
    if isinstance(legacy_level_val, str):
        raw_lower = legacy_level_val.lower()
        if "l0" in raw_lower or "episodic" in raw_lower:
            legacy_level = LegacyMemoryLevel.L0_EPISODIC
        elif "l1" in raw_lower or "working" in raw_lower:
            legacy_level = LegacyMemoryLevel.L1_WORKING
        elif "l2" in raw_lower or "semantic" in raw_lower:
            legacy_level = LegacyMemoryLevel.L2_SEMANTIC
        elif "l3" in raw_lower or "long_term" in raw_lower:
            legacy_level = LegacyMemoryLevel.L3_LONG_TERM

    if legacy_level is None:
        if record.tier == MemoryLevel.L0:
            legacy_level = LegacyMemoryLevel.L0_EPISODIC
        elif record.tier == MemoryLevel.L1:
            legacy_level = LegacyMemoryLevel.L1_WORKING
        elif record.tier == MemoryLevel.L2:
            legacy_level = LegacyMemoryLevel.L2_SEMANTIC
        elif record.tier == MemoryLevel.L3:
            legacy_level = LegacyMemoryLevel.L3_LONG_TERM
        else:
            legacy_level = LegacyMemoryLevel.L1_WORKING

    key = str(record.metadata.get("key") or record._custom_kind or record.memory_id)
    recall_count = int(record.metadata.get("recall_count", 0))
    created_iso = record.created_at.isoformat()
    updated_iso = record.updated_at.isoformat() if record.updated_at else created_iso

    return MemoryEntry(
        memory_id=record.memory_id,
        level=legacy_level,
        key=key,
        content=record.content,
        source=record.source,
        recall_count=recall_count,
        created_at=created_iso,
        updated_at=updated_iso,
    )


NON_SEMANTIC_OPERATIONAL_METADATA_KEYS: frozenset[str] = frozenset({
    "__status_updated_at",
})
"""Explicit allowlist of persistence-operational metadata keys excluded from canonical equivalence comparison.

Only keys explicitly listed here are treated as non-semantic operational metadata.
Arbitrary caller metadata (including keys starting with '__') is treated as semantic
and must match for idempotent equivalence.
"""


def are_canonically_equivalent(a: MemoryRecord, b: MemoryRecord) -> bool:
    """Evaluate whether two MemoryRecord instances share identical persistence-relevant canonical semantics.

    Audit criteria:
    - memory_id (must match)
    - project_id (must match, including None for global)
    - memory_type (canonical semantic type)
    - tier / level (canonical retention tier)
    - content (exact string match)
    - importance (float tolerance 1e-6)
    - confidence (float tolerance 1e-6)
    - subject (exact string match)
    - scope (exact string match)
    - source (exact string match)
    - status (canonical lifecycle status)
    - supersedes (exact reference or None)
    - valid_from (datetime equality)
    - valid_until (datetime equality)
    - relevant metadata (including logical_key; only keys explicitly in
      NON_SEMANTIC_OPERATIONAL_METADATA_KEYS={'__status_updated_at'} are excluded).

    Explicitly excluded operational lifecycle timestamps:
    - created_at, updated_at, last_accessed_at (operational lifecycle timestamps
      generated during write/access transitions that do not alter memory semantic content).
    """
    if a.memory_id != b.memory_id:
        return False
    if a.project_id != b.project_id:
        return False
    if a.memory_type != b.memory_type:
        return False
    if a.tier != b.tier:
        return False
    if a.content != b.content:
        return False
    if abs(a.importance - b.importance) > 1e-6:
        return False
    if abs(a.confidence - b.confidence) > 1e-6:
        return False
    if a.subject != b.subject:
        return False
    if a.scope != b.scope:
        return False
    if a.source != b.source:
        return False
    if a.status != b.status:
        return False
    if a.supersedes != b.supersedes:
        return False
    if a.valid_from != b.valid_from:
        return False
    if a.valid_until != b.valid_until:
        return False

    meta_a = {k: v for k, v in a.metadata.items() if k not in NON_SEMANTIC_OPERATIONAL_METADATA_KEYS}
    meta_b = {k: v for k, v in b.metadata.items() if k not in NON_SEMANTIC_OPERATIONAL_METADATA_KEYS}
    if meta_a != meta_b:
        return False

    return True

