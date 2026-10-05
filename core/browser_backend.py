from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class BrowserPolicy:
    allowed_domains: tuple[str, ...] = field(default_factory=tuple)
    allow_private_network: bool = False
    timeout_ms: int = 30000
    capture_screenshots: bool = True
    capture_traces: bool = True

    def is_domain_allowed(self, url: str) -> bool:
        if not self.allowed_domains:
            return True  # If empty, all public domains allowed unless private network check fails

        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.netloc.split(":")[0]

        if not self.allow_private_network:
            if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.startswith("192.168.") or host.startswith("10."):
                return False

        return any(host == domain or host.endswith(f".{domain}") for domain in self.allowed_domains)


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    action: str
    success: bool
    url: str
    title: str = ""
    content_snippet: str = ""
    screenshot_path: str | None = None
    trace_path: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "success": self.success,
            "url": self.url,
            "title": self.title,
            "content_snippet": self.content_snippet,
            "screenshot_path": self.screenshot_path,
            "trace_path": self.trace_path,
            "error_message": self.error_message,
        }


class BrowserSession(Protocol):
    async def navigate(self, url: str) -> BrowserActionResult: ...
    async def click(self, selector: str) -> BrowserActionResult: ...
    async def fill(self, selector: str, value: str) -> BrowserActionResult: ...
    async def screenshot(self, name: str = "screenshot") -> str: ...
    async def close(self) -> None: ...


class BrowserBackend(Protocol):
    async def create_session(self, policy: BrowserPolicy | None = None) -> BrowserSession: ...
    async def capabilities(self) -> Sequence[dict[str, Any]]: ...
