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
from typing import Any
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import aiohttp

from .image_scale import ImageScaler


class UnsafeAssetUrl(ValueError):
    """当图片地址可能指向不安全的网络地址时抛出。"""


class AssetTooLarge(ValueError):
    """当图片超过配置的下载体积上限时抛出。"""


class AssetCache:
    MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
    MAX_REDIRECTS = 3
    MEMORY_CACHE_ENTRIES = 64
    MEMORY_CACHE_MAX_BYTES = 32 * 1024 * 1024
    MAX_LOCAL_BYTES = 16 * 1024 * 1024
    MAX_DISK_CACHE_FILES = 512
    MAX_DISK_CACHE_BYTES = 256 * 1024 * 1024
    DATA_URI_CONCURRENCY = 6
    DOWNLOAD_FAILURE_CACHE_ENTRIES = 64
    DISK_CACHE_PRUNE_INTERVAL = 16
    ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    LOCAL_MIME_TYPES = {"font/otf", "font/ttf", "font/woff2", "application/font-sfnt", "application/x-font-opentype", "application/font-woff2"}
    MIME_SUFFIXES = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }

    def __init__(
        self,
        root: Path,
        session: aiohttp.ClientSession,
        proxy: str = "",
        logger: Any | None = None,
    ):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.session = session
        self.proxy = proxy.strip()
        self.logger = logger
        self._download_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._download_locks_guard = asyncio.Lock()
        self._data_uri_cache: OrderedDict[tuple[str, int, int], str] = OrderedDict()
        self._data_uri_cache_bytes = 0
        self._data_uri_semaphore = asyncio.Semaphore(self.DATA_URI_CONCURRENCY)
        self._failed_download_urls: OrderedDict[str, None] = OrderedDict()
        self._downloads_since_prune = 0
        self._disk_cache_prune_initialized = False
        # 字体单独缓存，避免被图片 LRU 挤掉导致每次渲染重新 base64 编码
        self._font_cache: dict[tuple[str, int, int], str] = {}
        # 图片缩放器，缓存在 root/images/ 下
        self.image_scaler = ImageScaler(self.root / "images", logger=logger)

    async def data_uri(self, source: str, box: tuple[int, int] | None = None, *, quality: int | None = None, fit: str = "cover", force_webp: bool = False) -> str:
        """把外部图片 URL 或已验证的 data URI 转成 data URI。

        box 给出该图在模板里的 CSS 显示尺寸（宽, 高）时，先按 cover 语义等比
        缩放到刚好覆盖该尺寸再转 webp，避免把远大于显示尺寸的原图整份内嵌。
        缩放不可用或失败时回退到原图，保证不丢图。
        """
        async with self._data_uri_semaphore:
            if not source:
                return ""
            if source.startswith("data:"):
                return source if self._is_safe_data_uri(source) else ""
            if source.startswith(("http://", "https://")):
                try:
                    path = await self._download(source)
                except Exception as exc:
                    self._log_download_failure(source, exc)
                    return ""
                return await self._encode(path, require_image=True, box=box, quality=quality, fit=fit, force_webp=force_webp)
            return ""

    async def data_uri_local(
        self,
        source: str | Path,
        box: tuple[int, int] | None = None,
        *,
        quality: int | None = None,
        fit: str = "cover",
        force_webp: bool = False,
        trusted_roots: tuple[Path, ...] = (),
    ) -> str:
        """编码受信任目录中的本地资源，拒绝任意外部路径。"""
        try:
            path = Path(source).resolve(strict=True)
            roots = (self.root.resolve(), *(root.resolve() for root in trusted_roots))
            if not any(path.is_relative_to(root) for root in roots):
                return ""
        except (OSError, ValueError):
            return ""
        async with self._data_uri_semaphore:
            return await self._encode(
                path,
                require_image=False,
                box=box,
                quality=quality,
                fit=fit,
                force_webp=force_webp,
            )

    async def _encode(self, path: Path, require_image: bool, box: tuple[int, int] | None, quality: int | None, fit: str, force_webp: bool) -> str:
        """按需缩放后编码；缩放拿不到结果时退回原图的同步编码路径。"""
        if box is not None:
            scaled = await self.image_scaler.data_uri(path, box, quality=quality, fit=fit, force_webp=force_webp)
            if scaled:
                return scaled
        return self._data_uri_from_path(path, require_image=require_image)

    def _data_uri_from_path(self, path: Path, require_image: bool) -> str:
        try:
            stat = path.stat()
        except OSError:
            return ""
        max_bytes = self.MAX_DOWNLOAD_BYTES if require_image else self.MAX_LOCAL_BYTES
        if not path.is_file() or not 0 < stat.st_size <= max_bytes:
            return ""
        cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)

        # 字体文件用单独缓存，避免被图片 LRU 挤掉
        is_font = path.suffix.lower() in {".otf", ".ttf", ".woff", ".woff2"}
        if is_font:
            cached = self._font_cache.get(cache_key)
            if cached is not None:
                return cached
        else:
            cached = self._data_uri_cache.get(cache_key)
            if cached is not None:
                self._data_uri_cache.move_to_end(cache_key)
                return cached

        try:
            payload = path.read_bytes()
        except OSError:
            return ""
        mime = self._detect_image_mime(payload) or mimetypes.guess_type(path.name)[0] or {".otf": "font/otf", ".ttf": "font/ttf", ".woff2": "font/woff2"}.get(path.suffix.lower())
        if require_image:
            if mime not in self.ALLOWED_MIME_TYPES or not self._matches_image_mime(payload, mime):
                return ""
        elif mime not in self.ALLOWED_MIME_TYPES | self.LOCAL_MIME_TYPES:
            return ""
        value = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"

        if is_font:
            self._font_cache[cache_key] = value
        else:
            self._remember_data_uri(cache_key, value)
        return value

    def _remember_data_uri(self, cache_key: tuple[str, int, int], value: str) -> None:
        previous = self._data_uri_cache.pop(cache_key, None)
        if previous is not None:
            self._data_uri_cache_bytes -= len(previous)
        self._data_uri_cache[cache_key] = value
        self._data_uri_cache_bytes += len(value)
        while (
            len(self._data_uri_cache) > self.MEMORY_CACHE_ENTRIES
            or self._data_uri_cache_bytes > self.MEMORY_CACHE_MAX_BYTES
        ):
            _, evicted = self._data_uri_cache.popitem(last=False)
            self._data_uri_cache_bytes -= len(evicted)

    async def download(self, url: str, *, max_bytes: int | None = None) -> Path:
        """下载远程资源并返回缓存路径。"""
        return await self._download(url, max_bytes=max_bytes)

    async def _download(self, url: str, *, max_bytes: int | None = None) -> Path:
        download_limit = self._download_limit(max_bytes)
        lock = await self._retain_download_lock(url)
        try:
            async with lock:
                target = self._target_path(url)
                if self._valid_cached_file(target, max_bytes=download_limit):
                    return target
                current = url
                request_kwargs = {"proxy": self.proxy} if self.proxy else {}
                for redirect_count in range(self.MAX_REDIRECTS + 1):
                    await self._validate_remote_url(current)
                    async with self.session.get(
                        current, allow_redirects=False, **request_kwargs
                    ) as response:
                        self._validate_response_peer(response)
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
                        if content_length is not None and content_length > download_limit:
                            raise AssetTooLarge(f"图片超过 {download_limit} 字节限制")
                        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
                        try:
                            written = 0
                            with temporary.open("wb") as output:
                                async for chunk in response.content.iter_chunked(64 * 1024):
                                    written += len(chunk)
                                    if written > download_limit:
                                        raise AssetTooLarge(f"图片超过 {download_limit} 字节限制")
                                    output.write(chunk)
                            with temporary.open("rb") as input_file:
                                header = input_file.read(12)
                            if not self._matches_image_mime(header, content_type):
                                raise ValueError("图片内容与声明类型不一致")
                            os.replace(temporary, target)
                            self._maybe_prune_disk_cache()
                            return target
                        finally:
                            try:
                                temporary.unlink(missing_ok=True)
                            except OSError:
                                pass
                raise UnsafeAssetUrl("图片重定向无效或次数过多")
        finally:
            await self._release_download_lock(url, lock)

    def _download_limit(self, max_bytes: int | None) -> int:
        if max_bytes is None:
            return self.MAX_DOWNLOAD_BYTES
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise ValueError("图片下载上限必须是整数")
        if not self.MAX_DOWNLOAD_BYTES <= max_bytes <= self.MAX_LOCAL_BYTES:
            raise ValueError(
                f"图片下载上限必须在 {self.MAX_DOWNLOAD_BYTES} 到 {self.MAX_LOCAL_BYTES} 字节之间"
            )
        return max_bytes

    async def _retain_download_lock(self, url: str) -> asyncio.Lock:
        async with self._download_locks_guard:
            lock, references = self._download_locks.get(url, (asyncio.Lock(), 0))
            self._download_locks[url] = (lock, references + 1)
            return lock

    async def _release_download_lock(self, url: str, lock: asyncio.Lock) -> None:
        async with self._download_locks_guard:
            current = self._download_locks.get(url)
            if current is None or current[0] is not lock:
                return
            _, references = current
            if references <= 1:
                self._download_locks.pop(url, None)
            else:
                self._download_locks[url] = (lock, references - 1)

    def _maybe_prune_disk_cache(self) -> None:
        # 首次下载仍做一次完整清理，以处理插件重启前遗留的超限缓存；
        # 后续按批次检查，避免每个新资源都全量扫描目录。
        if not getattr(self, "_disk_cache_prune_initialized", False):
            self._disk_cache_prune_initialized = True
            self._prune_disk_cache()
            return
        self._downloads_since_prune = getattr(self, "_downloads_since_prune", 0) + 1
        if self._downloads_since_prune < self.DISK_CACHE_PRUNE_INTERVAL:
            return
        self._downloads_since_prune = 0
        self._prune_disk_cache()

    def _prune_disk_cache(self) -> None:
        files: list[tuple[Path, int, int]] = []
        try:
            candidates = list(self.root.iterdir())
        except OSError:
            return
        for path in candidates:
            if path.name.startswith(".") or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((path, stat.st_size, stat.st_mtime_ns))
        total_bytes = sum(size for _, size, _ in files)
        remaining = len(files)
        for path, size, _ in sorted(files, key=lambda item: item[2]):
            if remaining <= self.MAX_DISK_CACHE_FILES and total_bytes <= self.MAX_DISK_CACHE_BYTES:
                break
            try:
                path.unlink()
            except OSError:
                continue
            remaining -= 1
            total_bytes -= size

    def _log_download_failure(self, url: str, exc: Exception) -> None:
        if self.logger is None or url in self._failed_download_urls:
            return
        self._failed_download_urls[url] = None
        self._failed_download_urls.move_to_end(url)
        while len(self._failed_download_urls) > self.DOWNLOAD_FAILURE_CACHE_ENTRIES:
            self._failed_download_urls.popitem(last=False)
        parsed = urlparse(url)
        safe_url = f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"
        self.logger.warning(f"图片资源加载失败：{safe_url}（{type(exc).__name__}）")

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

    def _validate_response_peer(self, response: Any) -> None:
        """直连请求建立 TCP 连接后的纵深防御校验。"""
        if self.proxy:
            # 配置了代理时由代理自行解析目标地址，这是一条显式的信任边界。
            return
        connection = getattr(response, "connection", None)
        transport = getattr(connection, "transport", None)
        if transport is None:
            protocol = getattr(response, "_protocol", None)
            transport = getattr(protocol, "transport", None)
        if transport is None:
            return
        peer = transport.get_extra_info("peername")
        if not isinstance(peer, tuple) or not peer:
            return
        try:
            address = ipaddress.ip_address(str(peer[0]))
        except ValueError as exc:
            raise UnsafeAssetUrl("图片连接的对端地址无效") from exc
        if not address.is_global:
            raise UnsafeAssetUrl("图片连接到了私网、回环或保留地址")
    def _valid_cached_file(self, path: Path, *, max_bytes: int | None = None) -> bool:
        limit = self.MAX_DOWNLOAD_BYTES if max_bytes is None else max_bytes
        try:
            stat = path.stat()
            if not path.is_file() or not 0 < stat.st_size <= limit:
                return False
            with path.open("rb") as input_file:
                header = input_file.read(12)
        except OSError:
            return False
        return bool(self._detect_image_mime(header))

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
