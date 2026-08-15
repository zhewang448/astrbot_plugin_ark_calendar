"""日历图片缓存管理。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

CalendarImageResult = tuple["Path | str", str, "dict[str, Any] | None"]


class CalendarImageManager:
    """日历图片的缓存查询、渲染、锁管理和降级处理。"""

    def __init__(self, render_cache, renderer, service, config, logger) -> None:
        self.render_cache = render_cache
        self.renderer = renderer
        self.service = service
        self.config = config
        self.logger = logger
        self._render_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._render_locks_guard = asyncio.Lock()

    # ── 配置读取 ──────────────────────────────────────────────

    def _value(self, key: str, default: Any) -> Any:
        from .config import config_value
        return config_value(self.config, "cache_and_render", key, default)

    def _int_value(self, key: str, default: int, *, minimum: int, maximum: int) -> int:
        from .config import config_int
        return config_int(self.config, "cache_and_render", key, default, minimum=minimum, maximum=maximum)

    def cache_enabled(self) -> bool:
        return bool(self._value("final_image_cache_enabled", True))

    def cache_max_age(self) -> int:
        requested = self._int_value("final_image_cache_max_age_minutes", 30, minimum=1, maximum=1440)
        return min(requested, max(1, int(self.service.cache_ttl().total_seconds() // 60)))

    def cache_keep_count(self) -> int:
        return self._int_value("final_image_cache_keep_count", 3, minimum=1, maximum=100)

    def fallback_max_age_hours(self) -> int:
        return self._int_value("fallback_max_age_hours", 12, minimum=1, maximum=168)

    def send_rendering_notice(self) -> bool:
        return bool(self._value("send_rendering_notice", True))

    def _display_config(self) -> dict[str, Any]:
        """构造影响最终图片内容的展示配置，用于缓存签名。"""
        from .config import config_value
        return {
            "timeline_days": self.service.timeline_days(),
            "template_hash": self.renderer.template_hash,
            "render_image_type": self._value("render_image_type", "png"),
            "render_device_scale_factor_level": self._value("render_device_scale_factor_level", "high"),
            "include_recent_operators": config_value(self.config, "basic", "include_recent_operators", True, "include_recent_operators"),
            "include_long_term": config_value(self.config, "basic", "include_long_term", True, "include_long_term"),
            "show_source_footer": config_value(self.config, "basic", "show_source_footer", True, "show_source_footer"),
            "pool_detail_cards": config_value(self.config, "basic", "pool_detail_cards", True, "pool_detail_cards"),
        }

    # ── 图片获取入口 ───────────────────────────────────────────

    def current_cached_image(self, display_config: dict[str, Any]) -> Path | None:
        """返回当前有效的最终图片缓存路径；不可用时返回 None。"""
        if not self.cache_enabled() or not self.service.last_snapshot or not self.service.snapshot_is_fresh():
            return None
        return self.render_cache.lookup(self.service.last_snapshot, display_config)

    async def get_calendar_image(self, snapshot, display_config: dict[str, Any]) -> CalendarImageResult:
        """获取日历图片：命中缓存直接返回，否则渲染并入缓存。"""
        if not self.cache_enabled():
            return await self._render(snapshot, display_config)
        cached = self.render_cache.lookup(snapshot, display_config)
        if cached:
            self.logger.info("最终日历图片缓存命中。")
            return cached, "cache", None
        signature = self.render_cache.signature(snapshot, display_config)
        lock = await self._retain_lock(signature)
        try:
            async with lock:
                cached = self.render_cache.lookup(snapshot, display_config)
                if cached:
                    self.logger.info("最终日历图片缓存由并发请求生成。")
                    return cached, "cache", None
                return await self._render(snapshot, display_config)
        finally:
            await self._release_lock(signature, lock)

    def fallback_notice(self, manifest: dict[str, Any] | None, messages) -> str:
        """依据实际发出图片的 manifest 生成降级提示。"""
        time_text = str((manifest or {}).get("snapshot_generated_at", "") or "未知")
        return f"{messages.text('cached_fallback_notice')}\n缓存数据时间：{time_text}"

    # ── 内部渲染 ───────────────────────────────────────────────

    async def _render(self, snapshot, display_config: dict[str, Any]) -> CalendarImageResult:
        started = time.monotonic()
        self.logger.info("最终日历图片缓存未命中，开始调用渲染器。")
        try:
            rendered = await self.renderer.calendar(snapshot)
            elapsed = time.monotonic() - started
            warning_seconds = self._int_value("slow_render_warning_seconds", 60, minimum=1, maximum=3600)
            if elapsed >= warning_seconds:
                self.logger.warning(f"方舟日历渲染耗时较长：{elapsed:.2f} 秒。")
            else:
                self.logger.info(f"方舟日历渲染完成，耗时 {elapsed:.2f} 秒。")
            if not self.cache_enabled():
                return rendered, "rendered", None
            cached = self.render_cache.store(
                rendered,
                snapshot,
                display_config,
                self.cache_max_age(),
                self.cache_keep_count(),
            )
            self.logger.info(f"最终日历图片已保存至插件缓存：{cached}")
            return cached, "rendered", None
        except Exception:
            fallback = self.render_cache.fallback(self.fallback_max_age_hours()) if self.cache_enabled() else None
            if fallback:
                image, manifest = fallback
                self.logger.warning(
                    f"日历渲染失败，已回退到缓存图片（快照时间：{manifest.get('snapshot_generated_at', '未知')}）。"
                )
                return image, "fallback", manifest
            raise

    # ── 渲染锁管理 ────────────────────────────────────────────

    async def _retain_lock(self, signature: str) -> asyncio.Lock:
        async with self._render_locks_guard:
            lock, refs = self._render_locks.get(signature, (asyncio.Lock(), 0))
            self._render_locks[signature] = (lock, refs + 1)
            return lock

    async def _release_lock(self, signature: str, lock: asyncio.Lock) -> None:
        async with self._render_locks_guard:
            current = self._render_locks.get(signature)
            if current is None or current[0] is not lock:
                return
            _, refs = current
            if refs <= 1:
                self._render_locks.pop(signature, None)
            else:
                self._render_locks[signature] = (lock, refs - 1)
