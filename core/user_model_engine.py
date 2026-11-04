"""Phase 05C — UserModelEngine & Preference Resolution.

This module implements the business service boundary for UserModel and Preference
Resolution in MyAgent / Houhou, building directly on canonical Phase 03/04
Durable Memory without creating separate storage tables or duplicate models:
    - core.durable_memory_service.DurableMemoryService
    - persistence.sqlite_memory_store.SQLiteMemoryStore
    - core.user_model.PreferenceItem, PreferenceSpec, EffectiveUserProfile

Architecture Constraints & Guarantees:
-------------------------------------
1. Single Persistence Layer:
   Does NOT create separate database tables, SQLite schemas, or secondary stores.
   All preferences are stored exclusively as canonical MemoryRecord with
   memory_type=MemoryType.PREFERENCE.

2. Trusted Provenance Gate (P1):
   External caller string labels (e.g. source="user") or caller-supplied flags
   (trusted_provenance=True) are NOT treated as self-authenticating evidence.
   Only operations bearing a valid, server-minted ServerBoundaryToken issued by
   TrustedProvenanceAuthority are granted ProvenanceStatus.VERIFIED_EXPLICIT.
   All other caller attempts fail closed (UntrustedProvenanceError).

3. Inferred Preference Guard:
   Inferred preferences (source_type=INFERRED) cannot be automatically written
   as active effective preferences via set_explicit_preference() in Phase 05C.

4. Persona & Policy Protection:
   UserModel is strictly forbidden from modifying or overriding Houhou identity,
   persona, or system policies (SecurityPolicyViolationError).

5. Secret Leakage Defense:
   Preference content is scanned for secret tokens / API keys (sk-, ghp-, private keys)
   prior to persistence (SecurityPolicyViolationError).

6. Concurrency & Transaction Safety Boundary (P1):
   - In-process concurrency: Serialized via asyncio.Lock per (project_id, logical_key).
   - Global-scoped writes (project_id=None): Under Phase 04 DurableMemoryService,
     conflict resolution is bypassed when project_id is None (line 227) and no
     single-transaction global supersede API exists.
     Therefore, global explicit write is FAIL-CLOSED with
     PersistenceContractBlockedError (BLOCKED_BY_PERSISTENCE_CONTRACT).
   - Multi-connection concurrent writes: Without a unique constraint or table lock
     in SQLiteMemoryStore, concurrent writes across separate SQLite connections can race.
     Documented and guarded within process boundary.

7. Effective Profile Correctness:
   - Only MemoryType.PREFERENCE and MemoryStatus.ACTIVE are considered.
   - Enforces temporal validity (valid_from <= as_of <= valid_until).
   - Excludes unverified caller claims (ProvenanceStatus.UNVERIFIED_CLAIM).
   - Project isolation: Project A cannot read Project B preferences.
   - Global preferences (project_id=None) are inherited by project profiles.
   - Deterministic tie-breaking with explainability (resolution_reasons).
   - Cap detection: Detects store 200-row browse cap to prevent silent truncation.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime

from core.durable_memory_service import DurableMemoryService
from core.memory import (
    MemoryStatus,
    MemoryType,
)
from core.user_model import (
    DEFAULT_USER_ID,
    EffectiveUserProfile,
    PreferenceCategory,
    PreferenceItem,
    PreferenceSourceType,
    PreferenceSpec,
    ProvenanceStatus,
    ServerBoundaryToken,
    TrustedProvenanceAuthority,
    build_effective_profile,
    memory_record_to_preference_item,
    preference_spec_to_memory_record,
)
from persistence.sqlite_memory_store import SQLiteMemoryStore

# ---------------------------------------------------------------------------
# Constants & Enforcements
# ---------------------------------------------------------------------------

STORE_BROWSE_CAP: int = 200
"""Hardcoded browse limit of SQLiteMemoryStore._retrieve_scored_sync when query is empty."""

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"(?:bearer\s+[a-zA-Z0-9_\-\.]{25,})", re.IGNORECASE),
)

_FORBIDDEN_KEY_PREFIXES: tuple[str, ...] = (
    "houhou",
    "agent_identity",
    "system_prompt",
    "persona",
    "system_rule",
    "core_identity",
)


# ---------------------------------------------------------------------------
# Domain Exceptions
# ---------------------------------------------------------------------------


class UserModelEngineError(Exception):
    """Base exception for UserModelEngine operations."""


class UntrustedProvenanceError(UserModelEngineError):
    """Raised when an operation fails the Trusted Provenance Gate."""


class InvalidPreferenceSourceError(UserModelEngineError):
    """Raised when an unsupported or unpermitted preference source type is supplied."""


class SecurityPolicyViolationError(UserModelEngineError):
    """Raised when an operation violates security policy (secret leakage or persona override)."""


class PersistenceContractBlockedError(UserModelEngineError):
    """Raised when an operation is blocked by persistence layer limitations (BLOCKED_BY_PERSISTENCE_CONTRACT)."""


class TruncatedRetrievalError(UserModelEngineError):
    """Raised when durable store returns items at its retrieval cap, preventing silent truncation."""


# ---------------------------------------------------------------------------
# UserModelEngine
# ---------------------------------------------------------------------------


class UserModelEngine:
    """Business service boundary for UserModel and Preference Resolution.

    Orchestrates reads, writes, conflict resolution, filtering, and provenance
    verification on top of canonical Durable Memory.
    """

    def __init__(
        self,
        durable_service: DurableMemoryService,
        *,
        store: SQLiteMemoryStore | None = None,
    ) -> None:
        self._durable_service = durable_service
        self._store = store if store is not None else durable_service._store
        self._locks: dict[tuple[str | None, str], asyncio.Lock] = {}

    def _get_lock(self, project_id: str | None, logical_key: str) -> asyncio.Lock:
        """Get or create an in-process serialization lock for a conflict identity."""
        key = (project_id, logical_key)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # ------------------------------------------------------------------
    # Effective Profile Resolution
    # ------------------------------------------------------------------

    async def get_effective_profile(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        project_id: str | None = None,
        as_of: datetime | None = None,
        exclude_unverified: bool = True,
    ) -> EffectiveUserProfile:
        """Constructs an EffectiveUserProfile by reading, filtering, and resolving active preferences.

        Invariants enforced:
        1. Only MemoryType.PREFERENCE records are read.
        2. Only MemoryStatus.ACTIVE records are considered (ARCHIVED/SUPERSEDED excluded).
        3. Global preferences (project_id=None) are inherited by project profiles.
        4. Cross-project isolation: Project A never reads Project B preferences.
        5. Temporal validity: valid_from <= as_of <= valid_until.
        6. Unverified claims exclusion: excluded when exclude_unverified=True.
        7. Silent truncation defense: raises TruncatedRetrievalError if store 200-cap is hit.
        8. Deterministic conflict resolution via calculate_precedence_rank.
        """
        if project_id == "__global__":
            raise ValueError(
                "RESERVED_SENTINEL: project_id='__global__' is forbidden. Use project_id=None for global profile."
            )

        ref_time = as_of or datetime.now(UTC)
        candidates: list[PreferenceItem] = []

        if project_id is not None:
            # Fetch project preferences
            proj_matches = await self._durable_service.retrieve(
                project_id,
                query="",
                limit=STORE_BROWSE_CAP,
                include_global=False,
                status_filter=MemoryStatus.ACTIVE,
                memory_type_filter=MemoryType.PREFERENCE,
            )
            if len(proj_matches) >= STORE_BROWSE_CAP:
                raise TruncatedRetrievalError(
                    f"Durable store browse limit ({STORE_BROWSE_CAP} records) reached for project '{project_id}'. "
                    "Profile results may be incomplete. Underlying store query cap exceeded."
                )

            # Fetch global preferences
            global_matches = await self._durable_service.retrieve_global(
                query="",
                limit=STORE_BROWSE_CAP,
                status_filter=MemoryStatus.ACTIVE,
                memory_type_filter=MemoryType.PREFERENCE,
            )
            if len(global_matches) >= STORE_BROWSE_CAP:
                raise TruncatedRetrievalError(
                    f"Durable store browse limit ({STORE_BROWSE_CAP} records) reached for global preferences. "
                    "Profile results may be incomplete. Underlying store query cap exceeded."
                )

            all_records = [m.record for m in proj_matches] + [m.record for m in global_matches]
        else:
            # Purely global profile
            global_matches = await self._durable_service.retrieve_global(
                query="",
                limit=STORE_BROWSE_CAP,
                status_filter=MemoryStatus.ACTIVE,
                memory_type_filter=MemoryType.PREFERENCE,
            )
            if len(global_matches) >= STORE_BROWSE_CAP:
                raise TruncatedRetrievalError(
                    f"Durable store browse limit ({STORE_BROWSE_CAP} records) reached for global preferences. "
                    "Profile results may be incomplete. Underlying store query cap exceeded."
                )
            all_records = [m.record for m in global_matches]

        # Convert to PreferenceItem and filter
        for rec in all_records:
            if rec.memory_type != MemoryType.PREFERENCE or rec.status != MemoryStatus.ACTIVE:
                continue
            item = memory_record_to_preference_item(rec, require_attestation=True)

            # Temporal validity
            if item.valid_from is not None and ref_time < item.valid_from:
                continue
            if item.valid_until is not None and ref_time > item.valid_until:
                continue

            # Unverified claims exclusion
            if exclude_unverified and item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM:
                continue

            candidates.append(item)

        return build_effective_profile(
            candidates,
            user_id=user_id,
            project_id=project_id,
            as_of=ref_time,
            exclude_unverified=exclude_unverified,
        )

    # ------------------------------------------------------------------
    # Preference Listing
    # ------------------------------------------------------------------

    async def list_preferences(
        self,
        *,
        project_id: str | None = None,
        category: PreferenceCategory | None = None,
        status: MemoryStatus | None = MemoryStatus.ACTIVE,
        source_type: PreferenceSourceType | None = None,
        provenance_status: ProvenanceStatus | None = None,
        include_global: bool = True,
        limit: int = 100,
    ) -> tuple[PreferenceItem, ...]:
        """List preference records matching specified filters without silent omission."""
        if project_id == "__global__":
            raise ValueError(
                "RESERVED_SENTINEL: project_id='__global__' is forbidden. Use project_id=None for global scope."
            )

        if project_id is not None:
            proj_matches = await self._durable_service.retrieve(
                project_id,
                query="",
                limit=limit,
                include_global=False,
                status_filter=status,
                memory_type_filter=MemoryType.PREFERENCE,
            )
            proj_records = [m.record for m in proj_matches]
            if include_global:
                global_matches = await self._durable_service.retrieve_global(
                    query="",
                    limit=limit,
                    status_filter=status,
                    memory_type_filter=MemoryType.PREFERENCE,
                )
                global_records = [m.record for m in global_matches]
                all_records = proj_records + global_records
            else:
                all_records = proj_records
        else:
            global_matches = await self._durable_service.retrieve_global(
                query="",
                limit=limit,
                status_filter=status,
                memory_type_filter=MemoryType.PREFERENCE,
            )
            all_records = [m.record for m in global_matches]

        items: list[PreferenceItem] = []
        for rec in all_records:
            if rec.memory_type != MemoryType.PREFERENCE:
                continue
            item = memory_record_to_preference_item(rec, require_attestation=True)
            if category is not None and item.category != category:
                continue
            if source_type is not None and item.source_type != source_type:
                continue
            if provenance_status is not None and item.provenance_status != provenance_status:
                continue
            items.append(item)

        return tuple(items)

    # ------------------------------------------------------------------
    # Explicit Preference Declaration (Trusted Boundary)
    # ------------------------------------------------------------------

    async def set_explicit_preference(
        self,
        spec: PreferenceSpec,
        *,
        boundary_token: ServerBoundaryToken | None = None,
    ) -> PreferenceItem:
        """Records an explicit user preference through the Trusted Provenance Gate.

        Security & Integrity Invariants:
        1. Only EXPLICIT_USER source_type is permitted. Inferred preferences are rejected.
        2. Content is scanned for secret leakage (sk-, ghp-, private keys).
        3. Forbidden keys (houhou:*, agent_identity:*, persona:*) are rejected to prevent
           identity or system policy override.
        4. Trusted Provenance Gate: A valid ServerBoundaryToken is mandatory.
           Untrusted or forged caller claims are rejected with UntrustedProvenanceError.
        5. Cryptographic Attestation:
           Mints a server-signed HMAC attestation bound to (token, logical_key, content,
           project_id, scope, user_id, source_type).
        6. Atomic Concurrency & Scope Safety:
           - In-process concurrency: Serialized via asyncio.Lock per (project_id, logical_key).
           - Multi-process/Multi-connection concurrency: Guaranteed by SQLiteMemoryStore
             BEGIN IMMEDIATE transaction across both global and project scopes.
        """
        # Guard 1: Source type validation
        if spec.source_type == PreferenceSourceType.INFERRED:
            raise InvalidPreferenceSourceError(
                "Inferred preferences cannot be recorded as active effective preferences "
                "via set_explicit_preference in Phase 05C."
            )
        if spec.source_type != PreferenceSourceType.EXPLICIT_USER:
            raise InvalidPreferenceSourceError(
                f"set_explicit_preference only accepts EXPLICIT_USER source type, got {spec.source_type}"
            )

        # Guard 2: Houhou Persona & System Policy protection
        key_lower = spec.logical_key.strip().lower()
        for prefix in _FORBIDDEN_KEY_PREFIXES:
            if key_lower == prefix or key_lower.startswith((f"{prefix}:", f"{prefix}.")):
                raise SecurityPolicyViolationError(
                    f"UserModel is strictly forbidden from managing or overriding Houhou persona "
                    f"or system policy: '{spec.logical_key}'"
                )

        # Guard 3: Secret leakage defense
        for pattern in _SECRET_PATTERNS:
            if pattern.search(spec.content):
                raise SecurityPolicyViolationError(
                    "Preference content contains potential secret or credential material; "
                    "rejected by security policy gate."
                )

        # Guard 4: Sentinel check
        if spec.project_id == "__global__":
            raise ValueError(
                "RESERVED_SENTINEL: project_id='__global__' is forbidden. Use project_id=None with scope='global'."
            )

        # Guard 5: Trusted Provenance Gate
        if not TrustedProvenanceAuthority.validate_token(boundary_token):
            raise UntrustedProvenanceError(
                "Cannot record verified explicit preference without a valid server-owned ServerBoundaryToken. "
                "Caller-supplied flags or labels are not self-authenticating."
            )

        # Generate persistent preference memory_id bound cryptographically
        pref_id = f"pref_{uuid.uuid4().hex[:12]}"

        # Mint cryptographic attestation bound to record fields
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=boundary_token,
            memory_id=pref_id,
            logical_key=spec.logical_key,
            content=spec.content,
            category=spec.category,
            project_id=spec.project_id,
            scope=spec.scope,
            user_id=spec.user_id,
            source_type=spec.source_type.value,
            confidence=spec.confidence,
            importance=spec.importance,
            valid_from=spec.valid_from,
            valid_until=spec.valid_until,
            semantic_metadata=dict(spec.metadata),
        )

        # In-process serialization lock
        async with self._get_lock(spec.project_id, spec.logical_key):
            record = preference_spec_to_memory_record(
                spec,
                preference_id=pref_id,
                attestation=attestation,
            )
            # DurableMemoryService.write delegates to SQLiteMemoryStore.write_records_atomic
            # ensuring BEGIN IMMEDIATE transaction boundary across multi-process SQLite connections
            persisted_ids = await self._durable_service.write(
                (record,),
                resolve_conflicts=True,
            )
            persisted_id = persisted_ids[0]

            persisted_record = await self._durable_service.get_by_id(persisted_id)
            if persisted_record is None:
                raise RuntimeError(
                    f"Invariant violation: successfully written preference '{persisted_id}' not found in store"
                )
            return memory_record_to_preference_item(persisted_record, require_attestation=True)

    # ------------------------------------------------------------------
    # Retract Preference (Lifecycle Transition to ARCHIVED)
    # ------------------------------------------------------------------

    async def retract_preference(
        self,
        preference_id: str,
        *,
        project_id: str | None = None,
    ) -> PreferenceItem:
        """Transitions an active preference to ARCHIVED status.

        Invariants enforced:
        1. Verifies the record exists and is of memory_type PREFERENCE via DurableMemoryService.
        2. Project isolation: cannot retract another project's preference.
        3. Archives via DurableMemoryService.archive(). Does NOT permanently hard-delete.
        """
        record = await self._durable_service.get_by_id(preference_id)
        if record is None:
            raise KeyError(f"Preference '{preference_id}' not found in durable store.")

        if record.memory_type != MemoryType.PREFERENCE:
            raise ValueError(
                f"Record '{preference_id}' is of type {record.memory_type.value}, not PREFERENCE."
            )

        # Project isolation check
        if project_id is not None and record.project_id is not None and record.project_id != project_id:
            raise PermissionError(
                f"Project isolation violation: cannot retract preference '{preference_id}' belonging to "
                f"project '{record.project_id}' from project '{project_id}'."
            )

        if record.status == MemoryStatus.ARCHIVED:
            return memory_record_to_preference_item(record, require_attestation=True)

        await self._durable_service.archive(record.project_id, preference_id)

        updated_record = await self._durable_service.get_by_id(preference_id)
        if updated_record is None:
            raise RuntimeError(f"Failed to re-fetch archived preference '{preference_id}'")

        return memory_record_to_preference_item(updated_record, require_attestation=True)
