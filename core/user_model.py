"""Phase 05B — UserModel Core Domain Model & Conversion Contracts.

This module defines the canonical domain model for UserModel and user preference
management in MyAgent / Houhou, building strictly on top of the Phase 03/04
frozen memory contracts:
    - core.memory.MemoryRecord
    - core.memory.MemoryType.PREFERENCE
    - core.memory.MemoryLevel (L2 for project, L3 for global)
    - core.memory.MemoryStatus (ACTIVE, SUPERSEDED, ARCHIVED)

Architecture Constraints & Invariants:
-------------------------------------
1. Single Domain Model:
   PreferenceItem is NOT a competing memory class. It is the typed domain projection
   of a canonical MemoryRecord with memory_type=MemoryType.PREFERENCE.

2. Single-User Attribution vs Multi-User Reality:
   user_id is an attribution metadata tag (defaulting to "default_user"), NOT a
   cryptographic identity, authentication claim, or tenant isolation boundary.
   MyAgent currently runs as a local single-user system.

3. Archive Semantics (Soft Tombstone):
   MemoryStatus.ARCHIVED represents an inactive historical audit state. It is NOT
   a permanent hard deletion. Archived items are excluded from EffectiveUserProfile
   but preserved in durable storage for provenance and historical audit.

4. Source Provenance & Trust Boundary:
   A raw string label (e.g. source="user") provided by an external caller or legacy
   record is NOT treated as verified evidence of user intent.
   The system strictly distinguishes:
       - ProvenanceStatus.VERIFIED_EXPLICIT: Directly verified user statement originating
         from an authenticated caller boundary (trusted_provenance=True).
       - ProvenanceStatus.UNVERIFIED_CLAIM: Raw unverified caller claim without a trusted
         boundary token. Degraded to Rank 1 so forged claims cannot hijack verified preferences.
       - ProvenanceStatus.INFERRED_HYPOTHESIS: Heuristic observation with required evidence.
       - ProvenanceStatus.SYSTEM_BASELINE: Built-in fallback baseline.
       - ProvenanceStatus.LEGACY_UNCLASSIFIED: Historical record lacking provenance metadata.

5. Deterministic Precedence Hierarchy:
   When resolving conflicting active preferences for the same logical_key, the
   following strict precedence rank is applied:
       Rank 5: PROJECT + VERIFIED_EXPLICIT (Highest)
       Rank 4: GLOBAL  + VERIFIED_EXPLICIT
       Rank 3: PROJECT + INFERRED_HYPOTHESIS
       Rank 2: GLOBAL  + INFERRED_HYPOTHESIS
       Rank 1: UNVERIFIED_CLAIM (Degraded unverified/forged caller claims)
       Rank 0: SYSTEM_BASELINE / LEGACY_UNCLASSIFIED (Lowest)
   A verified explicit preference CANNOT be overridden by an inferred or unverified
   preference regardless of timestamp. Within the same rank, the newer created_at
   timestamp breaks ties.

6. Legacy Fallback Policy:
   Historical records from Phase 03/04 lacking UserModel metadata are handled with
   explicit safe policies:
       - Missing category: Default to PreferenceCategory.UNCLASSIFIED (not falsely assumed TECHNICAL).
       - Missing logical_key: Default to "unkeyed:{memory_id}".
       - Missing provenance: Default to ProvenanceStatus.LEGACY_UNCLASSIFIED.
       - Untrusted source='runtime': Default to PreferenceSourceType.SYSTEM_DEFAULT.

7. Global Superseding Concurrency Boundary:
   Any multi-step read-then-write superseding pattern executed outside a single
   SQLite transaction is inherently non-atomic. Callers must acknowledge this
   limitation until atomic transaction support is extended to global scopes.

8. Defect Isolation (MemoryMatch.logical_key):
   MemoryRecord does not define a logical_key property (stored in metadata).
   UserModel strictly accesses logical_key through record.metadata["logical_key"],
   avoiding the known AttributeError on MemoryMatch.logical_key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import types
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    normalize_memory_type,
)

# ---------------------------------------------------------------------------
# Reserved Metadata Keys & Cryptographic Constants
# ---------------------------------------------------------------------------

PROVENANCE_ATTESTATION_KEY: str = "__provenance_attestation__"
"""Metadata key storing server-signed cryptographic provenance attestation."""

RESERVED_USER_MODEL_METADATA_KEYS: frozenset[str] = frozenset({
    "__canonical__",
    PROVENANCE_ATTESTATION_KEY,
    "logical_key",
    "category",
    "source_type",
    "provenance_status",
    "trusted_provenance",
    "user_id",
    "inferred_evidence",
})

DEFAULT_USER_ID: str = "default_user"
"""Default contextual attribution tag for the local single-user agent."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PreferenceCategory(StrEnum):
    """Categorization of user preferences for structured retrieval and reasoning."""

    COMMUNICATION = "communication"  # Language, tone, verbosity, formatting conventions
    TECHNICAL = "technical"          # Languages, frameworks, typing, linting, toolchains
    WORKFLOW = "workflow"            # Git patterns, testing methodologies, review protocols
    CONSTRAINT = "constraint"        # Hard restrictions: forbidden commands, forbidden tools
    UNCLASSIFIED = "unclassified"    # Legacy or unverified records without explicit category


class PreferenceSourceType(StrEnum):
    """Provenance classification for preference data."""

    EXPLICIT_USER = "explicit_user"    # Directly stated by the user via conversation or tool
    INFERRED = "inferred"              # Deduced by agent from project patterns or observations
    SYSTEM_DEFAULT = "system_default"  # Built-in fallback baseline


