from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from core.protocols import ModelBackend


class SharedModelBackendProvider:
    def __init__(
        self,
        build: Callable[[], Awaitable[ModelBackend]],
    ) -> None:
        self._build = build
        self._backend: ModelBackend | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> ModelBackend:
        backend = self._backend
        if backend is not None:
            return backend

        async with self._lock:
            backend = self._backend
            if backend is not None:
                return backend

            # Assign only after a successful build.
            # If build() raises, _backend remains None and a later
            # call can retry safely.
            backend = await self._build()
            self._backend = backend
            return backend
