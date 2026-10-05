from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Protocol


class DecisionSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# Default TTL per severity.  HIGH decisions never auto-expire.
_DEFAULT_TTL: dict[DecisionSeverity, timedelta | None] = {
    DecisionSeverity.LOW: timedelta(hours=24),
    DecisionSeverity.MEDIUM: timedelta(hours=72),
    DecisionSeverity.HIGH: None,  # Never auto-expire — requires explicit cancel
}


@dataclass(frozen=True, slots=True)
class DecisionOption:
    option_id: str
    title: str
    description: str
    trade_offs: str = ""
    impact: str = ""
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "title": self.title,
            "description": self.description,
            "trade_offs": self.trade_offs,
            "impact": self.impact,
            "recommended": self.recommended,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionOption:
        return cls(
            option_id=data["option_id"],
            title=data["title"],
            description=data["description"],
            trade_offs=data.get("trade_offs", ""),
            impact=data.get("impact", ""),
            recommended=data.get("recommended", False),
        )


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    task_id: str
    step_id: str | None
    severity: DecisionSeverity
    state: DecisionState
    prompt: str
    options: tuple[DecisionOption, ...] = field(default_factory=tuple)
    selected_option_id: str | None = None
    rationale: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    resolved_at: str | None = None
    # Extended context fields (aligned with AGENTS.md DecisionRecord spec)
    plan_id: str | None = None
    session_id: str | None = None
    # Expiry: ISO-8601 UTC timestamp; None means the decision never auto-expires.
    expires_at: str | None = None

    def is_open(self) -> bool:
        return self.state == DecisionState.OPEN

    def is_expired(self) -> bool:
        """Returns True if the decision has passed its expiry deadline."""
        if self.expires_at is None:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) >= expiry
        except ValueError:
            return False

    def get_option(self, option_id: str) -> DecisionOption | None:
        for opt in self.options:
            if opt.option_id == option_id:
                return opt
        return None

    def resolve(self, selected_option_id: str, rationale: str | None = None) -> DecisionRecord:
        if self.state != DecisionState.OPEN:
            raise ValueError(f"cannot resolve decision in non-open state: {self.state.value}")
        if not self.get_option(selected_option_id):
            raise ValueError(f"invalid option_id '{selected_option_id}' for decision {self.decision_id}")
        return DecisionRecord(
            decision_id=self.decision_id,
            task_id=self.task_id,
            step_id=self.step_id,
            severity=self.severity,
            state=DecisionState.RESOLVED,
            prompt=self.prompt,
            options=self.options,
            selected_option_id=selected_option_id,
            rationale=rationale,
            created_at=self.created_at,
            resolved_at=datetime.now(timezone.utc).isoformat(),
            plan_id=self.plan_id,
            session_id=self.session_id,
            expires_at=self.expires_at,
        )

    def cancel(self, rationale: str | None = None) -> DecisionRecord:
        if self.state != DecisionState.OPEN:
            raise ValueError(f"cannot cancel decision in non-open state: {self.state.value}")
        return DecisionRecord(
            decision_id=self.decision_id,
            task_id=self.task_id,
            step_id=self.step_id,
            severity=self.severity,
            state=DecisionState.CANCELLED,
            prompt=self.prompt,
            options=self.options,
            selected_option_id=None,
            rationale=rationale,
            created_at=self.created_at,
            resolved_at=datetime.now(timezone.utc).isoformat(),
            plan_id=self.plan_id,
            session_id=self.session_id,
            expires_at=self.expires_at,
        )

    def expire(self) -> DecisionRecord:
        """Transitions the decision to EXPIRED state (only valid from OPEN)."""
        if self.state != DecisionState.OPEN:
            raise ValueError(f"cannot expire decision in non-open state: {self.state.value}")
        return DecisionRecord(
            decision_id=self.decision_id,
            task_id=self.task_id,
            step_id=self.step_id,
            severity=self.severity,
            state=DecisionState.EXPIRED,
            prompt=self.prompt,
            options=self.options,
            selected_option_id=None,
            rationale="Auto-expired due to TTL",
            created_at=self.created_at,
            resolved_at=datetime.now(timezone.utc).isoformat(),
            plan_id=self.plan_id,
            session_id=self.session_id,
            expires_at=self.expires_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "severity": self.severity.value,
            "state": self.state.value,
            "prompt": self.prompt,
            "options": [opt.to_dict() for opt in self.options],
            "selected_option_id": self.selected_option_id,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        return cls(
            decision_id=data["decision_id"],
            task_id=data["task_id"],
            step_id=data.get("step_id"),
            severity=DecisionSeverity(data["severity"]),
            state=DecisionState(data["state"]),
            prompt=data["prompt"],
            options=tuple(DecisionOption.from_dict(opt) for opt in data.get("options", ())),
            selected_option_id=data.get("selected_option_id"),
            rationale=data.get("rationale"),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            resolved_at=data.get("resolved_at"),
            plan_id=data.get("plan_id"),
            session_id=data.get("session_id"),
            expires_at=data.get("expires_at"),
        )


class DecisionPolicy:
    """Deterministic policy enforcing decision rules and severity constraints."""

    @classmethod
    def validate_creation(
        cls,
        *,
        severity: DecisionSeverity,
        options: tuple[DecisionOption, ...] | list[DecisionOption],
    ) -> None:
        if len(options) < 2:
            raise ValueError("a decision must have at least 2 distinct options")
        option_ids = [opt.option_id for opt in options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option IDs within a decision must be unique")

    @classmethod
    def enforce_minimum_severity(
        cls,
        base_severity: DecisionSeverity,
        proposed_severity: DecisionSeverity,
    ) -> DecisionSeverity:
        """Ensures that model/AI cannot unilaterally downgrade decision severity."""
        rank = {
            DecisionSeverity.LOW: 1,
            DecisionSeverity.MEDIUM: 2,
            DecisionSeverity.HIGH: 3,
        }
        if rank[proposed_severity] < rank[base_severity]:
            return base_severity
        return proposed_severity

    @classmethod
    def compute_expires_at(cls, severity: DecisionSeverity) -> str | None:
        """Returns ISO-8601 expiry timestamp for a new decision, or None if it never expires."""
        ttl = _DEFAULT_TTL.get(severity)
        if ttl is None:
            return None
        return (datetime.now(timezone.utc) + ttl).isoformat()


class DecisionStore(Protocol):
    async def save(self, record: DecisionRecord) -> None: ...
    async def get(self, decision_id: str) -> DecisionRecord | None: ...
    async def list_by_task(self, task_id: str) -> tuple[DecisionRecord, ...]: ...
    async def list_open(self, task_id: str | None = None) -> tuple[DecisionRecord, ...]: ...
    async def list_expired_open(self) -> tuple[DecisionRecord, ...]: ...