class ProvenanceStatus(StrEnum):
    """Trust and provenance verification state of a preference."""

    VERIFIED_EXPLICIT = "verified_explicit"      # Asserted via verified caller boundary (trusted_provenance=True)
    UNVERIFIED_CLAIM = "unverified_claim"        # Caller claims explicit_user without trusted verification token
    INFERRED_HYPOTHESIS = "inferred_hypothesis"  # Heuristic inference backed by explicit evidence
    SYSTEM_BASELINE = "system_baseline"          # Built-in system baseline
    LEGACY_UNCLASSIFIED = "legacy_unclassified"  # Historical record lacking provenance


def normalize_preference_category(val: str | PreferenceCategory) -> PreferenceCategory:
    """Normalize input string or enum into canonical PreferenceCategory."""
    if isinstance(val, PreferenceCategory):
        return val
    raw = str(val).strip().lower()
    for cat in PreferenceCategory:
        if cat.value == raw:
            return cat
    if raw in {"comm", "tone", "style", "language", "format"}:
        return PreferenceCategory.COMMUNICATION
    if raw in {"tech", "code", "coding", "stack", "linter", "typing"}:
        return PreferenceCategory.TECHNICAL
    if raw in {"flow", "git", "test", "testing", "ci"}:
        return PreferenceCategory.WORKFLOW
    if raw in {"rule", "restriction", "forbidden", "limit"}:
        return PreferenceCategory.CONSTRAINT
    if raw in {"unclassified", "legacy", "general", "unknown", "other"}:
        return PreferenceCategory.UNCLASSIFIED
    return PreferenceCategory.UNCLASSIFIED


def normalize_preference_source_type(val: str | PreferenceSourceType) -> PreferenceSourceType:
    """Normalize input string or enum into canonical PreferenceSourceType."""
    if isinstance(val, PreferenceSourceType):
        return val
    raw = str(val).strip().lower()
    for st in PreferenceSourceType:
        if st.value == raw:
            return st
    if raw in {"explicit", "user", "manual", "direct", "explicit_user"}:
        return PreferenceSourceType.EXPLICIT_USER
    if raw in {"inferred", "learned", "observed", "heuristic", "auto"}:
        return PreferenceSourceType.INFERRED
    return PreferenceSourceType.SYSTEM_DEFAULT


def normalize_provenance_status(val: str | ProvenanceStatus) -> ProvenanceStatus:
    """Normalize input string or enum into canonical ProvenanceStatus."""
    if isinstance(val, ProvenanceStatus):
        return val
    raw = str(val).strip().lower()
    for ps in ProvenanceStatus:
        if ps.value == raw:
            return ps
    if raw in {"verified", "verified_explicit", "trusted"}:
        return ProvenanceStatus.VERIFIED_EXPLICIT
    if raw in {"unverified", "unverified_claim", "untrusted", "forged"}:
        return ProvenanceStatus.UNVERIFIED_CLAIM
    if raw in {"inferred", "inferred_hypothesis", "hypothesis"}:
        return ProvenanceStatus.INFERRED_HYPOTHESIS
    if raw in {"system", "baseline", "system_baseline"}:
        return ProvenanceStatus.SYSTEM_BASELINE
    return ProvenanceStatus.LEGACY_UNCLASSIFIED


# ---------------------------------------------------------------------------
# Trusted Provenance Gate & Boundary Authority
# ---------------------------------------------------------------------------


class ServerBoundaryToken:
    """Cryptographically signed server-minted token proving trusted application boundary origin.

    Cannot be forged or fabricated by model-generated dictionaries or caller strings.
    """

    __slots__ = ("_created_at", "_issuer", "_secret_signature", "_token_id")

    def __init__(self, issuer: str, token_id: str, secret_signature: str) -> None:
        self._issuer = issuer
        self._token_id = token_id
        self._created_at = datetime.now(UTC)
        self._secret_signature = secret_signature

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def token_id(self) -> str:
        return self._token_id


