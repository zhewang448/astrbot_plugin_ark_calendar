"""B站动态管理器，负责查询、推送和状态管理。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

from .platform_utils import platform_supports_proactive_send

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
        renderer: Any | None = None,
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
        self.renderer = renderer
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
            push_enabled = bool(self.config.get("bilibili_dynamic", {}).get("push_enabled", False))
            # 旧状态没有该字段时，按当前配置迁移，避免已启用实例重载后误丢新动态。
            push_ever_enabled = bool(state.get("push_ever_enabled", push_enabled))

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
                    # 未启用期间发现的动态属于历史；启用后重载期间发现的动态才进入推送队列。
                    "pushed": not baseline_established or not push_enabled or not push_ever_enabled,
                }
            if push_enabled and not push_ever_enabled and baseline_established:
                # disabled -> enabled 的首次切换建立新的发送基线，避免补发停用期间的历史动态。
                for dyn in dynamics:
                    state["dynamics"].setdefault(dyn["id"], {})["pushed"] = True
                logger.info("B站动态首次启用：已将当前动态设为历史基线，不补发停用期间内容。")
            state["push_ever_enabled"] = push_ever_enabled or push_enabled
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

    def _render_image_count_threshold(self) -> int:
        """图片数不超过此值时，将文字与全部图片绘制进一张终端图。"""
        value = self.config.get("bilibili_dynamic", {}).get("render_image_count_threshold", 1)
        try:
            return max(0, min(9, int(value)))
        except (TypeError, ValueError, OverflowError):
            return 1

    def _push_types(self) -> list[str]:
        """获取允许自动推送的动态类型。空列表表示不做类型过滤。"""
        raw = self.config.get("bilibili_dynamic", {}).get("push_types", ["video", "image", "text"])
        if isinstance(raw, str):
            raw = raw.split(",")
        if not isinstance(raw, list):
            return []
        return [str(item).strip().lower() for item in raw if str(item).strip()]

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
                # 先获取元数据，过滤后再下载真正需要投递的图片。
                dynamics = await self.source.recent_dynamics(limit=10, download_images=False)
                state = self.source.load_state()
                records = state.get("dynamics", {})
                new_dynamics = []
                push_types = self._push_types()
                for dynamic in dynamics:
                    record = records.get(dynamic["id"], {})
                    if not self.source.should_push(dynamic, push_types):
                        record.update({
                            "title": dynamic.get("title", ""),
                            "pushed": True,
                            "suppressed": True,
                            "suppressed_type": dynamic.get("dynamic_type", ""),
                            "suppressed_at": datetime.now().isoformat(),
                        })
                        records[dynamic["id"]] = record
                        continue
                    if record.get("suppressed"):
                        # 配置后来放开该类型时，允许重新进入投递队列。
                        record.pop("suppressed", None)
                        record.pop("suppressed_type", None)
                        record.pop("suppressed_at", None)
                        record["pushed"] = False
                    delivered_to = record.get("delivered_to")
                    if not isinstance(delivered_to, dict):
                        delivered_to = {}
                        record["delivered_to"] = delivered_to
                    eligible_targets = record.get("eligible_targets")
                    if not isinstance(eligible_targets, list):
                        eligible_targets = list(targets)
                        record["eligible_targets"] = eligible_targets
                    if record.get("pushed") or all(sid in delivered_to for sid in eligible_targets):
                        continue
                    new_dynamics.append(dynamic)

                self.source.save_state(state)
                if not new_dynamics:
                    logger.debug("B站动态推送：没有新动态。")
                    return 0, 0

                logger.info(f"B站动态推送：检测到 {len(new_dynamics)} 条新动态，准备推送到 {len(targets)} 个会话。")

                sent_count = 0
                failed_count = 0

                for dynamic in new_dynamics:
                    dyn_id = dynamic["id"]
                    record = state.setdefault("dynamics", {}).setdefault(dyn_id, {})
                    hydrate_images = getattr(self.source, "hydrate_images", None)
                    if hydrate_images:
                        dynamic = await hydrate_images(dynamic)
                    delivered_to = record.get("delivered_to")
                    if not isinstance(delivered_to, dict):
                        delivered_to = {}
                        record["delivered_to"] = delivered_to

                    for sid in targets:
                        if sid in delivered_to:
                            continue
                        if not platform_supports_proactive_send(sid, self.context):
                            failed_count += 1
                            logger.warning(f"B站动态不支持主动投递：动态={dyn_id}，目标={sid}")
                            continue
                        try:
                            components = await self.build_message_components(dynamic, sid)
                            dispatched = await self.context.send_message(sid, MessageChain(components))
                            if dispatched is False:
                                failed_count += 1
                                logger.warning(f"B站动态未投递：动态={dyn_id}，目标={sid}")
                                continue
                            await self._send_forward_images(dynamic, sid)
                            delivered_to[sid] = datetime.now().isoformat()
                            sent_count += 1
                            await asyncio.sleep(0.5)  # 限流
                        except Exception:
                            logger.error(f"B站动态推送失败：动态={dyn_id}，目标={sid}", exc_info=True)
                            failed_count += 1

                    eligible_targets = record.get("eligible_targets") or targets
                    record["eligible_targets"] = list(eligible_targets)
                    record["pushed"] = all(sid in delivered_to for sid in eligible_targets)
                    if record["pushed"]:
                        record["pushed_at"] = datetime.now().isoformat()
                    self.source.save_state(state)

                logger.info(f"B站动态推送完成：成功 {sent_count} 次投递，失败 {failed_count} 次。")
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
                if not platform_supports_proactive_send(sid, self.context):
                    failed_count += 1
                    logger.warning(f"B站动态测试推送不支持主动投递：目标={sid}")
                    continue
                try:
                    components = await self.build_message_components(dynamic, sid)
                    dispatched = await self.context.send_message(sid, MessageChain(components))
                    if dispatched is False:
                        failed_count += 1
                        logger.warning(f"B站动态测试推送未投递：目标={sid}")
                        continue
                    await self._send_forward_images(dynamic, sid)
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
            普通消息链组件列表；超阈值原图转发由 build_forward_components() 单独构造。
        """
        cached_images = [str(path) for path in dynamic.get("cached_images", []) if Path(path).is_file()]
        declared_count = len(dynamic.get("images") or cached_images)
        threshold = self._render_image_count_threshold()
        # 兼容未注入渲染器的第三方调用方；插件主流程始终注入 CalendarRenderer。
        if self.renderer is None:
            link = str(dynamic.get("link", "") or "")
            components = [Comp.Plain(text=self._fallback_text(dynamic, ""))]
            components.extend(Comp.Image.fromFileSystem(image) for image in cached_images)
            if link:
                components.append(Comp.Plain(text=f"查看完整动态：{link}"))
            return components
        renderable = declared_count <= threshold and self.renderer is not None

        rendered = None
        if renderable:
            try:
                rendered = await self.renderer.bilibili_dynamic(dynamic, include_images=True)
            except Exception:
                logger.warning("B站动态图片渲染失败，回退到文字链。", exc_info=True)
        if rendered is None and self.renderer is not None and declared_count > threshold:
            try:
                rendered = await self.renderer.bilibili_dynamic(dynamic, include_images=False)
            except Exception:
                logger.warning("B站动态文字图片渲染失败，回退到文字链。", exc_info=True)

        link = str(dynamic.get("link", "") or "")
        link_text = f"查看完整动态：{link}" if link else ""
        components: list = []
        if rendered is not None and isinstance(rendered, (str, Path)) and Path(str(rendered)).is_file():
            # 链接与渲染图片处在同一条消息链中，阅读顺序固定为图片、链接。
            components.append(Comp.Image.fromFileSystem(str(rendered)))
            if link_text:
                components.append(Comp.Plain(text=link_text))
        else:
            components.append(Comp.Plain(text=self._fallback_text(dynamic, link_text)))
            if declared_count <= threshold:
                # 渲染服务短暂不可用时仍保留动态原图，避免小图动态丢失内容。
                components.extend(Comp.Image.fromFileSystem(image) for image in cached_images)

        return components

    async def build_forward_components(self, dynamic: dict[str, Any], target_sid: str) -> list:
        """构建超阈值动态的原图合并转发，绝不混入普通消息链。"""
        cached_images = [str(path) for path in dynamic.get("cached_images", []) if Path(path).is_file()]
        declared_count = len(dynamic.get("images") or cached_images)
        if (
            declared_count <= self._render_image_count_threshold()
            or not cached_images
            or not self._supports_forward(target_sid)
        ):
            return []
        try:
            from astrbot.core.message.components import Node, Nodes
            nodes = [
                Node(
                    content=[Comp.Image.fromFileSystem(image)],
                    name="明日方舟官方",
                    uin="161775300",
                )
                for image in cached_images
            ]
            return [Nodes(nodes=nodes)]
        except Exception:
            logger.debug("当前平台未能构造动态图片合并转发。", exc_info=True)
            return []

    async def _send_forward_images(self, dynamic: dict[str, Any], target_sid: str) -> None:
        """在普通消息链投递成功后，单独投递原图转发；失败不回滚正文。"""
        try:
            components = await self.build_forward_components(dynamic, target_sid)
            if not components:
                return
            dispatched = await self.context.send_message(target_sid, MessageChain(components))
            if dispatched is False:
                logger.warning("B站动态原图合并转发未投递：目标=%s", target_sid)
        except Exception:
            logger.warning("B站动态原图合并转发失败：目标=%s", target_sid, exc_info=True)

    async def build_list_components(self, dynamics: list[dict[str, Any]]) -> list:
        """将动态列表渲染为图片，失败时回退为简洁文本。"""
        if self.renderer is not None:
            try:
                rendered = await self.renderer.bilibili_dynamic_list(dynamics)
                if isinstance(rendered, (str, Path)) and Path(str(rendered)).is_file():
                    return [Comp.Image.fromFileSystem(str(rendered))]
            except Exception:
                logger.warning("B站动态列表图片渲染失败，回退到文字版。", exc_info=True)
        lines = ["明日方舟官方B站动态", ""]
        for index, dynamic in enumerate(dynamics, 1):
            lines.append(f"{index}. {dynamic.get('title', '')}")
        return [Comp.Plain(text="\n".join(lines))]

    def _supports_forward(self, target_sid: str) -> bool:
        if not bool(self.config.get("bilibili_dynamic", {}).get("use_forward_on_qq", True)):
            return False
        try:
            from .platform_utils import split_sid
            parsed = split_sid(target_sid)
            platform_inst = self.context.get_platform_inst(parsed[0] if parsed else "")
            return bool(platform_inst and platform_inst.meta().name == "aiocqhttp")
        except Exception:
            return False

    @staticmethod
    def _fallback_text(dynamic: dict[str, Any], link_text: str) -> str:
        title = str(dynamic.get("title", "") or "官方动态")
        description = str(dynamic.get("description_text", "") or "").strip()
        if len(description) > 800:
            description = description[:800] + "..."
        parts = [f"【明日方舟官方动态】\n{title}"]
        if description:
            parts.append(description)
        if link_text:
            parts.append(link_text)
        return "\n\n".join(parts)
