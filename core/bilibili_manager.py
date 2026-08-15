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
        """插件加载/重载时建立动态基线。

        首次运行（状态缓存里没有 baseline_established 标记）时，把当前拉到的动态
        全部记为已推送：它们是安装前就存在的历史动态，不应该在第一次定时检查时发出去。
        基线建立之后，只有此刻还没出现过的动态才会被判为新动态并推送。

        不受 push_enabled 影响：用户先装插件、之后才开推送时，基线必须已经存在，
        否则开启那一刻会把整页历史动态一次性发出。
        """
        try:
            state = self.source.load_state()
            if not isinstance(state.get("dynamics"), dict):
                state["dynamics"] = {}
            baseline_established = bool(state.get("baseline_established"))

            # 拉取最近的动态（不下载图片，只获取元数据）
            dynamics = await self.source.recent_dynamics(limit=20, download_images=False)
            if not dynamics:
                # 拉取失败时不要写基线标记，留到下次重载再建，避免把空基线固化下来。
                logger.warning("B站动态基线未建立：本次未取到任何动态，将在下次重载时重试。")
                return

            now_text = datetime.now().isoformat()
            for dyn in dynamics:
                dyn_id = dyn["id"]
                if dyn_id in state["dynamics"]:
                    continue
                state["dynamics"][dyn_id] = {
                    "title": dyn.get("title", ""),
                    "seen_at": now_text,
                    # 首次建立基线：历史动态直接视为已推送，之后的新动态才推。
                    # 基线已存在时说明这是重载，此处新出现的动态是真·新动态，留给定时任务推。
                    "pushed": not baseline_established,
                }
            state["last_update"] = now_text
            state["baseline_established"] = True
            self.source.save_state(state)

            if baseline_established:
                logger.info(f"B站动态状态已刷新：已知 {len(state['dynamics'])} 条，新增动态将在下次检查时推送。")
            else:
                logger.info(
                    f"B站动态基线已建立：{len(dynamics)} 条现有动态记为历史、不会推送，"
                    "此后新发布的动态才会推送。"
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
    ) -> list:
        """构建消息组件列表。

        Args:
            dynamic: 动态字典
            target_sid: 目标会话ID

        Returns:
            消息组件列表，QQ平台含多图时返回 [Nodes]，其他平台返回 [Plain, Image, ...]
        """
        components: list = []

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

        header_text = "\n".join(header_lines)

        # 图片处理：使用已下载的本地缓存路径
        cached_images = dynamic.get("cached_images", [])
        max_images = self._max_images_per_message()

        if cached_images:
            display_images = cached_images[:max_images]

            # 底部提示
            footer_lines = []
            if len(cached_images) > max_images:
                footer_lines.append(f"📷 动态含 {len(cached_images)} 张图片，已展示前 {max_images} 张")

            link = dynamic.get("link", "")
            if link:
                footer_lines.append(f"🔗 查看完整动态：{link}")

            footer_lines.append("━━━━━━━━━━━━━━━━━━━━")
            footer_text = "\n".join(footer_lines)

            # 检测平台：aiocqhttp 使用合并转发，避免刷屏
            from ..core.platform_utils import split_sid
            parsed = split_sid(target_sid)
            platform_id = parsed[0] if parsed else ""

            # 尝试从 context 获取平台类型
            use_forward = False
            try:
                platform_inst = self.context.get_platform_inst(platform_id)
                if platform_inst:
                    platform_name = platform_inst.meta().name
                    # aiocqhttp 且图片数量 > 1 时使用合并转发
                    use_forward = platform_name == "aiocqhttp" and len(display_images) > 1
            except Exception:
                pass

            if use_forward:
                # QQ合并转发：每张图一个节点
                try:
                    from astrbot.core.message.components import Node, Nodes
                    nodes = []
                    # 第一个节点：标题 + 第一张图
                    first_content = [Comp.Plain(text=header_text)]
                    if display_images and Path(display_images[0]).exists():
                        first_content.append(Comp.Image.fromFileSystem(display_images[0]))
                    nodes.append(Node(content=first_content, name="明日方舟官方", uin="161775300"))

                    # 后续图片各一个节点
                    for img_path in display_images[1:]:
                        if Path(img_path).exists():
                            nodes.append(Node(
                                content=[Comp.Image.fromFileSystem(img_path)],
                                name="明日方舟官方",
                                uin="161775300"
                            ))

                    # 最后一个节点：底部提示
                    nodes.append(Node(content=[Comp.Plain(text=footer_text)], name="明日方舟官方", uin="161775300"))

                    return [Nodes(nodes=nodes)]
                except Exception:
                    # 合并转发构建失败，回退到普通消息
                    pass

            # 普通消息：逐个发送
            components.append(Comp.Plain(text=header_text))
            for img_path in display_images:
                if Path(img_path).exists():
                    components.append(Comp.Image.fromFileSystem(img_path))
            components.append(Comp.Plain(text=footer_text))
        else:
            # 无图片，只加链接
            link = dynamic.get("link", "")
            if link:
                components.append(Comp.Plain(text=f"{header_text}🔗 {link}\n━━━━━━━━━━━━━━━━━━━━"))
            else:
                components.append(Comp.Plain(text=header_text + "━━━━━━━━━━━━━━━━━━━━"))

        return components
