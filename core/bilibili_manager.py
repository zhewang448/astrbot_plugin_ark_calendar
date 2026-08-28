"""B站动态管理器，负责查询、推送和状态管理。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

from .bilibili_media import extract_bilibili_video_url
from .parser_bridge import fetch_video_path
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
        require_baseline: bool = False,
        notification_manager: Any | None = None,
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
        self.notification_manager = notification_manager
        self._push_lock = asyncio.Lock()
        self._baseline_ready = not require_baseline

    async def initialize_state(self) -> bool:
        """插件加载/重载时建立动态基线。

        首次加载会把最近 20 条动态设为历史，不发送。后续首次检测到的动态会
        固定当时的目标 SID；之后新增或重新加入的 SID 不会补收这条旧动态。
        """
        self._baseline_ready = False
        try:
            state = self.source.load_state()
            baseline_established = bool(state.get("baseline_established"))
            push_enabled = bool(self.config.get("bilibili_dynamic", {}).get("push_enabled", False))
            previous_push_enabled = bool(
                state.get("push_enabled", state.get("push_ever_enabled", push_enabled))
            )
            records = self._normalized_records(state)
            targets = self._push_targets()

            # 拉取最近的动态（不下载图片，只获取元数据）
            dynamics = await self.source.recent_dynamics(limit=20, download_images=False)
            if not dynamics:
                # 拉取失败时不要写基线标记，留到下次重载再建，避免把空基线固化下来。
                logger.warning("B站动态基线未建立：本次未取到任何动态，将在下次重载时重试。")
                return False

            queue_new_dynamics = baseline_established and previous_push_enabled and push_enabled
            push_types = self._push_types()
            # 早期 v0.9.1 曾把重载期间的新动态记为 pushed=False、但未冻结目标；
            # 升级时仅迁移这类未完成记录，之后写回最小状态格式。
            for dyn_id, record in records.items():
                if record.get("state") == "pending":
                    records[dyn_id] = (
                        {"targets": {sid: False for sid in targets}}
                        if queue_new_dynamics and targets
                        else {"state": "ignored"}
                    )
            for dyn in dynamics:
                dyn_id = str(dyn["id"])
                if dyn_id in records:
                    continue
                records[dyn_id] = self._new_record(
                    dyn,
                    targets,
                    queue_new_dynamics,
                    push_types,
                )

            if not push_enabled:
                # 停用期间的待投递项也属于历史，重新启用时不补发。
                records = {dyn_id: {"state": "ignored"} for dyn_id in records}

            self._remove_unsubscribed_targets(records, targets)
            state["dynamics"] = records
            state["baseline_established"] = True
            state["push_enabled"] = push_enabled
            state.pop("last_update", None)
            state.pop("push_ever_enabled", None)
            self.source.save_state(state)
            self._baseline_ready = True

            if baseline_established:
                logger.info(f"B站动态状态已刷新：已知 {len(records)} 条。")
            else:
                logger.info(
                    f"B站动态基线已建立：{len(dynamics)} 条现有动态记为历史、不会推送，"
                    "此后新发布的动态才会推送。"
                )
            return True
        except Exception:
            logger.warning("B站动态状态初始化失败，不影响后续功能。", exc_info=True)
            return False

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

    @staticmethod
    def _normalized_records(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """将旧版状态迁移为最小记录：历史/过滤状态或固定目标投递表。"""
        raw_records = state.get("dynamics")
        if not isinstance(raw_records, dict):
            return {}

        records: dict[str, dict[str, Any]] = {}
        for dyn_id, raw_record in raw_records.items():
            if not isinstance(raw_record, dict):
                records[str(dyn_id)] = {"state": "ignored"}
                continue
            state_name = raw_record.get("state")
            if state_name in {"ignored", "suppressed", "pending"}:
                records[str(dyn_id)] = {"state": state_name}
                continue

            raw_targets = raw_record.get("targets")
            if isinstance(raw_targets, dict):
                targets = {
                    str(sid).strip(): bool(delivered)
                    for sid, delivered in raw_targets.items()
                    if str(sid).strip()
                }
                records[str(dyn_id)] = {"targets": targets} if targets else {"state": "ignored"}
                continue

            # 兼容 v0.9.1 早期的 delivered_to / eligible_targets 状态，并在本次保存时删除冗余字段。
            delivered_to = raw_record.get("delivered_to")
            delivered = delivered_to if isinstance(delivered_to, dict) else {}
            eligible_targets = raw_record.get("eligible_targets")
            if isinstance(eligible_targets, list):
                targets = {
                    str(sid).strip(): str(sid).strip() in delivered
                    for sid in eligible_targets
                    if str(sid).strip()
                }
            else:
                targets = {str(sid).strip(): True for sid in delivered if str(sid).strip()}
            if targets:
                records[str(dyn_id)] = {"targets": targets}
            elif raw_record.get("pushed") is False:
                records[str(dyn_id)] = {"state": "pending"}
            else:
                records[str(dyn_id)] = {"state": "ignored"}
        return records

    def _new_record(
        self,
        dynamic: dict[str, Any],
        targets: list[str],
        queue_new_dynamics: bool,
        push_types: list[str],
    ) -> dict[str, Any]:
        """为首次检测到的动态冻结状态和可投递目标。"""
        if not queue_new_dynamics or not targets:
            return {"state": "ignored"}
        if not self.source.should_push(dynamic, push_types):
            return {"state": "suppressed"}
        return {"targets": {sid: False for sid in targets}}

    @staticmethod
    def _remove_unsubscribed_targets(records: dict[str, dict[str, Any]], targets: list[str]) -> None:
        """移除已退订目标，使其重新加入后不会补收旧动态。"""
        active_targets = set(targets)
        for record in records.values():
            pending_targets = record.get("targets")
            if not isinstance(pending_targets, dict):
                continue
            for sid in list(pending_targets):
                if sid not in active_targets:
                    pending_targets.pop(sid)
            if not pending_targets:
                record.clear()
                record["state"] = "ignored"

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
            if not self._baseline_ready:
                logger.info("B站动态基线尚未建立，本次仅建立基线，不投递历史动态。")
                await self.initialize_state()
                return 0, 0
            try:
                # 先获取元数据，过滤后再下载真正需要投递的图片。
                dynamics = await self.source.recent_dynamics(limit=20, download_images=False)
                state = self.source.load_state()
                records = self._normalized_records(state)
                targets = self._push_targets()
                push_enabled = bool(self.config.get("bilibili_dynamic", {}).get("push_enabled", False))
                new_dynamics: list[dict[str, Any]] = []
                push_types = self._push_types()
                for dynamic in dynamics:
                    dyn_id = str(dynamic["id"])
                    record = records.get(dyn_id)
                    if record is None:
                        record = self._new_record(dynamic, targets, push_enabled, push_types)
                        records[dyn_id] = record
                    pending_targets = record.get("targets")
                    if isinstance(pending_targets, dict) and any(not sent for sent in pending_targets.values()):
                        new_dynamics.append(dynamic)

                if not push_enabled:
                    records = {dyn_id: {"state": "ignored"} for dyn_id in records}
                    new_dynamics = []
                self._remove_unsubscribed_targets(records, targets)
                state["dynamics"] = records
                state["baseline_established"] = True
                state["push_enabled"] = push_enabled
                state.pop("last_update", None)
                state.pop("push_ever_enabled", None)
                self.source.save_state(state)
                if not new_dynamics:
                    logger.debug("B站动态推送：没有新动态。")
                    return 0, 0

                logger.info(f"B站动态推送：检测到 {len(new_dynamics)} 条新动态，准备推送到 {len(targets)} 个会话。")

                sent_count = 0
                failed_count = 0

                for dynamic in new_dynamics:
                    dyn_id = str(dynamic["id"])
                    record = records[dyn_id]
                    hydrate_images = getattr(self.source, "hydrate_images", None)
                    if hydrate_images:
                        dynamic = await hydrate_images(dynamic)
                    pending_targets = record.get("targets", {})
                    parser_video_path = await self._get_parser_video_path(dynamic, pending_targets)
                    video_failures: list[str] = []
                    for sid, delivered in pending_targets.items():
                        if delivered:
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
                            await self._send_parser_video(
                                dynamic, sid, parser_video_path, video_failures
                            )
                            pending_targets[sid] = True
                            sent_count += 1
                            await asyncio.sleep(0.5)  # 限流
                        except Exception:
                            logger.error(f"B站动态推送失败：动态={dyn_id}，目标={sid}", exc_info=True)
                            failed_count += 1

                    if video_failures:
                        await self._notify_video_send_failed(dynamic, video_failures)
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

    def _video_via_parser_enabled(self) -> bool:
        """读取“视频动态额外发送视频文件”开关。"""
        return bool(
            self.config.get("bilibili_dynamic", {}).get("send_video_via_parser", False)
        )

    async def _get_parser_video_path(
        self,
        dynamic: dict[str, Any],
        pending_targets: Any,
    ):
        """同一条视频动态只解析下载一次，多个目标复用本地文件。"""
        if not pending_targets or not self._video_via_parser_enabled():
            return None
        if dynamic.get("dynamic_type") != "video":
            return None

        video_url = extract_bilibili_video_url(str(dynamic.get("description_html", "") or ""))
        if not video_url:
            logger.debug("B站视频动态未提取到可解析的视频链接。")
            return None
        return await fetch_video_path(self.context, video_url)

    async def build_parser_video_components(self, dynamic: dict[str, Any], target_sid: str) -> list:
        """手动查询时构建 parser 视频组件；未开启或解析失败返回空列表。"""
        if not self._video_via_parser_enabled() or dynamic.get("dynamic_type") != "video":
            return []

        video_path = await self._get_parser_video_path(dynamic, [target_sid])
        if video_path is None:
            logger.warning("B站动态查询视频解析失败：目标=%s", target_sid)
            return []

        try:
            return [Comp.Video.fromFileSystem(str(video_path))]
        except Exception:
            logger.warning("B站动态查询视频组件构造失败：目标=%s", target_sid, exc_info=True)
            return []

    async def _send_parser_video(
        self,
        dynamic: dict[str, Any],
        target_sid: str,
        video_path,
        failures: list[str],
    ) -> None:
        """在图文推送成功后尝试单独发送视频；失败不回滚图文。"""
        if video_path is None:
            if self._video_via_parser_enabled() and dynamic.get("dynamic_type") == "video":
                if target_sid not in failures:
                    failures.append(target_sid)
            return
        try:
            dispatched = await self.context.send_message(
                target_sid,
                MessageChain([Comp.Video.fromFileSystem(str(video_path))]),
            )
            if dispatched is False:
                logger.warning("B站动态视频未投递：目标=%s", target_sid)
                if target_sid not in failures:
                    failures.append(target_sid)
        except Exception:
            logger.warning("B站动态视频发送失败：目标=%s", target_sid, exc_info=True)
            if target_sid not in failures:
                failures.append(target_sid)

    async def _notify_video_send_failed(
        self,
        dynamic: dict[str, Any],
        failures: list[str],
    ) -> None:
        """按动态聚合上报视频失败，避免多群失败重复刷屏。"""
        if not self.notification_manager:
            return
        unique_targets = list(dict.fromkeys(str(sid) for sid in failures if str(sid)))
        if not unique_targets:
            return
        title = str(dynamic.get("title", "") or "官方动态").strip() or "官方动态"
        await self.notification_manager.notify(
            "【B站动态视频发送失败】\n"
            f"动态：{title}\n"
            f"失败目标：{'、'.join(unique_targets)}\n"
            "图文推送已保留；请检查 astrbot_plugin_parser 及其 B站解析器配置。",
            "bilibili_video_send_failed",
        )

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
