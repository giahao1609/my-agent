from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Sequence

from core.browser_backend import BrowserActionResult, BrowserBackend, BrowserPolicy, BrowserSession


class PlaywrightBrowserSession(BrowserSession):
    def __init__(self, policy: BrowserPolicy, session_dir: Path) -> None:
        self._policy = policy
        self._session_dir = session_dir
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._current_url = "about:blank"
        self._current_title = ""
        self._is_closed = False

    async def navigate(self, url: str) -> BrowserActionResult:
        if self._is_closed:
            raise RuntimeError("Browser session is closed")

        if not self._policy.is_domain_allowed(url):
            return BrowserActionResult(
                action="navigate",
                success=False,
                url=url,
                error_message=f"Domain or private network access denied by BrowserPolicy for URL: {url}",
            )

        self._current_url = url
        self._current_title = f"Page - {url}"

        screenshot_path = None
        if self._policy.capture_screenshots:
            shot_file = self._session_dir / f"nav_{uuid.uuid4().hex[:8]}.png"
            shot_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")
            screenshot_path = str(shot_file)

        return BrowserActionResult(
            action="navigate",
            success=True,
            url=self._current_url,
            title=self._current_title,
            content_snippet=f"Navigated to {url} successfully.",
            screenshot_path=screenshot_path,
        )

    async def click(self, selector: str) -> BrowserActionResult:
        if self._is_closed:
            raise RuntimeError("Browser session is closed")

        return BrowserActionResult(
            action="click",
            success=True,
            url=self._current_url,
            title=self._current_title,
            content_snippet=f"Clicked element matching '{selector}'",
        )

    async def fill(self, selector: str, value: str) -> BrowserActionResult:
        if self._is_closed:
            raise RuntimeError("Browser session is closed")

        return BrowserActionResult(
            action="fill",
            success=True,
            url=self._current_url,
            title=self._current_title,
            content_snippet=f"Filled element '{selector}' with value length {len(value)}",
        )

    async def screenshot(self, name: str = "screenshot") -> str:
        if self._is_closed:
            raise RuntimeError("Browser session is closed")

        shot_file = self._session_dir / f"{name}_{uuid.uuid4().hex[:8]}.png"
        shot_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")
        return str(shot_file)

    async def close(self) -> None:
        self._is_closed = True


class PlaywrightBrowserAdapter(BrowserBackend):
    def __init__(self, artifacts_dir: Path | str | None = None) -> None:
        self._artifacts_dir = Path(artifacts_dir) if artifacts_dir else Path("/tmp/my_agent_browser_artifacts")

    async def create_session(self, policy: BrowserPolicy | None = None) -> BrowserSession:
        effective_policy = policy or BrowserPolicy()
        session_id = f"browsesx-{uuid.uuid4().hex[:12]}"
        session_dir = self._artifacts_dir / session_id
        return PlaywrightBrowserSession(effective_policy, session_dir)

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        from core.status import Availability, CapabilityStatus
        return (
            CapabilityStatus(
                name="playwright_browser_automation",
                state=Availability.MOCKED,
                reason="No Playwright runtime; static PNG and synthetic DOM",
                implementation="PlaywrightBrowserAdapter",
                provider_or_backend="synthetic_browser",
                verification_method="source_inspection",
                evidence="No playwright dependency imported; navigate returns synthetic string",
            ),
            CapabilityStatus(
                name="fresh_browser_context",
                state=Availability.MOCKED,
                reason="Simulated browser session directory only",
                implementation="PlaywrightBrowserAdapter",
                provider_or_backend="synthetic_browser",
                verification_method="source_inspection",
                evidence="Session directory created on disk without browser context",
            ),
            CapabilityStatus(
                name="screenshot_trace_artifacts",
                state=Availability.MOCKED,
                reason="Synthesizes fake screenshots with static 20-byte PNG header",
                implementation="PlaywrightBrowserAdapter",
                provider_or_backend="synthetic_browser",
                verification_method="source_inspection",
                evidence="Writes hardcoded 1x1 PNG bytes to file",
            ),
        )


default_playwright_browser = PlaywrightBrowserAdapter()
