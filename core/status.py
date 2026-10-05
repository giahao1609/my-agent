from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Availability(StrEnum):
    AVAILABLE = 'available'
    READY = 'ready'  # backward-compatible alias
    DEGRADED = 'degraded'
    MOCKED = 'mocked'
    UNAVAILABLE = 'unavailable'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    name: str
    state: Availability
    reason: str | None = None
    implementation: str = ""
    provider_or_backend: str = ""
    last_verified_at: str | None = None
    verification_method: str = ""
    evidence: str = ""
    failure_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError('capability name must not be empty')

        eff_reason = self.failure_reason or self.reason
        if self.state not in (Availability.READY, Availability.AVAILABLE) and not eff_reason:
            raise ValueError('reason is required when capability is not ready')

        if eff_reason and not self.reason:
            object.__setattr__(self, 'reason', eff_reason)
        if eff_reason and not self.failure_reason:
            object.__setattr__(self, 'failure_reason', eff_reason)

    @property
    def status(self) -> Availability:
        return self.state

    @property
    def available(self) -> bool:
        """True if and only if capability is real, operational, and verified ready."""
        return self.state in (Availability.READY, Availability.AVAILABLE)

    @property
    def is_available(self) -> bool:
        return self.available

    @property
    def is_mocked(self) -> bool:
        return self.state is Availability.MOCKED

    @property
    def is_degraded(self) -> bool:
        return self.state is Availability.DEGRADED

    @property
    def is_unavailable(self) -> bool:
        return self.state is Availability.UNAVAILABLE

    @property
    def is_unknown(self) -> bool:
        return self.state is Availability.UNKNOWN

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": str(self.state),
            "state": str(self.state),
            "available": self.available,
            "is_mocked": self.is_mocked,
            "implementation": self.implementation,
            "provider_or_backend": self.provider_or_backend,
            "last_verified_at": self.last_verified_at,
            "verification_method": self.verification_method,
            "evidence": self.evidence,
            "failure_reason": self.failure_reason or self.reason,
            "metadata": dict(self.metadata),
        }


# Canonical alias ensuring a single unified capability model across the codebase:
CapabilityRecord = CapabilityStatus
