from __future__ import annotations

from core.capabilities.builtin import create_default_registry, register_builtin_capabilities
from core.capabilities.models import Availability, CapabilityRecord, CapabilityStatus
from core.capabilities.registry import CapabilityProvider, CapabilityRegistry
from core.errors import CapabilityMockedError, CapabilityUnavailableError

default_capability_registry = create_default_registry()

__all__ = [
    "Availability",
    "CapabilityStatus",
    "CapabilityRecord",
    "CapabilityRegistry",
    "CapabilityProvider",
    "CapabilityUnavailableError",
    "CapabilityMockedError",
    "default_capability_registry",
    "create_default_registry",
    "register_builtin_capabilities",
]