class TrustedProvenanceAuthority:
    """Server-owned authority responsible for issuing boundary tokens and signing/verifying record attestations."""

    _KEY_PATH: Path | None = None
    _SECRET: str | None = None
    _CACHED_KEY_PATH: Path | None = None

    @classmethod
    def set_secret(cls, secret: str | None) -> None:
        """Explicitly set or override in-memory secret (useful for isolated testing)."""
        cls._SECRET = secret
        cls._CACHED_KEY_PATH = None

    @classmethod
    def set_key_path(cls, path: Path | None) -> None:
        """Set custom key file path or clear override when None."""
        cls._KEY_PATH = Path(path).expanduser().resolve() if path is not None else None
        cls._SECRET = None
        cls._CACHED_KEY_PATH = None

    @classmethod
    def get_key_path(cls) -> Path:
        """Resolve the runtime-appropriate provenance key path and forbid immutable release writes."""
        from .runtime_environment import (
            assert_not_in_immutable_release,
            resolve_runtime_environment,
        )

        if cls._KEY_PATH is not None:
            resolved = cls._KEY_PATH.expanduser().resolve()
            assert_not_in_immutable_release(resolved)
            return resolved

        env_path = os.environ.get("MYAGENT_PROVENANCE_KEY_PATH")
        if env_path and env_path.strip():
            resolved = Path(env_path.strip()).expanduser().resolve()
            assert_not_in_immutable_release(resolved)
            return resolved

        cfg = resolve_runtime_environment()
        resolved = cfg.provenance_key_path
        assert_not_in_immutable_release(resolved, cfg.canonical_source_root)
        return resolved

    @classmethod
    def get_secret(cls) -> str:
        """Get or load persistent HMAC secret key without regenerating existing keys."""
        from .runtime_environment import assert_not_in_immutable_release

        if cls._SECRET is not None and cls._CACHED_KEY_PATH is None:
            return cls._SECRET

        env_key = os.environ.get("MYAGENT_PROVENANCE_SECRET")
        if env_key and env_key.strip():
            return env_key.strip()

        key_path = cls.get_key_path()
        if cls._SECRET is not None and cls._CACHED_KEY_PATH == key_path:
            return cls._SECRET

        if key_path.exists():
            content = key_path.read_text(encoding="utf-8").strip()
            if not content:
                raise RuntimeError(
                    f"Existing provenance key file '{key_path}' is empty; refusing to regenerate or overwrite."
                )
            cls._SECRET = content
            cls._CACHED_KEY_PATH = key_path
            return cls._SECRET

        assert_not_in_immutable_release(key_path)
        new_secret = secrets.token_hex(32)
        fd: int | None = None
        created_file = False
        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_file = True
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = None
                fh.write(new_secret)
                fh.flush()
                os.fsync(fh.fileno())
            key_path.chmod(0o600)
            persisted = key_path.read_text(encoding="utf-8").strip()
            if persisted != new_secret:
                raise OSError(f"Provenance key verification failed after write to '{key_path}'.")
        except FileExistsError as exc:
            content = key_path.read_text(encoding="utf-8").strip()
            if not content:
                cls._SECRET = None
                cls._CACHED_KEY_PATH = None
                raise RuntimeError(
                    f"Existing provenance key file '{key_path}' is empty; refusing to regenerate or overwrite."
                ) from exc
            cls._SECRET = content
            cls._CACHED_KEY_PATH = key_path
            return cls._SECRET
        except Exception:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if created_file:
                try:
                    key_path.unlink(missing_ok=True)
                except OSError:
                    pass
            cls._SECRET = None
            cls._CACHED_KEY_PATH = None
            raise

        cls._SECRET = new_secret
        cls._CACHED_KEY_PATH = key_path
        return cls._SECRET

    @classmethod
    def issue_token(cls, issuer: str = "houhou_front_agent") -> ServerBoundaryToken:
        """Issue a cryptographically signed ServerBoundaryToken."""
        token_id = uuid.uuid4().hex
        signature = hmac.new(
            cls.get_secret().encode(),
            f"{issuer}:{token_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return ServerBoundaryToken(issuer=issuer, token_id=token_id, secret_signature=signature)

    @classmethod
    def validate_token(cls, token: Any) -> bool:
        """Validate if a given token is an authentic ServerBoundaryToken issued by this authority."""
        if not isinstance(token, ServerBoundaryToken):
            return False
        expected = hmac.new(
            cls.get_secret().encode(),
            f"{token.issuer}:{token.token_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(token._secret_signature, expected)

    @classmethod
    def _compute_semantic_metadata_hash(cls, metadata: Mapping[str, Any] | None) -> str:
        """Compute deterministic SHA-256 hash of unreserved domain metadata."""
        if not metadata:
            return ""
        filtered = {
            str(k): v
            for k, v in metadata.items()
            if k not in RESERVED_USER_MODEL_METADATA_KEYS
        }
        if not filtered:
            return ""
        raw_json = json.dumps(
            filtered,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

    @classmethod
    def _compute_canonical_payload_bytes_v2(
        cls,
        *,
        version: str,
        issuer: str,
        token_id: str,
        memory_id: str,
        logical_key: str,
        content_sha256: str,
        category: str,
        project_id: str,
        scope: str,
        subject: str,
        memory_type: str,
        user_id: str,
        source_type: str,
        confidence: str,
        importance: str,
        valid_from: str,
        valid_until: str,
        semantic_metadata_hash: str,
    ) -> bytes:
        payload_dict = {
            "cat": category,
            "conf": confidence,
            "csha": content_sha256,
            "imp": importance,
            "iss": issuer,
            "lk": logical_key,
            "mhash": semantic_metadata_hash,
            "mid": memory_id,
            "mt": memory_type,
            "pid": project_id,
            "sbj": subject,
            "scp": scope,
            "st": source_type,
            "tid": token_id,
            "uid": user_id,
            "v": version,
            "vf": valid_from,
            "vu": valid_until,
        }
        return json.dumps(
            payload_dict,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def create_attestation(
        cls,
        token: ServerBoundaryToken,
        *,
        logical_key: str,
        content: str,
        project_id: str | None,
        memory_id: str | None = None,
        scope: str | None = None,
        subject: str = "user",
        memory_type: str = "preference",
        category: PreferenceCategory | str = PreferenceCategory.UNCLASSIFIED,
        source_type: PreferenceSourceType | str = PreferenceSourceType.EXPLICIT_USER,
        confidence: float = 1.0,
        importance: float = 0.5,
        user_id: str = DEFAULT_USER_ID,
        valid_from: datetime | str | None = None,
        valid_until: datetime | str | None = None,
        semantic_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a cryptographically signed V2 provenance attestation bound to this specific preference."""
        content_sha256 = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
        pid_str = project_id if project_id is not None else "__global__"
        scope_str = scope or ("project" if project_id is not None else "global")
        st_norm = normalize_preference_source_type(source_type).value
        cat_norm = normalize_preference_category(category).value
        mid_str = str(memory_id).strip() if memory_id else f"pref_{uuid.uuid4().hex[:12]}"
        lk_str = str(logical_key).strip()
        uid_str = str(user_id).strip() or DEFAULT_USER_ID
        sbj_str = str(subject).strip() or "user"
        mt_str = memory_type.value if hasattr(memory_type, "value") else str(memory_type).strip()
        conf_str = f"{float(confidence):.4f}"
        imp_str = f"{float(importance):.4f}"
        vf_str = valid_from.isoformat() if isinstance(valid_from, datetime) else (str(valid_from).strip() if valid_from else "")
        vu_str = valid_until.isoformat() if isinstance(valid_until, datetime) else (str(valid_until).strip() if valid_until else "")
        meta_hash = cls._compute_semantic_metadata_hash(semantic_metadata)

        raw_bytes = cls._compute_canonical_payload_bytes_v2(
            version="2.0",
            issuer=token.issuer,
            token_id=token.token_id,
            memory_id=mid_str,
            logical_key=lk_str,
            content_sha256=content_sha256,
            category=cat_norm,
            project_id=pid_str,
            scope=scope_str,
            subject=sbj_str,
            memory_type=mt_str,
            user_id=uid_str,
            source_type=st_norm,
            confidence=conf_str,
            importance=imp_str,
            valid_from=vf_str,
            valid_until=vu_str,
            semantic_metadata_hash=meta_hash,
        )
        signature = hmac.new(
            cls.get_secret().encode("utf-8"),
            raw_bytes,
            hashlib.sha256,
        ).hexdigest()

        return {
            "version": "2.0",
            "issuer": token.issuer,
            "token_id": token.token_id,
            "memory_id": mid_str,
            "logical_key": lk_str,
            "content_sha256": content_sha256,
            "category": cat_norm,
            "project_id": pid_str,
            "scope": scope_str,
            "subject": sbj_str,
            "memory_type": mt_str,
            "user_id": uid_str,
            "source_type": st_norm,
            "confidence": conf_str,
            "importance": imp_str,
            "valid_from": vf_str or None,
            "valid_until": vu_str or None,
            "semantic_metadata_hash": meta_hash,
            "signature": signature,
        }

    @classmethod
    def verify_record_attestation(cls, record: MemoryRecord) -> bool:
        """Verify the cryptographic provenance attestation attached to a MemoryRecord.

        Security & Invariants:
        1. Only version '2.0' attestations are accepted. Legacy '1.0' attestations fail-closed
           because they omit critical field bindings (category, timestamps, confidence, memory_id).
        2. Binds immutable declaration: memory_id, logical_key, content, category, project_id,
           scope, subject, memory_type, user_id, source_type, confidence, importance, valid_from,
           valid_until, and unreserved metadata hash.
        3. Lifecycle mutations (status: ACTIVE/SUPERSEDED/ARCHIVED, supersedes, updated_at,
           last_accessed_at) are operational transitions and do not invalidate the attestation.
        4. Comparison uses constant-time hmac.compare_digest.
        """
        raw_attestation = record.metadata.get(PROVENANCE_ATTESTATION_KEY)
        if not isinstance(raw_attestation, dict):
            return False

        version = raw_attestation.get("version")
        if version != "2.0":
            # Fail-closed: V1 is insecure and cannot be treated as VERIFIED_EXPLICIT
            return False

        sig = raw_attestation.get("signature")
        if not isinstance(sig, str) or not sig:
            return False

        issuer = raw_attestation.get("issuer")
        token_id = raw_attestation.get("token_id")
        if not issuer or not token_id:
            return False

        # 1. memory_id binding check
        att_mid = raw_attestation.get("memory_id")
        if not att_mid or record.memory_id != att_mid:
            return False

        # 2. logical_key binding check
        rec_lk = record.metadata.get("logical_key")
        att_lk = raw_attestation.get("logical_key")
        if not rec_lk or rec_lk != att_lk:
            return False

        # 3. content binding check
        expected_content_hash = hashlib.sha256(record.content.strip().encode("utf-8")).hexdigest()
        att_csha = raw_attestation.get("content_sha256")
        if expected_content_hash != att_csha:
            return False

        # 4. category binding check
        rec_cat = normalize_preference_category(record.metadata.get("category", PreferenceCategory.UNCLASSIFIED)).value
        att_cat = raw_attestation.get("category")
        if rec_cat != att_cat:
            return False

        # 5. project_id & scope binding check
        expected_pid = record.project_id if record.project_id is not None else "__global__"
        att_pid = raw_attestation.get("project_id")
        if expected_pid != att_pid:
            return False

        if record.scope != raw_attestation.get("scope"):
            return False

        # 6. subject & memory_type binding check
        rec_sbj = str(record.subject).strip() if record.subject else "user"
        if rec_sbj != raw_attestation.get("subject"):
            return False

        rec_mt = normalize_memory_type(record.memory_type).value
        if rec_mt != raw_attestation.get("memory_type"):
            return False

        # 7. user_id & source_type binding check
        rec_uid = str(record.metadata.get("user_id", DEFAULT_USER_ID)).strip() or DEFAULT_USER_ID
        if rec_uid != raw_attestation.get("user_id"):
            return False

        rec_st = normalize_preference_source_type(record.source).value
        if rec_st != raw_attestation.get("source_type"):
            return False

        # 8. confidence & importance binding check
        rec_conf = f"{float(record.confidence):.4f}"
        if rec_conf != raw_attestation.get("confidence"):
            return False

        rec_imp = f"{float(record.importance):.4f}"
        if rec_imp != raw_attestation.get("importance"):
            return False

        # 9. valid_from & valid_until binding check
        rec_vf = record.valid_from.isoformat() if record.valid_from else ""
        att_vf = raw_attestation.get("valid_from") or ""
        if rec_vf != att_vf:
            return False

        rec_vu = record.valid_until.isoformat() if record.valid_until else ""
        att_vu = raw_attestation.get("valid_until") or ""
        if rec_vu != att_vu:
            return False

        # 10. unreserved metadata hash check
        rec_meta_hash = cls._compute_semantic_metadata_hash(record.metadata)
        att_meta_hash = raw_attestation.get("semantic_metadata_hash") or ""
        if rec_meta_hash != att_meta_hash:
            return False

        # 11. Reconstruct canonical bytes and verify HMAC signature
        raw_bytes = cls._compute_canonical_payload_bytes_v2(
            version="2.0",
            issuer=issuer,
            token_id=token_id,
            memory_id=att_mid,
            logical_key=att_lk,
            content_sha256=att_csha,
            category=att_cat,
            project_id=att_pid,
            scope=raw_attestation.get("scope", ""),
            subject=raw_attestation.get("subject", ""),
            memory_type=raw_attestation.get("memory_type", ""),
            user_id=raw_attestation.get("user_id", ""),
            source_type=raw_attestation.get("source_type", ""),
            confidence=raw_attestation.get("confidence", ""),
            importance=raw_attestation.get("importance", ""),
            valid_from=att_vf,
            valid_until=att_vu,
            semantic_metadata_hash=att_meta_hash,
        )
        expected_sig = hmac.new(
            cls.get_secret().encode("utf-8"),
            raw_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(sig, expected_sig)


# ---------------------------------------------------------------------------
# Specifications & Domain Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreferenceSpec:
    """Input specification for recording or declaring a user preference."""

    logical_key: str
    content: str
    category: PreferenceCategory = PreferenceCategory.TECHNICAL
    project_id: str | None = None
    scope: str | None = None
    source_type: PreferenceSourceType = PreferenceSourceType.EXPLICIT_USER
    confidence: float = 1.0
    importance: float = 0.5
    user_id: str = DEFAULT_USER_ID
    inferred_evidence: str | None = None
    trusted_provenance: bool = True
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.logical_key or not str(self.logical_key).strip():
            raise ValueError("logical_key must not be empty")
        if not self.content or not str(self.content).strip():
            raise ValueError("content must not be empty")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {self.confidence}")
        if not (0.0 <= self.importance <= 1.0):
            raise ValueError(f"importance must be between 0.0 and 1.0, got {self.importance}")

        # Check for reserved metadata keys
        for k in self.metadata:
            if k in RESERVED_USER_MODEL_METADATA_KEYS:
                raise ValueError(
                    f"metadata key '{k}' is reserved for UserModel packaging and cannot be supplied by caller"
                )

        # Inferred evidence validation
        if self.source_type == PreferenceSourceType.INFERRED and not self.inferred_evidence:
            raise ValueError("inferred_evidence is required when source_type is INFERRED")


@dataclass(frozen=True, slots=True)
class PreferenceItem:
    """Domain model projection of a canonical user preference memory record.

    Attributes:
    ----------
    preference_id:      Stable identifier matching underlying MemoryRecord.memory_id.
    logical_key:        Semantic identifier for conflict identity (e.g. 'code_style:test_framework').
    content:            Textual statement or configuration payload of the preference.
    category:           Domain category (COMMUNICATION, TECHNICAL, WORKFLOW, CONSTRAINT, UNCLASSIFIED).
    scope:              Logical scope boundary ('global' or 'project').
    project_id:         Associated project ID (None for global preferences).
    source_type:        Provenance classification (EXPLICIT_USER, INFERRED, SYSTEM_DEFAULT).
    provenance_status:  Verification trust state (VERIFIED_EXPLICIT, UNVERIFIED_CLAIM, INFERRED_HYPOTHESIS, etc.).
    confidence:         Confidence score in [0.0, 1.0].
    importance:         Importance score in [0.0, 1.0].
    status:             Lifecycle status (ACTIVE, SUPERSEDED, ARCHIVED).
    tier:               Storage tier (L3 for global, L2 for project).
    user_id:            Contextual attribution tag (not tenant auth boundary).
    supersedes:         ID of an older preference record replaced by this one.
    inferred_evidence:  Context/reasoning explaining why preference was inferred.
    valid_from:         Optional ISO-8601 temporal start validity.
    valid_until:        Optional ISO-8601 temporal expiration validity.
    metadata:           Unreserved domain metadata (shallow-immutable mapping).
    created_at:         Creation timestamp with UTC timezone.
    updated_at:         Optional last update timestamp with UTC timezone.
    last_accessed_at:   Optional last access timestamp with UTC timezone.
    """

    preference_id: str
    logical_key: str
    content: str
    category: PreferenceCategory
    scope: str
    project_id: str | None
    source_type: PreferenceSourceType
    provenance_status: ProvenanceStatus
    confidence: float
    importance: float
    status: MemoryStatus
    tier: MemoryLevel
    user_id: str = DEFAULT_USER_ID
    supersedes: str | None = None
    inferred_evidence: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    last_accessed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.preference_id or not str(self.preference_id).strip():
            raise ValueError("preference_id must not be empty")
        if not self.logical_key or not str(self.logical_key).strip():
            raise ValueError("logical_key must not be empty")
        if not self.content or not str(self.content).strip():
            raise ValueError("content must not be empty")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {self.confidence}")
        if not (0.0 <= self.importance <= 1.0):
            raise ValueError(f"importance must be between 0.0 and 1.0, got {self.importance}")

        # Scope vs project_id consistency invariant matching MemoryRecord
        if self.project_id is None and self.scope != "global":
            object.__setattr__(self, "scope", "global")
        elif self.project_id is not None and self.scope != "project":
            object.__setattr__(self, "scope", "project")

        # Tier consistency invariant: global -> L3, project -> L2
        if self.project_id is None and self.tier != MemoryLevel.L3:
            object.__setattr__(self, "tier", MemoryLevel.L3)
        elif self.project_id is not None and self.tier != MemoryLevel.L2:
            object.__setattr__(self, "tier", MemoryLevel.L2)

        # Check for reserved metadata keys
        for k in self.metadata:
            if k in RESERVED_USER_MODEL_METADATA_KEYS:
                raise ValueError(
                    f"metadata key '{k}' is reserved for UserModel packaging and cannot be supplied in metadata"
                )

        # Enforce read-only mapping proxy for shallow-immutability of metadata
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", types.MappingProxyType(dict(self.metadata)))

    @property
    def is_active(self) -> bool:
        """Returns True if the preference is currently ACTIVE."""
        return self.status == MemoryStatus.ACTIVE

    @property
    def is_global(self) -> bool:
        """Returns True if this is a global cross-project preference."""
        return self.project_id is None or self.scope == "global"

    @property
    def is_explicit(self) -> bool:
        """Returns True if directly declared by the user (regardless of trust verification)."""
        return self.source_type == PreferenceSourceType.EXPLICIT_USER

    @property
    def is_verified(self) -> bool:
        """Returns True if proven to originate from a verified explicit user boundary."""
        return self.provenance_status == ProvenanceStatus.VERIFIED_EXPLICIT


# ---------------------------------------------------------------------------
# Precedence Ranking & Effective Profile
# ---------------------------------------------------------------------------


def calculate_precedence_rank(item: PreferenceItem) -> int:
    """Computes deterministic precedence rank for a preference item.

    Precedence hierarchy (highest to lowest):
        Rank 5: PROJECT + VERIFIED_EXPLICIT
        Rank 4: GLOBAL  + VERIFIED_EXPLICIT
        Rank 3: PROJECT + INFERRED_HYPOTHESIS
        Rank 2: GLOBAL  + INFERRED_HYPOTHESIS
        Rank 1: UNVERIFIED_CLAIM (Forged or unverified claims degraded here)
        Rank 0: SYSTEM_BASELINE / LEGACY_UNCLASSIFIED
    """
    if item.provenance_status in (ProvenanceStatus.SYSTEM_BASELINE, ProvenanceStatus.LEGACY_UNCLASSIFIED):
        return 0

    if item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM:
        # Untrusted claims cannot override verified or valid project inferred preferences
        return 1

    if item.provenance_status == ProvenanceStatus.INFERRED_HYPOTHESIS or item.source_type == PreferenceSourceType.INFERRED:
        return 3 if not item.is_global else 2

    if item.provenance_status == ProvenanceStatus.VERIFIED_EXPLICIT or item.source_type == PreferenceSourceType.EXPLICIT_USER:
        return 5 if not item.is_global else 4

    return 0


@dataclass(frozen=True, slots=True)
class EffectiveUserProfile:
    """Aggregated, conflict-resolved user profile for a target execution context.

    Resolves active preferences across global and project scopes using the
    deterministic Precedence Hierarchy.
    """

    user_id: str
    project_id: str | None
    preferences_by_key: Mapping[str, PreferenceItem]
    resolution_reasons: Mapping[str, str] = field(default_factory=dict)

    def get(self, logical_key: str, default: str | None = None) -> str | None:
        """Get the textual preference content for a logical_key."""
        item = self.preferences_by_key.get(logical_key)
        return item.content if item else default

    def get_item(self, logical_key: str) -> PreferenceItem | None:
        """Get the full PreferenceItem for a logical_key."""
        return self.preferences_by_key.get(logical_key)

    def get_reason(self, logical_key: str) -> str | None:
        """Get the deterministic resolution reason explaining why this preference was selected."""
        return self.resolution_reasons.get(logical_key)

    def has(self, logical_key: str) -> bool:
        """Check if a preference exists for a logical_key."""
        return logical_key in self.preferences_by_key

    def get_by_category(self, category: PreferenceCategory) -> tuple[PreferenceItem, ...]:
        """Get all resolved preferences belonging to a category."""
        return tuple(
            item for item in self.preferences_by_key.values()
            if item.category == category
        )

    def active_constraints(self) -> tuple[PreferenceItem, ...]:
        """Convenience method returning all active CONSTRAINT preferences."""
        return self.get_by_category(PreferenceCategory.CONSTRAINT)

    @property
    def active_preferences(self) -> tuple[PreferenceItem, ...]:
        """All active, conflict-resolved preferences."""
        return tuple(self.preferences_by_key.values())

    @property
    def categorized_preferences(self) -> dict[str, list[PreferenceItem]]:
        """Categorized mapping of active preferences."""
        result: dict[str, list[PreferenceItem]] = {}
        for item in self.preferences_by_key.values():
            cat = item.category.value if isinstance(item.category, PreferenceCategory) else str(item.category)
            result.setdefault(cat, []).append(item)
        return result


def build_effective_profile(
    items: Sequence[PreferenceItem],
    *,
    user_id: str = DEFAULT_USER_ID,
    project_id: str | None = None,
    as_of: datetime | None = None,
    exclude_unverified: bool = False,
) -> EffectiveUserProfile:
    """Constructs an EffectiveUserProfile by resolving active preferences.

    Invariants enforced:
    1. Only ACTIVE preferences are considered (ARCHIVED and SUPERSEDED are excluded).
    2. Preferences matching the target project_id or global preferences (project_id=None) are considered.
    3. Preferences for a different specific project_id are excluded.
    4. Temporal validity: valid_from <= as_of <= valid_until (when as_of is provided).
    5. Unverified claims exclusion: excluded if exclude_unverified is True.
    6. Conflicting items with the same logical_key are resolved using calculate_precedence_rank.
    7. Ties within the same rank are broken by newer created_at timestamp, then stable preference_id.
    8. Deterministic resolution reasons are recorded for every selected preference.
    """
    candidates: list[PreferenceItem] = []
    for item in items:
        # Invariant 1: Status must be ACTIVE
        if item.status != MemoryStatus.ACTIVE:
            continue
        # Invariant 2 & 3: Project match or global
        if item.project_id is not None and project_id is not None and item.project_id != project_id:
            continue
        if item.project_id is not None and project_id is None:
            # When viewing purely global profile, exclude project-specific preferences
            continue
        # Invariant 4: Temporal validity
        if as_of is not None:
            if item.valid_from is not None and as_of < item.valid_from:
                continue
            if item.valid_until is not None and as_of > item.valid_until:
                continue
        # Invariant 5: Unverified claims exclusion
        if exclude_unverified and item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM:
            continue
        candidates.append(item)

    # Group by logical_key and resolve conflicts
    resolved_by_key: dict[str, PreferenceItem] = {}
    reasons_by_key: dict[str, str] = {}
    for candidate in candidates:
        lk = candidate.logical_key
        candidate_rank = calculate_precedence_rank(candidate)
        candidate_scope_prov = f"{candidate.scope}_{candidate.provenance_status.value}"

        if lk not in resolved_by_key:
            resolved_by_key[lk] = candidate
            reasons_by_key[lk] = f"rank:{candidate_rank} ({candidate_scope_prov})"
            continue

        existing = resolved_by_key[lk]
        existing_rank = calculate_precedence_rank(existing)
        existing_scope_prov = f"{existing.scope}_{existing.provenance_status.value}"

        if candidate_rank > existing_rank:
            resolved_by_key[lk] = candidate
            reasons_by_key[lk] = (
                f"rank:{candidate_rank} ({candidate_scope_prov}) "
                f"over rank:{existing_rank} ({existing_scope_prov})"
            )
        elif candidate_rank == existing_rank:
            # Tie breaker: newer created_at wins, then stable preference_id
            if candidate.created_at > existing.created_at:
                resolved_by_key[lk] = candidate
                reasons_by_key[lk] = f"rank:{candidate_rank} tie_breaker:newer_timestamp"
            elif candidate.created_at == existing.created_at:
                if candidate.preference_id >= existing.preference_id:
                    resolved_by_key[lk] = candidate
                    reasons_by_key[lk] = f"rank:{candidate_rank} tie_breaker:stable_id"

    return EffectiveUserProfile(
        user_id=user_id,
        project_id=project_id,
        preferences_by_key=resolved_by_key,
        resolution_reasons=reasons_by_key,
    )


# ---------------------------------------------------------------------------
# Converters: PreferenceItem <-> MemoryRecord
# ---------------------------------------------------------------------------


def preference_item_to_memory_record(item: PreferenceItem) -> MemoryRecord:
    """Converts a typed PreferenceItem into a canonical Phase 03 MemoryRecord.

    Preserves 100% of domain metadata and temporal properties.
    """
    resolved_scope = "global" if item.project_id is None else "project"
    resolved_tier = MemoryLevel.L3 if item.project_id is None else MemoryLevel.L2

    # Package UserModel domain metadata
    packed_meta: dict[str, Any] = dict(item.metadata)
    packed_meta["logical_key"] = item.logical_key
    packed_meta["category"] = item.category.value
    packed_meta["source_type"] = item.source_type.value
    packed_meta["provenance_status"] = item.provenance_status.value
    packed_meta["trusted_provenance"] = (item.provenance_status == ProvenanceStatus.VERIFIED_EXPLICIT)
    packed_meta["user_id"] = item.user_id
    if item.inferred_evidence is not None:
        packed_meta["inferred_evidence"] = item.inferred_evidence

    return MemoryRecord(
        memory_id=item.preference_id,
        project_id=item.project_id,
        content=item.content,
        importance=item.importance,
        metadata=packed_meta,
        created_at=item.created_at,
        memory_type=MemoryType.PREFERENCE,
        tier=resolved_tier,
        confidence=item.confidence,
        subject="user",
        scope=resolved_scope,
        source=item.source_type.value,
        status=item.status,
        supersedes=item.supersedes,
        updated_at=item.updated_at,
        last_accessed_at=item.last_accessed_at,
        valid_from=item.valid_from,
        valid_until=item.valid_until,
    )


def memory_record_to_preference_item(
    record: MemoryRecord,
    *,
    require_attestation: bool = False,
) -> PreferenceItem:
    """Reconstructs a typed PreferenceItem from a canonical MemoryRecord.

    Strict provenance verification & graceful legacy handling:
    - Verifies cryptographic provenance attestation via TrustedProvenanceAuthority.
    - When require_attestation=True, any record lacking authentic server attestation
      is degraded to UNVERIFIED_CLAIM (cannot be VERIFIED_EXPLICIT).
    - Missing category defaults honestly to UNCLASSIFIED (not assumed TECHNICAL).
    - Missing logical_key defaults to "unkeyed:{memory_id}".
    - Preserves all temporal boundaries (valid_from, valid_until).
    """
    raw_meta = dict(record.metadata)

    # 1. Logical key resolution
    raw_lk = raw_meta.pop("logical_key", None)
    if raw_lk and str(raw_lk).strip():
        logical_key = str(raw_lk).strip()
    else:
        logical_key = f"unkeyed:{record.memory_id}"

    # 2. Category resolution: honest fallback to UNCLASSIFIED if not specified
    raw_cat = raw_meta.pop("category", None)
    if raw_cat:
        category = normalize_preference_category(raw_cat)
    else:
        category = PreferenceCategory.UNCLASSIFIED

    # 3. Source type & Provenance status resolution
    raw_st = raw_meta.pop("source_type", None) or record.source
    has_trusted_token = bool(raw_meta.pop("trusted_provenance", False))
    raw_prov = raw_meta.pop("provenance_status", None)

    # Cryptographic attestation verification
    has_valid_attestation = TrustedProvenanceAuthority.verify_record_attestation(record)

    if has_valid_attestation:
        source_type = PreferenceSourceType.EXPLICIT_USER
        provenance_status = ProvenanceStatus.VERIFIED_EXPLICIT
    elif require_attestation:
        # Strict server boundary mode: claims lacking valid cryptographic attestation
        # cannot be VERIFIED_EXPLICIT under any circumstances.
        raw_st_norm = str(raw_st).strip().lower()
        if raw_st_norm in ("inferred", "learned", "observed", "heuristic") or raw_prov in ("inferred_hypothesis", "inferred"):
            source_type = PreferenceSourceType.INFERRED
            provenance_status = ProvenanceStatus.INFERRED_HYPOTHESIS
        elif raw_prov:
            source_type = normalize_preference_source_type(raw_st)
            norm_prov = normalize_provenance_status(raw_prov)
            provenance_status = ProvenanceStatus.UNVERIFIED_CLAIM if norm_prov == ProvenanceStatus.VERIFIED_EXPLICIT else norm_prov
        elif has_trusted_token or raw_st_norm in ("explicit_user", "user", "manual", "direct"):
            source_type = PreferenceSourceType.EXPLICIT_USER
            provenance_status = ProvenanceStatus.UNVERIFIED_CLAIM
        else:
            source_type = PreferenceSourceType.SYSTEM_DEFAULT
            provenance_status = ProvenanceStatus.LEGACY_UNCLASSIFIED
    elif raw_prov:
        provenance_status = normalize_provenance_status(raw_prov)
        source_type = normalize_preference_source_type(raw_st)
    elif has_trusted_token:
        # Legacy fallback mode without strict attestation
        if raw_st in ("runtime", None, ""):
            source_type = PreferenceSourceType.EXPLICIT_USER
        else:
            source_type = normalize_preference_source_type(raw_st)
        if source_type == PreferenceSourceType.EXPLICIT_USER:
            provenance_status = ProvenanceStatus.VERIFIED_EXPLICIT
        elif source_type == PreferenceSourceType.INFERRED:
            provenance_status = ProvenanceStatus.INFERRED_HYPOTHESIS
        else:
            provenance_status = ProvenanceStatus.SYSTEM_BASELINE
    else:
        # Untrusted or legacy records: distinguish claimed label from verified provenance
        raw_st_norm = str(raw_st).strip().lower()
        if raw_st_norm in ("explicit_user", "user", "manual", "direct"):
            # Unauthenticated external claim -> degraded to UNVERIFIED_CLAIM
            source_type = PreferenceSourceType.EXPLICIT_USER
            provenance_status = ProvenanceStatus.UNVERIFIED_CLAIM
        elif raw_st_norm in ("inferred", "learned", "observed", "heuristic"):
            source_type = PreferenceSourceType.INFERRED
            provenance_status = ProvenanceStatus.INFERRED_HYPOTHESIS
        else:
            # Historical runtime or default records
            source_type = PreferenceSourceType.SYSTEM_DEFAULT
            provenance_status = ProvenanceStatus.LEGACY_UNCLASSIFIED

    # 4. User ID attribution tag
    user_id = str(raw_meta.pop("user_id", DEFAULT_USER_ID)).strip() or DEFAULT_USER_ID

    # 5. Inferred evidence
    inferred_evidence = raw_meta.pop("inferred_evidence", None)
    if inferred_evidence is not None:
        inferred_evidence = str(inferred_evidence)

    # Clean out internal __canonical__ and attestation if present
    raw_meta.pop("__canonical__", None)
    raw_meta.pop(PROVENANCE_ATTESTATION_KEY, None)

    # Scope and tier consistency
    resolved_scope = "global" if record.project_id is None else "project"
    resolved_tier = MemoryLevel.L3 if record.project_id is None else MemoryLevel.L2

    return PreferenceItem(
        preference_id=record.memory_id,
        logical_key=logical_key,
        content=record.content,
        category=category,
        scope=resolved_scope,
        project_id=record.project_id,
        source_type=source_type,
        provenance_status=provenance_status,
        confidence=record.confidence,
        importance=record.importance,
        status=record.status,
        tier=resolved_tier,
        user_id=user_id,
        supersedes=record.supersedes,
        inferred_evidence=inferred_evidence,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
        metadata=raw_meta,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_accessed_at=record.last_accessed_at,
    )


def preference_spec_to_memory_record(
    spec: PreferenceSpec,
    *,
    preference_id: str | None = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    supersedes: str | None = None,
    attestation: Mapping[str, Any] | None = None,
) -> MemoryRecord:
    """Constructs a canonical MemoryRecord directly from a PreferenceSpec."""
    pid = preference_id or (attestation.get("memory_id") if attestation else None) or f"pref_{uuid.uuid4().hex[:12]}"
    resolved_scope = "global" if spec.project_id is None else "project"
    resolved_tier = MemoryLevel.L3 if spec.project_id is None else MemoryLevel.L2

    prov_status = (
        ProvenanceStatus.VERIFIED_EXPLICIT
        if spec.source_type == PreferenceSourceType.EXPLICIT_USER and spec.trusted_provenance
        else (
            ProvenanceStatus.INFERRED_HYPOTHESIS
            if spec.source_type == PreferenceSourceType.INFERRED
            else ProvenanceStatus.SYSTEM_BASELINE
        )
    )

    packed_meta: dict[str, Any] = dict(spec.metadata)
    packed_meta["logical_key"] = spec.logical_key
    packed_meta["category"] = spec.category.value
    packed_meta["source_type"] = spec.source_type.value
    packed_meta["provenance_status"] = prov_status.value
    packed_meta["trusted_provenance"] = (prov_status == ProvenanceStatus.VERIFIED_EXPLICIT)
    packed_meta["user_id"] = spec.user_id
    if spec.inferred_evidence is not None:
        packed_meta["inferred_evidence"] = spec.inferred_evidence
    if attestation is not None:
        packed_meta[PROVENANCE_ATTESTATION_KEY] = dict(attestation)

    return MemoryRecord(
        memory_id=pid,
        project_id=spec.project_id,
        content=spec.content,
        importance=spec.importance,
        metadata=packed_meta,
        created_at=datetime.now(UTC),
        memory_type=MemoryType.PREFERENCE,
        tier=resolved_tier,
        confidence=spec.confidence,
        subject="user",
        scope=resolved_scope,
        source=spec.source_type.value,
        status=status,
        supersedes=supersedes,
        valid_from=spec.valid_from,
        valid_until=spec.valid_until,
    )
