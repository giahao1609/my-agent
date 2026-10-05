from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from core.errors import CapabilityMockedError, CapabilityUnavailableError
from core.status import Availability, CapabilityRecord, CapabilityStatus


@runtime_checkable
class CapabilityProvider(Protocol):
    """Protocol for components that report one or more capabilities."""

    async def capabilities(self) -> Sequence[CapabilityStatus | Mapping[str, Any]]: ...


class CapabilityRegistry:
    """Canonical registry for truthful, evidence-backed capabilities."""

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityRecord] = {}
        self._providers: list[CapabilityProvider] = []
        self._dynamic_probes: dict[str, Callable[[], CapabilityRecord]] = {}

    @staticmethod
    def normalize_name(name: str) -> str:
        return name.strip().lower().replace(" ", "_").replace(".", "_").replace("-", "_")

    def register(self, record: CapabilityRecord) -> None:
        """Register a static capability record."""
        norm = self.normalize_name(record.name)
        self._capabilities[norm] = record

    def register_probe(self, name: str, probe: Callable[[], CapabilityRecord]) -> None:
        """Register a dynamic probe callable that evaluates capability truth on demand."""
        norm = self.normalize_name(name)
        self._dynamic_probes[norm] = probe

    def register_provider(self, provider: CapabilityProvider) -> None:
        """Register a dynamic capability provider component."""
        self._providers.append(provider)

    def get(self, name: str) -> CapabilityRecord:
        """Retrieve capability by name. Returns UNKNOWN record if not verified with sufficient evidence."""
        norm = self.normalize_name(name)
        if norm in self._dynamic_probes:
            try:
                rec = self._dynamic_probes[norm]()
                self._capabilities[norm] = rec
                return rec
            except Exception as exc:
                return CapabilityRecord(
                    name=name,
                    state=Availability.UNKNOWN,
                    reason=f"Probe failed: {exc}",
                    verification_method="dynamic_probe_error",
                    evidence=str(exc),
                    last_verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )

        if norm in self._capabilities:
            return self._capabilities[norm]

        for key, rec in self._capabilities.items():
            if rec.name.lower() == name.lower():
                return rec

        return CapabilityRecord(
            name=name,
            state=Availability.UNKNOWN,
            reason=f"Capability '{name}' has insufficient evidence or is unregistered",
            verification_method="none",
            evidence="No runtime evidence available",
            last_verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    def is_available(self, name: str) -> bool:
        """Returns True if and only if capability is real, operational, and verified ready."""
        record = self.get(name)
        return record.available

    def require(self, name: str, *, allow_mock: bool = False) -> CapabilityRecord:
        """Require a capability to be available. Rejects MOCKED and UNKNOWN."""
        record = self.get(name)
        if record.is_mocked and not allow_mock:
            raise CapabilityMockedError(
                f"Capability '{name}' is MOCKED and cannot satisfy real AVAILABLE requirement. "
                f"Evidence: {record.evidence}"
            )
        if not record.available:
            raise CapabilityUnavailableError(
                f"Capability '{name}' is {record.state} and not AVAILABLE. "
                f"Reason: {record.failure_reason or record.reason}"
            )
        return record

    def list(self) -> list[CapabilityRecord]:
        """List all verified capability records."""
        for norm, probe in self._dynamic_probes.items():
            try:
                self._capabilities[norm] = probe()
            except Exception:
                pass
        return sorted(self._capabilities.values(), key=lambda c: c.name)

    async def refresh_providers(self) -> list[CapabilityRecord]:
        """Collect capabilities from registered dynamic providers."""
        for provider in self._providers:
            try:
                res = await provider.capabilities()
                for item in res:
                    if isinstance(item, CapabilityStatus):
                        self.register(item)
                    elif isinstance(item, Mapping):
                        name = str(item.get("name", ""))
                        status_str = str(item.get("status", "unknown")).lower()
                        state = Availability(status_str) if status_str in Availability._value2member_map_ else Availability.UNKNOWN
                        self.register(
                            CapabilityRecord(
                                name=name,
                                state=state,
                                reason=str(item.get("reason", "")) or None,
                                implementation=str(item.get("implementation", "")),
                                provider_or_backend=str(item.get("provider_or_backend", "")),
                                evidence=str(item.get("evidence", "")),
                                last_verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            )
                        )
            except Exception:
                pass
        return self.list()

    async def select_provider(
        self,
        capability_name: str,
        candidate_providers: Sequence[Any],
        *,
        require_real: bool = True,
    ) -> Any:
        """Select a candidate provider that satisfies the capability requirement, rejecting mocks if required."""
        norm_target = self.normalize_name(capability_name)
        record = self.get(capability_name)

        for candidate in candidate_providers:
            if hasattr(candidate, "capabilities"):
                try:
                    caps = candidate.capabilities()
                    if hasattr(caps, "__await__"):
                        caps = await caps
                    if isinstance(caps, Sequence):
                        for cap in caps:
                            cap_name = cap.name if isinstance(cap, CapabilityStatus) else cap.get("name", "")
                            norm_cap = self.normalize_name(cap_name)
                            if norm_cap == norm_target or norm_target in norm_cap or norm_cap in norm_target:
                                is_mock = (
                                    cap.is_mocked
                                    if isinstance(cap, CapabilityStatus)
                                    else cap.get("status") == Availability.MOCKED.value or cap.get("state") == Availability.MOCKED.value
                                )
                                if require_real and is_mock:
                                    break
                                return candidate
                except Exception:
                    pass

            cls_name = type(candidate).__name__
            if record.implementation and cls_name in record.implementation:
                if require_real and record.is_mocked:
                    continue
                if record.available:
                    return candidate

        raise CapabilityUnavailableError(
            f"No suitable provider found for capability '{capability_name}' (require_real={require_real})"
        )
