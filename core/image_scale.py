from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from pathlib import Path
from typing import Any

# 缩放逻辑版本；改动尺寸计算、编码参数时 +1，让磁盘上的旧缓存自然失效。
SCALE_VERSION = 1

# webp 编码参数。method 越大压得越狠也越慢，4 是体积与耗时的平衡点。
WEBP_QUALITY = 82
WEBP_METHOD = 4

# 带 alpha 的源图（头像、道具图标）编码时质量给高一些，这类图面积小、
# 边缘透明度对观感影响大，多花的字节可以忽略。
WEBP_QUALITY_ALPHA = 88

# PIL 解压炸弹上限，沿用 PIL 默认值，显式设置以防被别处改过。
MAX_IMAGE_PIXELS = 178_956_970

# 内存里最多保留几张缩放结果的 data URI。缩放后单张通常几 KB 到几十 KB。
MEMORY_ENTRIES = 64
# 磁盘上最多保留几个缩放文件。同一张图会因不同显示尺寸产生多份，留宽裕些。
DISK_ENTRIES = 256

ALPHA_MODES = {"RGBA", "LA", "PA", "P"}


class ImageScaler:
    """把图片按模板里的 CSS 显示尺寸等比缩小并转 webp，按内容与目标尺寸缓存。

    渲染用的是 scale:"css"（1 CSS px = 1 device px），所以目标尺寸直接取
    CSS 尺寸、不乘 DPR。缩放是 CPU 活，放线程池执行，并按
    (SCALE_VERSION, 源文件 mtime/size, 目标尺寸) 哈希做内存 + 磁盘双层缓存。

    失败时一律返回空串，由调用方回退到原图——宁可请求体大，也不能丢图。
    """

    def __init__(self, cache_dir: Path, logger: Any | None = None):
        self.cache_dir = cache_dir
        self.logger = logger
        self._memory: dict[str, str] = {}
        self._memory_order: list[str] = []
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._unavailable_logged = False

    async def data_uri(self, path: Path, box: tuple[int, int]) -> str:
        """返回缩放后 webp 的 data URI；任何一步不成立时返回空串。"""
        width, height = box
        if width <= 0 or height <= 0:
            return ""

        key = self._cache_key(path, width, height)
        if key is None:
            return ""

        cached = self._memory.get(key)
        if cached is not None:
            return cached

        lock = await self._retain_lock(key)
        try:
            async with lock:
                # 等锁期间可能已由并发渲染建好。
                cached = self._memory.get(key)
                if cached is not None:
                    return cached
                try:
                    payload = await asyncio.to_thread(
                        self._load_or_build, key, path, width, height
                    )
                except Exception as exc:
                    self._log_unavailable_once(exc)
                    return ""
                if not payload:
                    return ""
                value = f"data:image/webp;base64,{base64.b64encode(payload).decode('ascii')}"
                self._remember(key, value)
                return value
        finally:
            # 锁已释放后再回收，否则 locked() 恒为真、字典只增不减。
            await self._release_lock(key)

    def _cache_key(self, path: Path, width: int, height: int) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        fingerprint = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
        digest = hashlib.sha256(
            f"{SCALE_VERSION}\x00{fingerprint}\x00{width}x{height}".encode("utf-8")
        ).hexdigest()
        return digest[:32]

    def _load_or_build(self, key: str, path: Path, width: int, height: int) -> bytes:
        """线程池里执行：命中磁盘缓存就直接读，否则缩放一份写回磁盘。"""
        target = self.cache_dir / f"{key}.webp"
        try:
            if target.is_file() and target.stat().st_size > 0:
                return target.read_bytes()
        except OSError:
            pass

        payload = self._build(path, width, height)
        if not payload:
            return b""

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
            self._prune_disk()
        except OSError as exc:
            # 写不进缓存不影响本次渲染，下次再试。
            if self.logger:
                self.logger.warning(f"图片缩放缓存写入失败，本次直接使用内存结果：{exc}")
        return payload

    def _build(self, path: Path, width: int, height: int) -> bytes:
        """缩放并编码。返回空串表示"不值得缩放"，由调用方回退原图。"""
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

        try:
            source_size = path.stat().st_size
        except OSError:
            return b""

        with Image.open(path) as image:
            image.load()
            source_width, source_height = image.size
            if source_width <= 0 or source_height <= 0:
                return b""

            # 按 cover 语义取缩放比：让缩放后的图刚好覆盖显示框。
            # 不做裁剪——时间轴条的宽度是 CSS 变量、运行时才定，裁了会错。
            # contain 场景（.stage-media）用 cover 比例只会略大于所需，不会欠采样。
            ratio = max(width / source_width, height / source_height)
            if ratio >= 1:
                # 源图本来就不比显示尺寸大，放大没有意义，让调用方用原图。
                return b""

            target_width = max(1, round(source_width * ratio))
            target_height = max(1, round(source_height * ratio))

            has_alpha = image.mode in ALPHA_MODES or "transparency" in image.info
            resized = image.convert("RGBA" if has_alpha else "RGB").resize(
                (target_width, target_height), Image.LANCZOS
            )

        buffer = io.BytesIO()
        resized.save(
            buffer,
            format="WEBP",
            quality=WEBP_QUALITY_ALPHA if has_alpha else WEBP_QUALITY,
            method=WEBP_METHOD,
        )
        payload = buffer.getvalue()

        # 小图转 webp 后偶尔反而更大，那就没必要换。
        if not payload or len(payload) >= source_size:
            return b""
        return payload

    def _prune_disk(self) -> None:
        try:
            files = [
                (item, item.stat().st_mtime_ns)
                for item in self.cache_dir.glob("*.webp")
                if item.is_file()
            ]
        except OSError:
            return
        if len(files) <= DISK_ENTRIES:
            return
        files.sort(key=lambda entry: entry[1])
        for item, _ in files[: len(files) - DISK_ENTRIES]:
            try:
                item.unlink()
            except OSError:
                continue

    def _remember(self, key: str, value: str) -> None:
        if key in self._memory:
            self._memory_order.remove(key)
        self._memory[key] = value
        self._memory_order.append(key)
        while len(self._memory_order) > MEMORY_ENTRIES:
            evicted = self._memory_order.pop(0)
            self._memory.pop(evicted, None)

    def _log_unavailable_once(self, exc: Exception) -> None:
        """缩放不可用时只提醒一次，避免每次渲染刷日志。"""
        if self._unavailable_logged or not self.logger:
            return
        self._unavailable_logged = True
        self.logger.warning(
            f"图片缩放不可用，已回退到内嵌原图（请求体会明显变大）：{exc}"
        )

    async def _retain_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def _release_lock(self, key: str) -> None:
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is not None and not lock.locked():
                self._locks.pop(key, None)
