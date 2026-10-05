from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal / Healthy operation
    OPEN = "open"          # Failing / Outage - skip provider
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass(slots=True)
class ProviderHealthRecord:
    provider_name: str
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    last_failure_timestamp: float = 0.0
    total_requests: int = 0
    total_failures: int = 0
    last_error_message: str | None = None

    @property
    def is_available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            # Check if cooldown has elapsed
            if time.time() - self.last_failure_timestamp >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return True
        return False

    def record_success(self) -> None:
        self.total_requests += 1
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.last_error_message = None

    def record_failure(self, error_message: str | None = None) -> None:
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure_timestamp = time.time()
        self.last_error_message = error_message

        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "state": self.state.value,
            "is_available": self.is_available,
            "consecutive_failures": self.consecutive_failures,
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
            "last_error_message": self.last_error_message,
        }


class FallbackRoutingEngine:
    """Manages provider health, circuit breakers, and automatic failover sequences."""

    DEFAULT_FALLBACK_CHAINS: dict[str, list[str]] = {
        "anthropic": ["anthropic", "openai", "google", "local"],
        "openai": ["openai", "anthropic", "google", "local"],
        "google": ["google", "openai", "anthropic", "local"],
        "local": ["local", "openai", "google", "anthropic"],
    }

    def __init__(self) -> None:
        self._providers: dict[str, ProviderHealthRecord] = {
            "openai": ProviderHealthRecord("openai"),
            "anthropic": ProviderHealthRecord("anthropic"),
            "google": ProviderHealthRecord("google"),
            "local": ProviderHealthRecord("local"),
        }

    def get_provider_health(self, provider_name: str) -> ProviderHealthRecord:
        if provider_name not in self._providers:
            self._providers[provider_name] = ProviderHealthRecord(provider_name)
        return self._providers[provider_name]

    def record_provider_result(
        self,
        provider_name: str,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        health = self.get_provider_health(provider_name)
        if success:
            health.record_success()
        else:
            health.record_failure(error_message)

    def resolve_active_provider(
        self,
        preferred_provider: str = "anthropic",
    ) -> tuple[str, list[str]]:
        """Resolves the best available provider following the fallback chain.

        Returns (selected_provider, list_of_skipped_unhealthy_providers).
        """
        chain = self.DEFAULT_FALLBACK_CHAINS.get(
            preferred_provider,
            [preferred_provider, "openai", "anthropic", "google", "local"],
        )
        skipped: list[str] = []

        for candidate in chain:
            health = self.get_provider_health(candidate)
            if health.is_available:
                return candidate, skipped
            skipped.append(candidate)

        # If all are open, return the preferred provider as last resort
        return preferred_provider, skipped

    def get_all_health_statuses(self) -> dict[str, dict[str, Any]]:
        return {
            name: record.to_dict()
            for name, record in self._providers.items()
        }
