from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class HttpClient:
    def __init__(self, session: aiohttp.ClientSession, retries: int = 2):
        self.session = session
        self.retries = retries

    async def text(self, url: str, **kwargs: Any) -> str:
        return await self._request(url, False, **kwargs)

    async def json(self, url: str, **kwargs: Any) -> Any:
        return await self._request(url, True, **kwargs)

    async def _request(self, url: str, as_json: bool, **kwargs: Any) -> Any:
        error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self.session.get(url, **kwargs) as response:
                    response.raise_for_status()
                    return await (response.json(content_type=None) if as_json else response.text())
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                error = exc
                if attempt < self.retries:
                    await asyncio.sleep(0.6 * (attempt + 1))
        assert error is not None
        raise RuntimeError(f"请求失败：{url}（{type(error).__name__}: {error}）") from error
