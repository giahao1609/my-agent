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


from unittest.mock import AsyncMock, MagicMock
from core.visual_diff import VisualDiffValidator
from integrations.playwright_browser import RealPlaywrightSession, create_minimal_png, is_playwright_installed


@pytest.mark.asyncio
async def test_real_playwright_session_driver_hooks(tmp_path: Path) -> None:
    # Setup mock playwright components
    mock_pw = MagicMock()
    mock_pw.stop = AsyncMock()

    mock_browser = MagicMock()
    mock_browser.close = AsyncMock()

    mock_context = MagicMock()
    mock_context.close = AsyncMock()

    mock_page = MagicMock()
    mock_page.url = "https://example.com/dashboard"
    mock_page.title = AsyncMock(return_value="Teachora Dashboard")
    mock_page.close = AsyncMock()
    mock_page.goto = AsyncMock(return_value=MagicMock(status=200))
    mock_page.click = AsyncMock()
    mock_page.fill = AsyncMock()

    async def fake_screenshot(path, full_page=False):
        Path(path).write_bytes(create_minimal_png(1280, 800, 255, 255, 255, 255))

    mock_page.screenshot = AsyncMock(side_effect=fake_screenshot)

    mock_a11y_tree = {
        "role": "WebArea",
        "name": "Teachora Dashboard",
        "children": [
            {"role": "heading", "name": "Active Courses", "level": 2},
            {"role": "button", "name": "Create Lesson"},
        ],
    }
    mock_page.accessibility = MagicMock()
    mock_page.accessibility.snapshot = AsyncMock(return_value=mock_a11y_tree)

    # Return WCAG violation in evaluate
    mock_page.evaluate = AsyncMock(
        side_effect=[
            "Overview of all active teacher modules and lessons.",  # snippet
            ["WCAG 1.1.1: Image missing alt attribute: banner.jpg"],  # wcag issues
        ]
    )

    policy = BrowserPolicy(
        allowed_domains=("example.com",),
        allow_private_network=False,
        capture_screenshots=True,
    )

    session = RealPlaywrightSession(
        playwright_instance=mock_pw,
        browser=mock_browser,
        context=mock_context,
        page=mock_page,
        policy=policy,
        session_dir=tmp_path / "real_session",
    )

    # Navigate
    res = await session.navigate("https://example.com/dashboard")
    assert res.success is True
    assert res.url == "https://example.com/dashboard"
    assert res.title == "Teachora Dashboard"
    assert res.content_snippet == "Overview of all active teacher modules and lessons."
    assert res.screenshot_path is not None
    assert Path(res.screenshot_path).exists()
    assert res.accessibility_tree == mock_a11y_tree
    assert len(res.a11y_violations) == 1
    assert "WCAG 1.1.1" in res.a11y_violations[0]

    # Click & Fill
    res_click = await session.click("button.cta")
    assert res_click.success is True
    mock_page.click.assert_awaited_once_with("button.cta", timeout=30000)

    res_fill = await session.fill("input#search", "Algebra")
    assert res_fill.success is True
    mock_page.fill.assert_awaited_once_with("input#search", "Algebra", timeout=30000)

    # Manual screenshot
    shot_path = await session.screenshot("final_page")
    assert Path(shot_path).exists()

    # Get a11y tree directly
    tree = await session.get_accessibility_tree()
    assert tree == mock_a11y_tree

    # Close session
    await session.close()
    mock_page.close.assert_awaited_once()
    mock_context.close.assert_awaited_once()
    mock_browser.close.assert_awaited_once()
    mock_pw.stop.assert_awaited_once()

    with pytest.raises(RuntimeError, match="closed"):
        await session.navigate("https://example.com/dashboard")


@pytest.mark.asyncio
async def test_playwright_visual_diff_integration(tmp_path: Path) -> None:
    """Verifies that screenshots generated by browser session can be parsed by VisualDiffValidator."""
    adapter = PlaywrightBrowserAdapter(artifacts_dir=tmp_path)
    policy = BrowserPolicy(allowed_domains=("example.com",), allow_private_network=False, capture_screenshots=True)

    session = await adapter.create_session(policy)
    res1 = await session.navigate("https://example.com/page1")
    res2 = await session.navigate("https://example.com/page2")
    await session.close()

    assert res1.screenshot_path is not None
    assert res2.screenshot_path is not None

    # VisualDiffValidator compare
    diff_res = VisualDiffValidator.compare_images(
        baseline_path=res1.screenshot_path,
        current_path=res2.screenshot_path,
        tolerance_percentage=1.0,
    )

    assert diff_res.total_pixels > 0
    assert diff_res.diff_percentage >= 0.0
    assert "Visual diff" in diff_res.summary or "Visual regression" in diff_res.summary


@pytest.mark.asyncio
async def test_playwright_capabilities_dynamic_switching(monkeypatch) -> None:
    adapter = PlaywrightBrowserAdapter()

    # Case 1: Playwright reported installed
    monkeypatch.setattr("integrations.playwright_browser.is_playwright_installed", lambda: True)
    caps_available = await adapter.capabilities()
    assert all(c.state.value == "available" for c in caps_available)
    assert any("Chromium" in c.evidence or "Playwright" in c.evidence for c in caps_available)

    # Case 2: Playwright reported absent
    monkeypatch.setattr("integrations.playwright_browser.is_playwright_installed", lambda: False)
    caps_mocked = await adapter.capabilities()
    assert all(c.state.value == "mocked" for c in caps_mocked)
    assert any("No playwright dependency" in c.evidence for c in caps_mocked)


@pytest.mark.asyncio
async def test_mcp_browser_navigate_with_accessibility(tmp_path: Path) -> None:
    import my_agent_mcp.server as server_mod

    res = await server_mod.browser_navigate(
        url="https://example.com",
        allowed_domains=["example.com"],
        capture_screenshots=True,
    )

    assert res["success"] is True
    assert res["url"] == "https://example.com"
    assert "screenshot_path" in res
    assert "accessibility_tree" in res
    assert "a11y_violations" in res
