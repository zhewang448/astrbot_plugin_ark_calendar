from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from pathlib import Path
from typing import Any

# subset 逻辑版本；改动字符收集规则或 subset 参数时 +1，让磁盘上的旧缓存自然失效。
SUBSET_VERSION = 1

# 无论当期数据里有没有出现，都固定收进子集的字符。
# 覆盖数字、拉丁、常用标点和日期词，避免个别渲染因为数据里恰好没出现而缺字。
BASE_CHARS = (
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    "　、。〈〉《》「」『』【】〔〕·—…～"
    "％＃＆＊－／＋＜＝＞￥　"
    "年月日时分秒周星期一二三四五六七八九十零今明昨天上下午前后至距开始结束不足已"
)

# 单个字符串超过这个长度基本是 base64 data URI，不含要渲染的字形，直接跳过省 CPU。
MAX_SCANNED_STRING = 4096
_SKIP_PREFIXES = ("data:", "http://", "https://", "//")

# 内存里最多保留几份子集的 data URI（每份约 300–400 KB）。
MEMORY_ENTRIES = 4
# 磁盘上最多保留几个子集文件，超出按 mtime 淘汰最旧的。
DISK_ENTRIES = 8


def _walk(value: Any, out: set[str]) -> None:
    """递归收集容器里所有字符串的字符，跳过 data URI / URL / 超长串。"""
    if isinstance(value, str):
        if not value or len(value) > MAX_SCANNED_STRING:
            return
        if value.startswith(_SKIP_PREFIXES):
            return
        out.update(value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and len(key) <= MAX_SCANNED_STRING:
                out.update(key)
            _walk(item, out)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _walk(item, out)


def collect_charset(*sources: Any) -> str:
    """把模板数据、模板源码等来源里实际出现的字符汇总成排序后的字符集。"""
    chars: set[str] = set(BASE_CHARS)
    for source in sources:
        _walk(source, chars)
    # 控制字符不需要字形。
    chars = {char for char in chars if char.isprintable()}
    return "".join(sorted(chars))


class FontSubsetError(RuntimeError):
    """子集化不可用（缺 fonttools/brotli，或源字体读不出来）。"""


class FontSubsetter:
    """把源字体按实际用到的字形裁成 woff2，并按字符集哈希做内存 + 磁盘缓存。

    子集化本身要 1–3 秒，所以放线程池执行，并按 (源字体, 字符集) 哈希缓存。
    活动数据按天变化，正常情况下每天只会重建一次，其余渲染全部命中缓存。
    """

    def __init__(self, source: Path, cache_dir: Path, logger: Any | None = None):
        self.source = source
        self.cache_dir = cache_dir
        self.logger = logger
        self._memory: dict[str, str] = {}
        self._memory_order: list[str] = []
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._unavailable_logged = False

    async def data_uri(self, charset: str) -> str:
        """返回子集字体的 data URI；不可用时抛 FontSubsetError 交给调用方回退。"""
        key = self._cache_key(charset)

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
                payload = await asyncio.to_thread(self._load_or_build, key, charset)
                value = f"data:font/woff2;base64,{base64.b64encode(payload).decode('ascii')}"
                self._remember(key, value)
                return value
        finally:
            # 锁已释放后再回收，否则 locked() 恒为真、字典只增不减。
            await self._release_lock(key)

    def _cache_key(self, charset: str) -> str:
        try:
            stat = self.source.stat()
            fingerprint = f"{self.source.name}:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError as exc:
            raise FontSubsetError(f"源字体不可读：{self.source}") from exc
        digest = hashlib.sha256(
            f"{SUBSET_VERSION}\x00{fingerprint}\x00{charset}".encode("utf-8")
        ).hexdigest()
        return digest[:32]

    def _load_or_build(self, key: str, charset: str) -> bytes:
        """线程池里执行：命中磁盘缓存就直接读，否则 subset 一份写回磁盘。"""
        target = self.cache_dir / f"{key}.woff2"
        try:
            if target.is_file() and target.stat().st_size > 0:
                return target.read_bytes()
        except OSError:
            pass

        payload = self._build(charset)

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
            self._prune_disk()
        except OSError as exc:
            # 写不进缓存不影响本次渲染，下次再试。
            if self.logger:
                self.logger.warning(f"字体子集缓存写入失败，本次直接使用内存结果：{exc}")
        return payload

    def _build(self, charset: str) -> bytes:
        try:
            from fontTools import subset as ft_subset
            from fontTools.ttLib import TTFont
        except ImportError as exc:
            raise FontSubsetError(
                "缺少 fonttools，无法子集化字体；请安装 fonttools[woff]"
            ) from exc

        options = ft_subset.Options()
        options.flavor = "woff2"
        options.desubroutinize = True
        options.hinting = False
        options.legacy_kern = False
        options.notdef_outline = True
        options.layout_features = ["*"]
        options.drop_tables += ["DSIG"]
        options.name_IDs = ["*"]
        options.name_legacy = False

        try:
            font = TTFont(str(self.source), fontNumber=0, lazy=True)
        except Exception as exc:
            raise FontSubsetError(f"源字体解析失败：{self.source}") from exc

        try:
            subsetter = ft_subset.Subsetter(options=options)
            subsetter.populate(text=charset)
            subsetter.subset(font)
            buffer = io.BytesIO()
            font.save(buffer)
            payload = buffer.getvalue()
        except Exception as exc:
            raise FontSubsetError(f"字体子集化失败：{exc}") from exc
        finally:
            font.close()

        if not payload:
            raise FontSubsetError("字体子集化返回空内容")
        return payload

    def _prune_disk(self) -> None:
        try:
            files = [
                (path, path.stat().st_mtime_ns)
                for path in self.cache_dir.glob("*.woff2")
                if path.is_file()
            ]
        except OSError:
            return
        if len(files) <= DISK_ENTRIES:
            return
        files.sort(key=lambda item: item[1])
        for path, _ in files[: len(files) - DISK_ENTRIES]:
            try:
                path.unlink()
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

    def log_unavailable_once(self, exc: Exception) -> None:
        """子集化不可用时只提醒一次，避免每次渲染刷日志。"""
        if self._unavailable_logged or not self.logger:
            return
        self._unavailable_logged = True
        self.logger.warning(
            f"字体子集化不可用，已回退到内嵌完整字体（请求体会明显变大）：{exc}"
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
