from __future__ import annotations

import pytest
from pathlib import Path

from core.browser_backend import BrowserPolicy
from integrations.playwright_browser import PlaywrightBrowserAdapter


def test_browser_policy_domain_filtering() -> None:
    policy = BrowserPolicy(allowed_domains=("example.com", "api.stripe.com"), allow_private_network=False)

    assert policy.is_domain_allowed("https://example.com/checkout") is True
    assert policy.is_domain_allowed("https://sub.example.com") is True
    assert policy.is_domain_allowed("https://malicious.com") is False
    assert policy.is_domain_allowed("http://localhost:8080") is False
    assert policy.is_domain_allowed("http://127.0.0.1:3000") is False


@pytest.mark.asyncio
async def test_playwright_browser_session_flow(tmp_path: Path) -> None:
    adapter = PlaywrightBrowserAdapter(artifacts_dir=tmp_path)
    policy = BrowserPolicy(allowed_domains=("example.com",), allow_private_network=False, capture_screenshots=True)

    session = await adapter.create_session(policy)

    # Denied navigation
    res_denied = await session.navigate("http://localhost:3000")
    assert res_denied.success is False
    assert "BrowserPolicy" in res_denied.error_message

    # Allowed navigation
    res_nav = await session.navigate("https://example.com/page")
    assert res_nav.success is True
    assert res_nav.screenshot_path is not None
    assert Path(res_nav.screenshot_path).exists()

    # Click & Fill
    res_click = await session.click("#submit-btn")
    assert res_click.success is True

    res_fill = await session.fill("input[name='q']", "test query")
    assert res_fill.success is True

    # Manual Screenshot
    shot_path = await session.screenshot("custom_step")
    assert Path(shot_path).exists()

    await session.close()
    with pytest.raises(RuntimeError, match="closed"):
        await session.navigate("https://example.com")


@pytest.mark.asyncio
async def test_playwright_browser_adapter_capabilities() -> None:
    adapter = PlaywrightBrowserAdapter()
    caps = await adapter.capabilities()
    assert len(caps) >= 3
    assert any(c["name"] == "playwright_browser_automation" for c in caps)
