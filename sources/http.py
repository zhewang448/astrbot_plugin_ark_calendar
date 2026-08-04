from __future__ import annotations

import asyncio
import random
from typing import Any

import aiohttp


class HttpClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        session: aiohttp.ClientSession,
        retries: int = 2,
        proxy: str = "",
    ):
        self.session = session
        self.retries = max(0, retries)
        self.proxy = proxy.strip()

    async def text(self, url: str, **kwargs: Any) -> str:
        return await self._request(url, False, **kwargs)

    async def json(self, url: str, **kwargs: Any) -> Any:
        return await self._request(url, True, **kwargs)

    async def _request(self, url: str, as_json: bool, **kwargs: Any) -> Any:
        error: Exception | None = None
        request_kwargs = dict(kwargs)
        headers = dict(request_kwargs.pop("headers", {}))
        headers.setdefault("Accept-Encoding", "identity")
        request_kwargs["headers"] = headers
        if self.proxy and "proxy" not in request_kwargs:
            request_kwargs["proxy"] = self.proxy

        for attempt in range(self.retries + 1):
            retry_after: float | None = None
            try:
                async with self.session.get(url, **request_kwargs) as response:
                    status = getattr(response, "status", 200)
                    if status in self.RETRY_STATUSES:
                        raw_retry_after = getattr(response, "headers", {}).get("Retry-After", "")
                        try:
                            retry_after = max(0.0, float(raw_retry_after))
                        except (TypeError, ValueError):
                            retry_after = None
                    response.raise_for_status()
                    return await (
                        response.json(content_type=None)
                        if as_json
                        else response.text()
                    )
            except aiohttp.ClientResponseError as exc:
                if exc.status not in self.RETRY_STATUSES:
                    raise
                error = exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                error = exc
            if attempt < self.retries:
                delay = retry_after if retry_after is not None else min(8.0, 0.6 * (2 ** attempt))
                await asyncio.sleep(delay + random.uniform(0.0, 0.25))
        assert error is not None
        raise RuntimeError(
            f"请求失败：{url}（{type(error).__name__}: {error}）"
        ) from error
