from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Availability(StrEnum):
    READY = 'ready'
    DEGRADED = 'degraded'
    UNAVAILABLE = 'unavailable'


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    name: str
    state: Availability
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError('capability name must not be empty')
        if self.state is not Availability.READY and not self.reason:
            raise ValueError('reason is required when capability is not ready')

    @property
    def available(self) -> bool:
        return self.state is not Availability.UNAVAILABLE
