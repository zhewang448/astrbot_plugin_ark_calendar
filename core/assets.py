from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import mimetypes
import os
import socket
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import aiohttp


class UnsafeAssetUrl(ValueError):
    """Raised when an asset URL could reach an unsafe network address."""


class AssetTooLarge(ValueError):
    """Raised when an asset exceeds the configured download limit."""


class AssetCache:
    MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
    MAX_REDIRECTS = 3
    MEMORY_CACHE_ENTRIES = 64
    MAX_LOCAL_BYTES = 16 * 1024 * 1024
    ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    LOCAL_MIME_TYPES = {"font/otf", "font/ttf", "application/font-sfnt", "application/x-font-opentype"}
    MIME_SUFFIXES = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }

    def __init__(
        self, root: Path, session: aiohttp.ClientSession, proxy: str = ""
    ):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.session = session
        self.proxy = proxy.strip()
        self._download_locks: dict[str, asyncio.Lock] = {}
        self._download_locks_guard = asyncio.Lock()
        self._data_uri_cache: OrderedDict[tuple[str, int, int], str] = OrderedDict()

    async def data_uri(self, source: str) -> str:
        if not source:
            return ""
        if source.startswith("data:"):
            return source if self._is_safe_data_uri(source) else ""
        if source.startswith(("http://", "https://")):
            try:
                path = await self._download(source)
            except Exception:
                return ""
            return self._data_uri_from_path(path, require_image=True)
        return self._data_uri_from_path(Path(source), require_image=False)

    def _data_uri_from_path(self, path: Path, require_image: bool) -> str:
        try:
            stat = path.stat()
        except OSError:
            return ""
        max_bytes = self.MAX_DOWNLOAD_BYTES if require_image else self.MAX_LOCAL_BYTES
        if not path.is_file() or not 0 < stat.st_size <= max_bytes:
            return ""
        cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        cached = self._data_uri_cache.get(cache_key)
        if cached is not None:
            self._data_uri_cache.move_to_end(cache_key)
            return cached
        try:
            payload = path.read_bytes()
        except OSError:
            return ""
        mime = self._detect_image_mime(payload) or mimetypes.guess_type(path.name)[0] or {".otf": "font/otf", ".ttf": "font/ttf"}.get(path.suffix.lower())
        if require_image:
            if mime not in self.ALLOWED_MIME_TYPES or not self._matches_image_mime(payload, mime):
                return ""
        elif mime not in self.ALLOWED_MIME_TYPES | self.LOCAL_MIME_TYPES:
            return ""
        value = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
        self._data_uri_cache[cache_key] = value
        self._data_uri_cache.move_to_end(cache_key)
        while len(self._data_uri_cache) > self.MEMORY_CACHE_ENTRIES:
            self._data_uri_cache.popitem(last=False)
        return value

    async def _download(self, url: str) -> Path:
        lock = await self._download_lock(url)
        async with lock:
            target = self._target_path(url)
            if self._valid_cached_file(target):
                return target
            current = url
            request_kwargs = {"proxy": self.proxy} if self.proxy else {}
            for redirect_count in range(self.MAX_REDIRECTS + 1):
                await self._validate_remote_url(current)
                async with self.session.get(
                    current, allow_redirects=False, **request_kwargs
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "")
                        if not location or redirect_count >= self.MAX_REDIRECTS:
                            raise UnsafeAssetUrl("图片重定向无效或次数过多")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type not in self.ALLOWED_MIME_TYPES:
                        raise ValueError(f"不支持的图片类型：{content_type or 'unknown'}")
                    content_length = response.content_length
                    if content_length is not None and content_length > self.MAX_DOWNLOAD_BYTES:
                        raise AssetTooLarge(f"图片超过 {self.MAX_DOWNLOAD_BYTES} 字节限制")
                    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
                    try:
                        written = 0
                        with temporary.open("wb") as output:
                            async for chunk in response.content.iter_chunked(64 * 1024):
                                written += len(chunk)
                                if written > self.MAX_DOWNLOAD_BYTES:
                                    raise AssetTooLarge(f"图片超过 {self.MAX_DOWNLOAD_BYTES} 字节限制")
                                output.write(chunk)
                        payload = temporary.read_bytes()
                        if not self._matches_image_mime(payload, content_type):
                            raise ValueError("图片内容与声明类型不一致")
                        os.replace(temporary, target)
                        return target
                    finally:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
            raise UnsafeAssetUrl("图片重定向无效或次数过多")

    async def _download_lock(self, url: str) -> asyncio.Lock:
        async with self._download_locks_guard:
            return self._download_locks.setdefault(url, asyncio.Lock())

    def _target_path(self, url: str) -> Path:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            suffix = ".img"
        return self.root / f"{hashlib.sha256(url.encode()).hexdigest()}{suffix}"

    async def _validate_remote_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise UnsafeAssetUrl("图片地址必须是 HTTPS URL")
        host = parsed.hostname.rstrip(".")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            loop = asyncio.get_running_loop()
            try:
                resolved = await loop.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            except socket.gaierror as exc:
                raise UnsafeAssetUrl(f"图片域名无法解析：{host}") from exc
            addresses = list({ipaddress.ip_address(item[4][0]) for item in resolved})
        if not addresses or any(not address.is_global for address in addresses):
            raise UnsafeAssetUrl("图片地址不能解析到私网、回环或保留地址")

    def _valid_cached_file(self, path: Path) -> bool:
        try:
            payload = path.read_bytes()
        except OSError:
            return False
        return 0 < len(payload) <= self.MAX_DOWNLOAD_BYTES and bool(self._detect_image_mime(payload))

    @classmethod
    def _detect_image_mime(cls, payload: bytes) -> str | None:
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if payload.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if payload.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
            return "image/webp"
        return None

    @classmethod
    def _matches_image_mime(cls, payload: bytes, mime: str) -> bool:
        return cls._detect_image_mime(payload) == mime

    def _is_safe_data_uri(self, value: str) -> bool:
        header, separator, encoded = value.partition(",")
        if not separator or ";base64" not in header.lower():
            return False
        mime = header[5:].split(";", 1)[0].lower()
        if mime not in self.ALLOWED_MIME_TYPES:
            return False
        if len(encoded) > ((self.MAX_DOWNLOAD_BYTES * 4) // 3) + 16:
            return False
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return False
        return len(payload) <= self.MAX_DOWNLOAD_BYTES and self._matches_image_mime(payload, mime)
