"""B站动态管理器，负责查询、推送和状态管理。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

if TYPE_CHECKING:
    from ..sources.bilibili_dynamic import BilibiliDynamicSource


class BilibiliDynamicManager:
    """B站动态管理器。

    职责：
    - 提供查询接口供命令调用
    - 定时检查新动态并推送到配置的会话
    - 维护推送状态（避免重复推送）
    """

    def __init__(
        self,
        source: BilibiliDynamicSource,
        context: Any,
        config: dict[str, Any],
    ) -> None:
        """初始化管理器。

        Args:
            source: 数据源实例
            context: 插件上下文（用于发送消息）
            config: 插件配置字典
        """
        self.source = source
        self.context = context
        self.config = config
        self._push_lock = asyncio.Lock()

    async def initialize_state(self) -> None:
        """插件加载/重载时初始化动态状态。

        拉取当前动态，保存到状态缓存中，标记为已知。这样可以避免：
        1. 首次启用时推送历史动态
        2. 插件重载时重复推送已推送过的动态
        """
        push_enabled = bool(self.config.get("bilibili_dynamic", {}).get("push_enabled", False))
        if not push_enabled:
            return

        try:
            # 拉取最近的动态（不下载图片，只获取元数据）
            dynamics = await self.source.recent_dynamics(limit=20, download_images=False)
            if dynamics:
                state = self.source.load_state()
                # 将当前动态标记为已知，但不标记为已推送
                if "dynamics" not in state:
                    state["dynamics"] = {}
                for dyn in dynamics:
                    dyn_id = dyn["id"]
                    if dyn_id not in state["dynamics"]:
                        state["dynamics"][dyn_id] = {
                            "title": dyn.get("title", ""),
                            "seen_at": datetime.now().isoformat(),
                            "pushed": False,
                        }
                state["last_update"] = datetime.now().isoformat()
                self.source.save_state(state)
                logger.info(
                    f"B站动态状态已初始化：记录 {len(dynamics)} 条当前动态，"
                    f"插件重载后新增的动态将在下次检查时推送。"
                )
        except Exception:
            logger.warning("B站动态状态初始化失败，不影响后续功能。", exc_info=True)

    def _list_default_count(self) -> int:
        """获取列表默认显示条数。"""
        value = self.config.get("bilibili_dynamic", {}).get("list_default_count", 5)
        return max(1, min(20, int(value)))

    def _push_targets(self) -> list[str]:
        """获取推送目标SID列表。"""
        raw = self.config.get("bilibili_dynamic", {}).get("target_sid_list", [])
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        if isinstance(raw, list):
            return [str(s).strip() for s in raw if str(s).strip()]
        return []

    def _max_images_per_message(self) -> int:
        """获取单条消息最多图片数。"""
        value = self.config.get("bilibili_dynamic", {}).get("max_images_per_message", 3)
        return max(1, min(9, int(value)))

    async def query_list(self, limit: int = 5) -> list[dict[str, Any]]:
        """查询最近的动态列表（不下载图片）。"""
        try:
            return await self.source.recent_dynamics(limit=limit, download_images=False)
        except Exception:
            logger.error("查询B站动态列表失败。", exc_info=True)
            return []

    async def query_detail(self, index: int, fallback_limit: int = 5) -> dict[str, Any] | None:
        """查询指定序号的动态详情（下载图片）。

        Args:
            index: 动态序号（从1开始）
            fallback_limit: 如果序号超出默认数量，拉取的最大条数

        Returns:
            动态字典，失败时返回 None
        """
        try:
            limit = max(index, fallback_limit)
            dynamics = await self.source.recent_dynamics(limit=limit, download_images=True)
            if index < 1 or index > len(dynamics):
                return None
            return dynamics[index - 1]
        except Exception:
            logger.error(f"查询B站动态详情（序号={index}）失败。", exc_info=True)
            return None

    async def check_and_push(self) -> tuple[int, int]:
        """检查新动态并推送到配置的目标会话。

        Returns:
            (成功推送数, 失败推送数)
        """
        if self._push_lock.locked():
            logger.debug("B站动态推送：已有任务正在执行，本次跳过。")
            return 0, 0

        async with self._push_lock:
            targets = self._push_targets()
            if not targets:
                logger.warning("B站动态推送：未配置 target_sid_list，跳过推送。")
                return 0, 0

            try:
                # 获取未推送的新动态（带图片）
                dynamics = await self.source.recent_dynamics(limit=10, download_images=True)
                state = self.source.load_state()
                new_dynamics = [
                    d for d in dynamics if not state.get("dynamics", {}).get(d["id"], {}).get("pushed", False)
                ]

                if not new_dynamics:
                    logger.debug("B站动态推送：没有新动态。")
                    return 0, 0

                logger.info(f"B站动态推送：检测到 {len(new_dynamics)} 条新动态，准备推送到 {len(targets)} 个会话。")

                sent_count = 0
                failed_count = 0

                for dynamic in new_dynamics:
                    dyn_id = dynamic["id"]
                    success_targets = []

                    for sid in targets:
                        try:
                            components = await self.build_message_components(dynamic, sid)
                            await self.context.send_message(sid, MessageChain(components))
                            success_targets.append(sid)
                            await asyncio.sleep(0.5)  # 限流
                        except Exception:
                            logger.error(f"B站动态推送失败：动态={dyn_id}，目标={sid}", exc_info=True)
                            failed_count += 1

                    if success_targets:
                        # 标记为已推送
                        if "dynamics" not in state:
                            state["dynamics"] = {}
                        if dyn_id not in state["dynamics"]:
                            state["dynamics"][dyn_id] = {}
                        state["dynamics"][dyn_id]["pushed"] = True
                        state["dynamics"][dyn_id]["pushed_at"] = datetime.now().isoformat()
                        self.source.save_state(state)
                        sent_count += 1

                logger.info(f"B站动态推送完成：成功 {sent_count} 条，失败 {failed_count} 条。")
                return sent_count, failed_count

            except Exception:
                logger.error("B站动态推送过程异常。", exc_info=True)
                return 0, 0

    async def force_push_recent(self, targets: list[str]) -> tuple[int, int]:
        """强制推送最近的动态（测试用，忽略已推送状态）。

        Args:
            targets: 目标SID列表

        Returns:
            (成功推送数, 失败推送数)
        """
        try:
            dynamics = await self.source.recent_dynamics(limit=1, download_images=True)
            if not dynamics:
                return 0, 0

            dynamic = dynamics[0]
            sent_count = 0
            failed_count = 0

            for sid in targets:
                try:
                    components = await self.build_message_components(dynamic, sid)
                    await self.context.send_message(sid, MessageChain(components))
                    sent_count += 1
                    await asyncio.sleep(0.5)
                except Exception:
                    logger.error(f"B站动态测试推送失败：目标={sid}", exc_info=True)
                    failed_count += 1

            return sent_count, failed_count

        except Exception:
            logger.error("B站动态测试推送异常。", exc_info=True)
            return 0, 0

    async def build_message_components(
        self,
        dynamic: dict[str, Any],
        target_sid: str,
    ) -> list[Comp.Plain | Comp.Image]:
        """构建消息组件列表。

        Args:
            dynamic: 动态字典
            target_sid: 目标会话ID

        Returns:
            消息组件列表
        """
        components: list[Comp.Plain | Comp.Image] = []

        # 构建头部文字
        icon_map = {"video": "🎬", "image": "🎨", "text": "📢", "repost": "🔄"}
        icon = icon_map.get(dynamic.get("dynamic_type", ""), "📝")

        header_lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            f"{icon} 明日方舟官方动态",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        title = dynamic.get("title", "")
        if title:
            header_lines.append(f"【{title}】")

        pub_date = dynamic.get("pub_date")
        if pub_date:
            time_str = self.source.format_relative_time(pub_date)
            header_lines.append(f"发布时间：{time_str}")

        header_lines.append("")

        # 描述文字
        description = dynamic.get("description_text", "")
        if description:
            max_len = 200
            if len(description) > max_len:
                description = description[:max_len] + "..."
            header_lines.append(description)
            header_lines.append("")

        components.append(Comp.Plain(text="\n".join(header_lines)))

        # 图片处理：使用已下载的本地缓存路径
        cached_images = dynamic.get("cached_images", [])
        max_images = self._max_images_per_message()

        if cached_images:
            display_images = cached_images[:max_images]
            for img_path in display_images:
                if Path(img_path).exists():
                    components.append(Comp.Image.fromFileSystem(img_path))

            # 底部提示
            footer_lines = []
            if len(cached_images) > max_images:
                footer_lines.append(f"📷 动态含 {len(cached_images)} 张图片，已展示前 {max_images} 张")

            link = dynamic.get("link", "")
            if link:
                footer_lines.append(f"🔗 查看完整动态：{link}")

            footer_lines.append("━━━━━━━━━━━━━━━━━━━━")

            if footer_lines:
                components.append(Comp.Plain(text="\n".join(footer_lines)))
        else:
            # 无图片，只加链接
            link = dynamic.get("link", "")
            if link:
                components.append(Comp.Plain(text=f"🔗 {link}\n━━━━━━━━━━━━━━━━━━━━"))

        return components
