from __future__ import annotations

import asyncio
import ipaddress
import json
import random
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver


class UnsafeRemoteUrl(ValueError):
    """远程 URL 使用了不安全的协议或地址时抛出。"""


class ResponseTooLarge(ValueError):
    """文本或 JSON 响应超出配置上限时抛出。"""


class PublicResolver(AbstractResolver):
    """直连请求只解析全球可路由地址。"""

    def __init__(self) -> None:
        self._resolver = DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        resolved = await self._resolver.resolve(host, port, family=family)
        if not resolved:
            raise UnsafeRemoteUrl(f"远程地址无法解析：{host}")
        for item in resolved:
            try:
                address = ipaddress.ip_address(str(item["host"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise UnsafeRemoteUrl(f"远程地址解析结果无效：{host}") from exc
            if not address.is_global:
                raise UnsafeRemoteUrl("远程地址不能解析到私网、回环或保留地址")
        return resolved

    async def close(self) -> None:
        await self._resolver.close()


class HttpClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}
    MAX_RETRY_AFTER_SECONDS = 60.0
    MAX_RESPONSE_BYTES = 10 * 1024 * 1024
    CHUNK_BYTES = 64 * 1024

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
        self._validate_url(url)
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
                        retry_after = self._retry_after_delay(raw_retry_after)
                    response.raise_for_status()
                    return await self._decode_response(response, as_json)
            except aiohttp.ClientResponseError as exc:
                if exc.status not in self.RETRY_STATUSES:
                    raise self._request_error(url, exc) from exc
                error = exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                error = exc
            if attempt < self.retries:
                delay = retry_after if retry_after is not None else min(8.0, 0.6 * (2 ** attempt))
                await asyncio.sleep(min(self.MAX_RETRY_AFTER_SECONDS, delay + random.uniform(0.0, 0.25)))
        assert error is not None
        raise self._request_error(url, error) from error

    @classmethod
    async def _decode_response(cls, response: Any, as_json: bool) -> Any:
        payload = await cls._read_limited_body(response)
        if payload is None:
            # 兼容路径：测试替身与不带 StreamReader 的 session 适配器。
            value = await (response.json(content_type=None) if as_json else response.text())
            cls._ensure_fallback_value_size(value, as_json)
            return value
        encoding = getattr(response, "charset", None) or "utf-8"
        text = payload.decode(encoding)
        return json.loads(text) if as_json else text

    @classmethod
    async def _read_limited_body(cls, response: Any) -> bytes | None:
        content_length = getattr(response, "content_length", None)
        if content_length is not None and content_length > cls.MAX_RESPONSE_BYTES:
            raise ResponseTooLarge(f"响应体超过 {cls.MAX_RESPONSE_BYTES} 字节限制")
        content = getattr(response, "content", None)
        if content is None or not hasattr(content, "iter_chunked"):
            return None
        total = 0
        payload = bytearray()
        async for chunk in content.iter_chunked(cls.CHUNK_BYTES):
            total += len(chunk)
            if total > cls.MAX_RESPONSE_BYTES:
                raise ResponseTooLarge(f"响应体超过 {cls.MAX_RESPONSE_BYTES} 字节限制")
            payload.extend(chunk)
        return bytes(payload)

    @classmethod
    def _ensure_fallback_value_size(cls, value: Any, as_json: bool) -> None:
        if as_json:
            size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        else:
            size = len(str(value).encode("utf-8"))
        if size > cls.MAX_RESPONSE_BYTES:
            raise ResponseTooLarge(f"响应体超过 {cls.MAX_RESPONSE_BYTES} 字节限制")

    @classmethod
    def _retry_after_delay(cls, value: Any, now: datetime | None = None) -> float | None:
        try:
            return min(cls.MAX_RETRY_AFTER_SECONDS, max(0.0, float(value)))
        except (TypeError, ValueError):
            pass
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return min(cls.MAX_RETRY_AFTER_SECONDS, max(0.0, (retry_at - current).total_seconds()))

    @staticmethod
    def _validate_url(url: str) -> None:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
        except (TypeError, ValueError) as exc:
            raise UnsafeRemoteUrl("远程地址无效") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not host:
            raise UnsafeRemoteUrl("远程地址必须是 HTTP 或 HTTPS URL")
        if parsed.username or parsed.password:
            raise UnsafeRemoteUrl("远程地址不能包含用户凭据")
        try:
            address = ipaddress.ip_address(host.rstrip("."))
        except ValueError:
            return
        if not address.is_global:
            raise UnsafeRemoteUrl("远程地址不能使用私网、回环或保留 IP")

    @classmethod
    def _request_error(cls, url: str, error: Exception) -> RuntimeError:
        if isinstance(error, aiohttp.ClientResponseError):
            detail = f"HTTP {error.status}"
        elif isinstance(error, asyncio.TimeoutError):
            detail = "TimeoutError"
        else:
            detail = type(error).__name__
        return RuntimeError(f"请求失败：{cls._redacted_url(url)}（{detail}）")

    @staticmethod
    def _redacted_url(url: str) -> str:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            netloc = host
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        except (TypeError, ValueError):
            return "<invalid-url>"