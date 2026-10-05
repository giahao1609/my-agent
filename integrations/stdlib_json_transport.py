from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.request import Request, urlopen


class StdlibJsonTransport:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero"
            )

        self._timeout_seconds = timeout_seconds
        self._opener = opener

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        if not url.strip():
            raise ValueError("url must not be empty")

        return await asyncio.to_thread(
            self._post_json_sync,
            url,
            dict(headers),
            dict(payload),
        )

    def _post_json_sync(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> Mapping[str, object]:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        request = Request(
            url=url,
            data=body,
            headers=headers,
            method="POST",
        )

        with self._opener(
            request,
            timeout=self._timeout_seconds,
        ) as response:
            raw = response.read()

        try:
            decoded = raw.decode("utf-8")
            result = json.loads(decoded)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "transport response must contain valid JSON"
            ) from exc

        if not isinstance(result, Mapping):
            raise ValueError(
                "transport response must be a JSON object"
            )

        return dict(result)
