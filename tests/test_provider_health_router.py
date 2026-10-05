from __future__ import annotations

import time
import pytest

from core.provider_health_router import (
    CircuitState,
    FallbackRoutingEngine,
    ProviderHealthRecord,
)


def test_provider_health_circuit_breaker():
    rec = ProviderHealthRecord(provider_name="openai", failure_threshold=3, cooldown_seconds=0.1)
    assert rec.is_available is True
    assert rec.state == CircuitState.CLOSED

    # Record 2 failures -> still closed
    rec.record_failure("error 1")
    rec.record_failure("error 2")
    assert rec.state == CircuitState.CLOSED
    assert rec.is_available is True

    # 3rd failure -> OPEN
    rec.record_failure("error 3")
    assert rec.state == CircuitState.OPEN
    assert rec.is_available is False

    # Wait for cooldown
    time.sleep(0.12)
    assert rec.is_available is True
    assert rec.state == CircuitState.HALF_OPEN

    # Success restores to CLOSED
    rec.record_success()
    assert rec.state == CircuitState.CLOSED
    assert rec.consecutive_failures == 0


def test_fallback_routing_engine_failover():
    engine = FallbackRoutingEngine()

    # Initially anthropic is healthy
    selected, skipped = engine.resolve_active_provider("anthropic")
    assert selected == "anthropic"
    assert skipped == []

    # Trip anthropic breaker
    anthropic = engine.get_provider_health("anthropic")
    for _ in range(3):
        anthropic.record_failure("API Timeout")

    # Should failover to openai
    selected, skipped = engine.resolve_active_provider("anthropic")
    assert selected == "openai"
    assert "anthropic" in skipped

    # Trip openai breaker as well
    openai = engine.get_provider_health("openai")
    for _ in range(3):
        openai.record_failure("Rate limit")

    # Should failover to google
    selected, skipped = engine.resolve_active_provider("anthropic")
    assert selected == "google"
    assert "anthropic" in skipped
    assert "openai" in skipped
