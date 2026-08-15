"""帮助页生成与缓存管理。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .render_cache import HelpImageCache


class HelpManager:
    """帮助长图的缓存查询、渲染协调与文字版本生成。"""

    def __init__(
        self,
        help_cache: HelpImageCache,
        renderer,
        service,
        logger,
    ) -> None:
        self.help_cache = help_cache
        self.renderer = renderer
        self.service = service
        self.logger = logger
        self._help_render_locks = {mode: asyncio.Lock() for mode in HelpImageCache.MODES}

    async def get_help_image(self, mode: str) -> Path | str | None:
        """取当日缓存的帮助长图；未命中则渲染并写入缓存。

        帮助页内容按自然日变化（倒计时、可订阅日程），因此缓存以
        (mode, 日期) 为键，当天复用同一张图，跨日自动失效。
        失败时返回 None 由调用方回退到文字版。
        """
        cached = self.help_cache.lookup(mode)
        if cached:
            self.logger.info(f"帮助长图命中当日缓存：{mode}。")
            return cached
        lock = self._help_render_locks.get(mode)
        if lock is None:
            return await self._render_help_image(mode)
        async with lock:
            cached = self.help_cache.lookup(mode)
            if cached:
                self.logger.info(f"帮助长图缓存由并发请求生成：{mode}。")
                return cached
            return await self._render_help_image(mode)

    async def _render_help_image(
        self,
        mode: str,
        snapshot=None,
        user_commands=None,
        admin_commands=None,
        subscription_commands=None,
    ) -> Path | str | None:
        """实际调用渲染器并写入当日缓存；缓存写失败不影响本次返回。"""
        try:
            snapshot = snapshot or await self.service.snapshot()
            if mode == "subscribe":
                user_rows = command_rows(subscription_commands or [])
                admin_rows: list[dict[str, Any]] = []
            else:
                user_rows = command_rows(user_commands or [])
                admin_rows = command_rows(admin_commands or [])
            rendered = await self.renderer.help_page(
                snapshot,
                user_rows,
                admin_rows,
                mode=mode,
            )
        except Exception:
            self.logger.error("生成方舟帮助长图失败，已回退到文字版本。", exc_info=True)
            return None
        try:
            stored = self.help_cache.store(rendered, mode)
            if stored:
                self.logger.info(f"帮助长图已写入当日缓存：{stored}")
                return stored
        except Exception:
            self.logger.warning(f"帮助长图缓存写入失败，本次直接使用渲染结果：{mode}。", exc_info=True)
        return rendered


def command_rows(commands: tuple | list) -> list[dict[str, Any]]:
    """帮助页命令卡片数据；aliases 保持为列表，模板逐个渲染成标签。"""
    return [
        {
            "name": command.name,
            "aliases": list(command.aliases),
            "summary": command.summary,
            "argument_hint": command.argument_hint,
            "example": command.example,
        }
        for command in commands
    ]


def generate_help_text(user_commands: tuple, admin_commands: tuple, help_command_name: str) -> str:
    """由命令定义生成帮助文本，使别名只需在一处维护。"""
    sections = [
        "罗德岛行动日历 · 使用说明",
        "【普通指令】\n" + "\n\n".join(spec.help_entry() for spec in user_commands),
        "【管理员指令】\n" + "\n\n".join(spec.help_entry() for spec in admin_commands),
        "【自动日报】\n请在插件配置的「自动方舟日报」中启用任务，填写星期、发送时间和目标 SID。",
        "【自动生日祝贺】\n"
        "可单独设置每日发送时间和目标 SID；当天没有干员生日时不会发送。\n"
        "目标 SID 和管理员 SID 可在对应会话发送 /sid 获取。",
        f"博士如果只想查看帮助，发送 /{help_command_name} 就可以了喵～",
    ]
    return "\n\n".join(sections)
