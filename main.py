from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import astrbot.api.message_components as Comp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .core.command_args import split_name_and_time, strip_command_prefix
from .core.config import config_int, config_strings, config_value, sync_builtin_message_previews
from .core.help_manager import HelpManager, generate_help_text
from .core.image_cache_manager import CalendarImageManager
from .core.messages import MessageCatalog
from .core.models import parse_iso
from .core.notification_manager import NotificationManager
from .core.platform_utils import is_group_session, platform_supports_at, platform_supports_proactive_send
from .core.render_cache import CalendarImageCache, HelpImageCache
from .core.renderer import CalendarRenderer
from .core.scheduler_utils import normalize_weekdays, parse_schedule_times
from .core.service import CalendarService
from .core.status_formatter import (
    birthday_details,
    data_quality_notice,
    format_status,
    parse_historical_day,
)
from .core.subscription import Subscription, SubscriptionManager
from .core.bilibili_manager import BilibiliDynamicManager
from .core.recruitment_calculator import RecruitmentCalculator, format_result
from .sources.bilibili_dynamic import BilibiliDynamicSource
from .sources.recruitment import RecruitmentSource

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class CommandSpec:
    """命令名称、别名与帮助条目的唯一来源。"""

    name: str
    aliases: tuple[str, ...] = ()
    summary: str = ""
    argument_hint: str = ""
    example: str = ""

    @property
    def alias_set(self) -> set[str]:
        return set(self.aliases)

    @property
    def invocations(self) -> tuple[str, ...]:
        """命令名与全部别名，用于从消息原文里剥掉触发词。"""
        return (self.name, *self.aliases)

    def help_entry(self) -> str:
        invocation = f"/{self.name}"
        if self.argument_hint:
            invocation = f"{invocation} {self.argument_hint}"
        if self.aliases:
            alias_text = "、".join(f"/{alias}" for alias in self.aliases)
            invocation = f"{invocation}（别名：{alias_text}）"
        return f"{invocation}\n{self.summary}"


# 指令定义顺序与 README 的“指令与别称”表保持一致：普通指令在前，管理员指令在后。
CALENDAR_COMMAND = CommandSpec(
    "方舟日历",
    ("方舟日报", "明日方舟日历", "舟日历"),
    "生成活动、寻访、生日和今日作战信息长图；命中图片缓存时会直接发送。",
    example="/方舟日历",
)
BIRTHDAY_COMMAND = CommandSpec(
    "方舟生日",
    ("方舟生日查询", "明日方舟生日", "舟生日"),
    "以文字查询干员生日，例如：/方舟生日 卡缇。",
    argument_hint="<干员名称>",
    example="/方舟生日 卡缇",
)
STATUS_COMMAND = CommandSpec(
    "方舟日历状态",
    ("方舟状态", "明日方舟日历状态"),
    "查看最近快照、数据源、降级状态和最终图片缓存。",
    example="/方舟日历状态",
)
SUBSCRIBE_COMMAND = CommandSpec(
    "方舟订阅",
    ("订阅方舟活动", "订阅卡池"),
    "订阅活动或卡池，在结束前一天提醒。",
    argument_hint="<活动/卡池名称> [提醒时间]",
    example="/方舟订阅 危机合约 · 熔火行动 20:30",
)
UNSUBSCRIBE_COMMAND = CommandSpec(
    "方舟取消订阅",
    ("取消订阅方舟", "取消订阅卡池"),
    "取消订阅活动或卡池。",
    argument_hint="<活动/卡池名称>",
    example="/方舟取消订阅 危机合约 · 熔火行动",
)
SUBSCRIPTION_LIST_COMMAND = CommandSpec(
    "方舟订阅列表",
    ("我的方舟订阅", "查看订阅"),
    "查看当前订阅的所有活动和卡池。",
    example="/方舟订阅列表",
)
BILIBILI_DYNAMIC_COMMAND = CommandSpec(
    "方舟动态",
    ("B站动态", "官方动态", "方舟B站"),
    "查看明日方舟官方B站最新动态；列表和详情均以终端图片返回，可指定编号查看详情。",
    argument_hint="[编号]",
    example="/方舟动态 3",
)
BILIBILI_DYNAMIC_TEST_COMMAND = CommandSpec(
    "方舟动态推送测试",
    ("测试动态推送",),
    "模拟检测到新动态并推送（管理员测试用）。",
    example="/方舟动态推送测试",
)
RECRUIT_COMMAND = CommandSpec(
    "方舟公招",
    ("公招计算", "明日方舟公招", "舟公招"),
    "输入标签计算可能招募的干员及保底星级，并以招募终端图片返回结果。",
    argument_hint="<标签1> [标签2] [标签3]",
    example="/方舟公招 近卫干员 输出 生存",
)
HELP_COMMAND = CommandSpec(
    "方舟日历帮助",
    ("方舟日报帮助", "明日方舟日报帮助"),
    "查看本帮助。",
    example="/方舟日历帮助",
)
REFRESH_COMMAND = CommandSpec(
    "方舟日历刷新",
    ("方舟日历更新", "方舟日报刷新"),
    "强制刷新数据源并重新生成日历图片。",
    example="/方舟日历刷新",
)
HISTORICAL_COMMAND = CommandSpec(
    "方舟历史日程测试",
    ("方舟回溯测试", "方舟日历历史测试"),
    "按指定日期生成与正常日报相同布局的历史测试图片，时间轴长度跟随配置；例如：/方舟历史日程测试 2026-07-01。",
    argument_hint="<日期>",
    example="/方舟历史日程测试 2026-07-01",
)

SUBSCRIPTION_COMMANDS = (SUBSCRIBE_COMMAND, UNSUBSCRIBE_COMMAND, SUBSCRIPTION_LIST_COMMAND)
USER_COMMANDS = (CALENDAR_COMMAND, BIRTHDAY_COMMAND, STATUS_COMMAND, *SUBSCRIPTION_COMMANDS, BILIBILI_DYNAMIC_COMMAND, RECRUIT_COMMAND, HELP_COMMAND)
ADMIN_COMMANDS = (REFRESH_COMMAND, HISTORICAL_COMMAND, BILIBILI_DYNAMIC_TEST_COMMAND)


class ArkCalendarPlugin(Star):
    # 明日方舟服务器日切时间，活动与卡池基本都在此刻结束。
    GAME_DAILY_RESET = (4, 0)
    # 预缓存与定时日报撞车时的顺延步长与最大尝试次数。
    PRECACHE_SHIFT_MINUTES = 10
    PRECACHE_SHIFT_ATTEMPTS = 6

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).resolve().parent
        if sync_builtin_message_previews(config, self.plugin_dir / "_conf_schema.json"):
            self.config.save_config()
            logger.info("已同步内置文案预览到当前插件配置。")
        self.data_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_ark_calendar"
        self.service = CalendarService(self.plugin_dir, self.data_dir, config, logger)
        self.renderer = CalendarRenderer(self, self.service)
        self.messages = MessageCatalog(config, logger)
        self.render_cache = CalendarImageCache(self.data_dir / "render")
        self.help_cache = HelpImageCache(self.data_dir / "render")
        self.subscription_manager = SubscriptionManager(self.data_dir, logger)
        self.bilibili_manager: BilibiliDynamicManager | None = None
        self.recruitment_source: RecruitmentSource | None = None
        self.image_manager = CalendarImageManager(
            self.render_cache, self.renderer, self.service, config, logger
        )
        self.help_manager = HelpManager(self.help_cache, self.renderer, self.service, logger)
        self.notification_manager = NotificationManager(config, self.service.cache, context, logger)
        # 定时任务相关
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduled_report_lock = asyncio.Lock()
        self._scheduled_birthday_greeting_lock = asyncio.Lock()
        self._scheduled_subscription_reminder_lock = asyncio.Lock()
        self._daily_precache_lock = asyncio.Lock()
        self._daily_precache_time = "04:00"
        self._startup_precache_task: asyncio.Task[None] | None = None
        self._bilibili_baseline_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        await self.service.initialize()
        # service.initialize() 之后 http 和 assets 才就绪，所以在这里创建 bilibili 相关对象。
        bilibili_source = BilibiliDynamicSource(
            http=self.service.http,
            cache=self.service.cache,
            asset_cache=self.service.assets,
        )
        custom_rsshub = self._value("bilibili_dynamic", "rsshub_base_url", "")
        if custom_rsshub:
            bilibili_source.set_custom_rsshub_url(custom_rsshub)
        self.bilibili_manager = BilibiliDynamicManager(
            source=bilibili_source,
            context=self.context,
            config=self.config,
            renderer=self.renderer,
            require_baseline=True,
        )
        # 建立动态历史基线依赖外部 RSSHub，不能阻塞插件加载。
        self._bilibili_baseline_task = asyncio.create_task(
            self.bilibili_manager.initialize_state(),
            name="ark_calendar_bilibili_baseline",
        )
        # 创建公招数据源
        self.recruitment_source = RecruitmentSource(http=self.service.http)
        self._initialize_scheduler()
        # 重载后的图片预热：默认开启，让首次帮助命令直接命中缓存。
        # v0.8.2 起帮助图请求体已降到 1.35 MB，预热不再明显影响 AstrBot 前端；
        # 需要彻底避免重载时的后台渲染可手动关闭。
        if self.service.value("cache_and_render", "reload_precache_enabled", True):
            self._startup_precache_task = asyncio.create_task(
                self._precache_help_images_after_reload(),
                name="ark_calendar_startup_help_precache",
            )
        logger.info(f"罗德岛行动终端插件 v{self.service.plugin_version} 已初始化。")

    async def terminate(self) -> None:
        if self._bilibili_baseline_task and not self._bilibili_baseline_task.done():
            self._bilibili_baseline_task.cancel()
            await asyncio.gather(self._bilibili_baseline_task, return_exceptions=True)
        self._bilibili_baseline_task = None
        if self._startup_precache_task and not self._startup_precache_task.done():
            self._startup_precache_task.cancel()
            await asyncio.gather(self._startup_precache_task, return_exceptions=True)
            self._startup_precache_task = None
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("方舟日历定时任务调度器已关闭。")
        await self.service.close()

    async def _precache_help_images_after_reload(self) -> None:
        """重载后后台补齐当天缺失或已失效的两张帮助图。"""
        try:
            missing_modes = [mode for mode in HelpImageCache.MODES if not self.help_cache.lookup(mode)]
            if not missing_modes:
                logger.info("重载后帮助长图预热跳过：两个当日缓存均有效。")
                return

            snapshot = await self.service.snapshot()
            for mode in missing_modes:
                await self.help_manager._render_help_image(
                    mode,
                    snapshot=snapshot,
                    user_commands=USER_COMMANDS,
                    admin_commands=ADMIN_COMMANDS,
                    subscription_commands=SUBSCRIPTION_COMMANDS,
                )
            logger.info(f"重载后帮助长图预热完成：{', '.join(missing_modes)}。")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("重载后帮助长图预热失败，将在收到对应命令时按需重试。", exc_info=True)

    @filter.command(CALENDAR_COMMAND.name, alias=CALENDAR_COMMAND.alias_set)
    async def calendar_command(self, event: AstrMessageEvent):
        """生成明日方舟活动、寻访、生日和今日信息长图。"""
        display_config = self.image_manager._display_config()
        cached = self.image_manager.current_cached_image(display_config)
        if cached:
            logger.info("手动方舟日历命令命中最终图片缓存。")
            quality = self.service.last_refresh_outcome.quality
            quality_notice = data_quality_notice(quality, self.messages)
            if quality_notice:
                yield event.plain_result(quality_notice)
            yield event.image_result(str(cached))
            return
        if self.image_manager.send_rendering_notice():
            yield event.plain_result(self.messages.text("rendering_started"))
        try:
            snapshot, outcome = await self.service.snapshot_with_outcome()
            quality_notice = data_quality_notice(outcome.quality, self.messages)
            if quality_notice:
                yield event.plain_result(quality_notice)
            image, image_state, fallback_manifest = await self.image_manager.get_calendar_image(snapshot, display_config)
            if image_state == "fallback":
                yield event.plain_result(self.image_manager.fallback_notice(fallback_manifest, self.messages))
            yield event.image_result(str(image))
            await self.notification_manager.observe_health(outcome, "手动日历")
        except Exception:
            logger.error("生成方舟日历失败。", exc_info=True)
            await self.notification_manager.notify(
                "【方舟日历异常告警】\n手动日历生成失败，详情请查看 AstrBot 日志。",
                "render_failed",
            )
            yield event.plain_result(self.messages.text("render_failed"))

    @filter.command(BIRTHDAY_COMMAND.name, alias=BIRTHDAY_COMMAND.alias_set)
    async def birthday_command(self, event: AstrMessageEvent, operator_name: str = ""):
        """查询指定干员的生日，例如：/方舟生日 卡缇。"""
        if not operator_name.strip():
            yield event.plain_result(self.messages.text("birthday_missing_query"))
            return
        try:
            operator, candidates = await self.service.find_operator(operator_name)
            if not operator:
                if candidates:
                    candidate_text = "\n".join(f"- {name}" for name in candidates)
                    yield event.plain_result(self.messages.text("birthday_candidates", candidates=candidate_text))
                else:
                    yield event.plain_result(self.messages.text("birthday_not_found", name=operator_name.strip()))
                return
            details = birthday_details(operator.profession, operator.rarity)
            if operator.birthday_month and operator.birthday_day:
                birthday = f"{operator.birthday_month} 月 {operator.birthday_day} 日"
                text = self.messages.text(
                    "birthday_found",
                    name=operator.name,
                    birthday=birthday,
                    details=details,
                )
                if self._is_birthday_today(operator):
                    text += "\n\n" + self.messages.text(
                        "birthday_today_greeting",
                        name=operator.name,
                        birthday=birthday,
                        details=details,
                    )
                yield event.plain_result(text)
            else:
                yield event.plain_result(
                    self.messages.text("birthday_unknown", name=operator.name, details=details)
                )
        except Exception:
            logger.error("查询干员生日失败。", exc_info=True)
            yield event.plain_result(self.messages.text("birthday_lookup_failed"))

    @filter.command(STATUS_COMMAND.name, alias=STATUS_COMMAND.alias_set)
    async def status_command(self, event: AstrMessageEvent):
        """查看最近一次快照、数据源和最终图片缓存状态。"""
        try:
            snapshot, outcome = await self.service.snapshot_with_outcome()
            logger.info("已响应方舟日历状态查询。")
            cache_status = (
                self.render_cache.status(snapshot, self.image_manager._display_config())
                if self.image_manager.cache_enabled() else {"state": "disabled"}
            )
            yield event.plain_result(format_status(snapshot, outcome, cache_status))
            await self.notification_manager.observe_health(outcome, "状态查询")
        except Exception:
            logger.error("读取方舟日历状态失败。", exc_info=True)
            yield event.plain_result(self.messages.text("status_failed"))

    @filter.command(SUBSCRIBE_COMMAND.name, alias=SUBSCRIBE_COMMAND.alias_set)
    async def subscribe_command(
        self,
        event: AstrMessageEvent,
        item_name: str = "",
        remind_time: str = "",
    ):
        """订阅活动或卡池，在结束前一天提醒。"""
        # 优先从原文剥命令名后整段解析，避免名称里的空格被框架切碎。
        arg_text = self._argument_text(event, SUBSCRIBE_COMMAND, item_name, remind_time)
        name, parsed_time, invalid_time = split_name_and_time(arg_text)

        if invalid_time:
            yield event.plain_result(self.messages.text("subscription_invalid_time"))
            return

        if not name:
            image = await self.help_manager.get_help_image(
                "subscribe",
                subscription_commands=SUBSCRIPTION_COMMANDS,
            )
            if image:
                yield event.image_result(str(image))
            else:
                yield event.plain_result(self.messages.text("subscription_missing_name"))
            return

        time_to_use = parsed_time or "12:00"

        try:
            snapshot = await self.service.snapshot()
            matches = self._find_timeline_items(snapshot, name)
            if not matches:
                yield event.plain_result(
                    self.messages.text("subscription_item_not_found", name=name)
                )
                return
            if len(matches) > 1:
                candidates = "\n".join(f"- {item.name}" for item in matches)
                yield event.plain_result(self.messages.text("subscription_candidates", candidates=candidates))
                return
            item = matches[0]

            user_id = str(event.message_obj.sender.user_id)
            # 必须存完整 SID（platform_id:message_type:session_id），
            # message_obj.session_id 只是裸会话号，Context.send_message() 无法解析。
            session_id = event.unified_msg_origin

            self.subscription_manager.add_subscription(item, user_id, session_id, time_to_use)
            yield event.plain_result(
                self.messages.text("subscription_added", name=item.name, time=time_to_use)
            )
        except Exception:
            logger.error("添加订阅失败。", exc_info=True)
            yield event.plain_result(self.messages.text("subscription_failed"))

    @filter.command(UNSUBSCRIBE_COMMAND.name, alias=UNSUBSCRIBE_COMMAND.alias_set)
    async def unsubscribe_command(self, event: AstrMessageEvent, item_name: str = ""):
        """取消订阅活动或卡池。"""
        name = self._argument_text(event, UNSUBSCRIBE_COMMAND, item_name).strip()

        if not name:
            yield event.plain_result(self.messages.text("unsubscribe_missing_name"))
            return

        try:
            user_id = str(event.message_obj.sender.user_id)
            session_id = event.unified_msg_origin
            subscriptions = self.subscription_manager.get_user_subscriptions(user_id, session_id)
            normalized = name.casefold()
            exact = [sub for sub in subscriptions if sub.item_name.casefold() == normalized]
            matches = exact or [
                sub for sub in subscriptions
                if normalized in sub.item_name.casefold() or sub.item_name.casefold() in normalized
            ]
            if not matches:
                yield event.plain_result(self.messages.text("subscription_not_found", name=name))
                return
            if len(matches) > 1:
                candidates = "\n".join(f"- {sub.item_name}" for sub in matches)
                yield event.plain_result(self.messages.text("unsubscribe_candidates", candidates=candidates))
                return
            sub = matches[0]

            if self.subscription_manager.remove_subscription(sub.item_id, user_id, session_id):
                yield event.plain_result(self.messages.text("subscription_removed", name=sub.item_name))
            else:
                yield event.plain_result(self.messages.text("subscription_not_found", name=sub.item_name))
        except Exception:
            logger.error("取消订阅失败。", exc_info=True)
            yield event.plain_result(self.messages.text("unsubscribe_failed"))

    @filter.command(SUBSCRIPTION_LIST_COMMAND.name, alias=SUBSCRIPTION_LIST_COMMAND.alias_set)
    async def subscription_list_command(self, event: AstrMessageEvent):
        """查看当前订阅的所有活动和卡池。"""
        try:
            user_id = str(event.message_obj.sender.user_id)
            session_id = event.unified_msg_origin
            subscriptions = self.subscription_manager.get_user_subscriptions(user_id, session_id)

            if not subscriptions:
                yield event.plain_result(self.messages.text("subscription_list_empty"))
                return

            lines = [self.messages.text("subscription_list_header")]
            for i, sub in enumerate(subscriptions, 1):
                type_label = "活动" if sub.item_type == "event" else "卡池"
                try:
                    end_time = parse_iso(sub.end_time).astimezone(CN_TZ)
                    end_str = end_time.strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError):
                    end_str = "时间未知"
                status = "✓ 已提醒" if sub.notified else f"⏰ {sub.remind_time} 提醒"
                lines.append(f"{i}. [{type_label}] {sub.item_name}\n   结束时间：{end_str}\n   提醒设置：{status}")

            yield event.plain_result("\n\n".join(lines))
        except Exception:
            logger.error("查询订阅列表失败。", exc_info=True)
            yield event.plain_result(self.messages.text("subscription_list_failed"))

    @filter.command(BILIBILI_DYNAMIC_COMMAND.name, alias=BILIBILI_DYNAMIC_COMMAND.alias_set)
    async def bilibili_dynamic_command(self, event: AstrMessageEvent, index: str = ""):
        """查看明日方舟官方B站最新动态。

        不带参数时显示列表，带编号时显示该动态详情。
        """
        if not self.bilibili_manager:
            yield event.plain_result(self.messages.text("bilibili_uninitialized"))
            return

        try:
            default_count = self.bilibili_manager._list_default_count()

            if not index.strip():
                # 显示列表
                dynamics = await self.bilibili_manager.query_list(default_count)

                if not dynamics:
                    yield event.plain_result(self.messages.text("bilibili_list_empty"))
                    return
                yield event.chain_result(await self.bilibili_manager.build_list_components(dynamics))

            else:
                # 显示指定动态的详情
                try:
                    idx = int(index)
                    if idx < 1:
                        yield event.plain_result(self.messages.text("bilibili_index_invalid"))
                        return
                except ValueError:
                    yield event.plain_result(self.messages.text("bilibili_index_invalid"))
                    return

                dynamic = await self.bilibili_manager.query_detail(idx, default_count)
                if not dynamic:
                    yield event.plain_result(self.messages.text("bilibili_not_found", index=idx))
                    return

                # 格式化并发送
                components = await self.bilibili_manager.build_message_components(
                    dynamic, event.unified_msg_origin
                )
                yield event.chain_result(components)
                forward_components = await self.bilibili_manager.build_forward_components(
                    dynamic, event.unified_msg_origin
                )
                if forward_components:
                    yield event.chain_result(forward_components)

        except Exception:
            logger.error("查询B站动态失败。", exc_info=True)
            yield event.plain_result(self.messages.text("bilibili_query_failed"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command(BILIBILI_DYNAMIC_TEST_COMMAND.name, alias=BILIBILI_DYNAMIC_TEST_COMMAND.alias_set)
    async def bilibili_dynamic_test_command(self, event: AstrMessageEvent):
        """管理员测试：模拟检测到新动态并推送。"""
        if not self.bilibili_manager:
            yield event.plain_result(self.messages.text("bilibili_uninitialized"))
            return

        try:
            yield event.plain_result(self.messages.text("push_test_started"))

            targets = self.bilibili_manager._push_targets()
            if not targets:
                yield event.plain_result(self.messages.text("push_test_no_target"))
                return

            sent, failed = await self.bilibili_manager.force_push_recent(targets)
            yield event.plain_result(self.messages.text("push_test_done", sent=sent, failed=failed))

        except Exception:
            logger.error("B站动态推送测试失败。", exc_info=True)
            yield event.plain_result(self.messages.text("push_test_failed"))

    @filter.command(RECRUIT_COMMAND.name, alias=RECRUIT_COMMAND.alias_set)
    async def recruit_command(self, event: AstrMessageEvent):
        """根据标签计算明日方舟公开招募可能出现的干员及保底星级。"""
        if not self.recruitment_source:
            yield event.plain_result(self.messages.text("recruit_uninitialized"))
            return

        # 从原文剥离命令名后取全部参数，支持空格/顿号/斜线分隔
        raw_text = self._argument_text(event, RECRUIT_COMMAND)

        if not raw_text.strip():
            help_text = (
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🏷️  方舟公招计算器\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "用法：/方舟公招 <标签1> [标签2] [标签3]\n\n"
                "示例：\n"
                "  /方舟公招 近卫干员 输出 生存\n"
                "  /方舟公招 资深干员 医疗干员\n"
                "  /方舟公招 高级资深干员\n\n"
                "可用职业标签（也可省略「干员」两字）：\n"
                "  近卫、狙击、术师、医疗、重装、辅助、特种、先锋\n\n"
                "可用位置标签：近战位、远程位\n\n"
                "特殊标签：资深干员（保底5★）、高级资深干员（保底6★）\n\n"
                "词缀标签：输出、治疗、生存、防护、控场、爆发、支援、减速、\n"
                "          削弱、群攻、位移、召唤、快速复活、费用回复、\n"
                "          支援机械（小车）、元素\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            try:
                rendered_help = await self.renderer.recruitment_help({
                    "职业": ["近卫干员", "狙击干员", "术师干员", "医疗干员", "重装干员", "辅助干员", "特种干员", "先锋干员"],
                    "位置": ["近战位", "远程位"],
                    "稀有度": ["新手", "资深干员（保底5★）", "高级资深干员（保底6★）"],
                    "词缀": ["输出", "治疗", "生存", "防护", "控场", "爆发", "支援", "减速", "削弱", "群攻", "位移", "召唤", "快速复活", "费用回复", "支援机械", "元素"],
                })
                if isinstance(rendered_help, (str, Path)) and Path(str(rendered_help)).is_file():
                    yield event.image_result(str(rendered_help))
                    return
            except Exception:
                logger.warning("公招帮助图片渲染失败，回退文字版。", exc_info=True)
            yield event.plain_result(help_text)
            return

        # 解析标签：支持空格、顿号、斜线、逗号分隔
        raw_tags = [t.strip() for t in re.split(r"[,，、/／\s]+", raw_text) if t.strip()]

        if len(raw_tags) > 5:
            yield event.plain_result(self.messages.text("recruit_too_many_tags", count=len(raw_tags)))
            return

        try:
            pool_data = await self.recruitment_source.get_recruitment_pool()
            if not pool_data["characters"]:
                yield event.plain_result(self.messages.text("recruit_data_failed"))
                return

            calculator = RecruitmentCalculator(pool_data["characters"])
            valid_tags, invalid_tags = calculator.normalize_tags(raw_tags)

            if invalid_tags:
                unknown = "、".join(invalid_tags)
                yield event.plain_result(self.messages.text("recruit_unknown_tags", tags=unknown))
                return

            if not valid_tags:
                yield event.plain_result(self.messages.text("recruit_empty_tags"))
                return

            results = calculator.calculate(valid_tags)
            try:
                rendered = await self.renderer.recruitment_result(results, valid_tags)
                if isinstance(rendered, (str, Path)) and Path(str(rendered)).is_file():
                    yield event.image_result(str(rendered))
                    return
            except Exception:
                logger.warning("公招结果图片渲染失败，回退文字版。", exc_info=True)
            yield event.plain_result(format_result(results, selected_tags=valid_tags))

        except Exception:
            logger.error("公招计算失败。", exc_info=True)
            yield event.plain_result(self.messages.text("recruit_failed"))

    @filter.command(HELP_COMMAND.name, alias=HELP_COMMAND.alias_set)
    async def help_command(self, event: AstrMessageEvent):
        """查看方舟日历的指令、别称与配置说明。"""
        image = await self.help_manager.get_help_image(
            "full",
            user_commands=USER_COMMANDS,
            admin_commands=ADMIN_COMMANDS,
        )
        if image:
            yield event.image_result(str(image))
        else:
            yield event.plain_result(generate_help_text(USER_COMMANDS, ADMIN_COMMANDS, HELP_COMMAND.name))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command(REFRESH_COMMAND.name, alias=REFRESH_COMMAND.alias_set)
    async def refresh_command(self, event: AstrMessageEvent):
        """管理员强制刷新数据并重新生成日历。"""
        yield event.plain_result(self.messages.text("force_refresh_started"))
        try:
            snapshot, outcome = await self.service.snapshot_with_outcome(force=True)
            if self.recruitment_source:
                self.recruitment_source.clear_cache()
            # 帮助页会展示可订阅日程；强制刷新后不能继续复用旧帮助图。
            self.help_cache.invalidate()
            display_config = self.image_manager._display_config()
            quality_notice = data_quality_notice(outcome.quality, self.messages)
            if quality_notice:
                yield event.plain_result(quality_notice)
            image, image_state, fallback_manifest = await self.image_manager.get_calendar_image(snapshot, display_config)
            if image_state == "fallback":
                yield event.plain_result(self.image_manager.fallback_notice(fallback_manifest, self.messages))
            yield event.image_result(str(image))
            await self.notification_manager.observe_health(outcome, "管理员强制刷新")
        except Exception:
            logger.error("强制刷新方舟日历失败。", exc_info=True)
            await self.notification_manager.notify(
                "【方舟日历异常告警】\n管理员强制刷新失败，详情请查看 AstrBot 日志。",
                "refresh_failed",
            )
            yield event.plain_result(self.messages.text("render_failed"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command(HISTORICAL_COMMAND.name, alias=HISTORICAL_COMMAND.alias_set)
    async def historical_schedule_command(
        self,
        event: AstrMessageEvent,
        date_text: str = "",
    ):
        """管理员按指定日期渲染与正常日报相同布局的历史测试图片。"""
        try:
            target_day = parse_historical_day(self._argument_text(event, HISTORICAL_COMMAND, date_text))
        except ValueError as exc:
            yield event.plain_result(self.messages.text("historical_range_invalid", error=exc))
            return
        try:
            snapshot = await self.service.historical_snapshot(target_day)
            image = await self.renderer.historical_calendar(snapshot)
            yield event.image_result(str(image))
        except Exception:
            logger.error("历史日程测试图片生成失败。", exc_info=True)
            yield event.plain_result(self.messages.text("historical_render_failed"))

    # ── 辅助方法 ─────────────────────────────────────────────

    @staticmethod
    def _argument_text(event: AstrMessageEvent, spec: CommandSpec, *fallback: str) -> str:
        """取命令后面的参数原文。

        框架按空白切分位置参数，名称里带空格（如「危机合约 · 熔火行动」）会被切碎，
        因此优先从消息原文里剥掉命令名自己解析；拿不到原文时退回拼接位置参数。
        """
        raw = getattr(event, "message_str", "") or ""
        argument_text = strip_command_prefix(raw, spec.invocations)
        if argument_text:
            return argument_text
        return " ".join(part for part in fallback if part).strip()

    @staticmethod
    def _is_birthday_today(operator) -> bool:
        now = datetime.now(CN_TZ)
        return operator.birthday_month == now.month and operator.birthday_day == now.day

    def _find_timeline_items(self, snapshot, name: str) -> list:
        """根据名称查找全部匹配的活动或卡池。"""
        name_normalized = name.lower().strip()
        all_items = snapshot.events + snapshot.gacha_pools + snapshot.long_term_events

        # 精确匹配
        exact = [item for item in all_items if item.name.lower() == name_normalized]
        if exact:
            return exact

        # 模糊匹配
        return [
            item for item in all_items
            if name_normalized in item.name.lower() or item.name.lower() in name_normalized
        ]

    # ── 配置读取（简化包装） ───────────────────────────────────

    def _value(self, section: str, key: str, default: Any, legacy_key: str | None = None) -> Any:
        return config_value(self.config, section, key, default, legacy_key)

    def _int_value(
        self,
        section: str,
        key: str,
        default: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
        legacy_key: str | None = None,
    ) -> int:
        return config_int(
            self.config, section, key, default,
            minimum=minimum, maximum=maximum, legacy_key=legacy_key,
        )

    # ── 定时任务注册与执行 ────────────────────────────────────


    def _initialize_scheduler(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone=CN_TZ)
        report_jobs = self._add_scheduled_report_jobs()
        birthday_jobs = self._add_scheduled_birthday_greeting_job()
        reminder_jobs = self._add_scheduled_subscription_reminder_job()
        precache_job = self._add_daily_precache_job()
        bilibili_job = self._add_bilibili_dynamic_job()
        if not report_jobs and not birthday_jobs and not reminder_jobs and not precache_job and not bilibili_job:
            self.scheduler = None
            return
        self.scheduler.start()

    def _add_scheduled_report_jobs(self) -> int:
        enabled = bool(self._value("scheduled_report", "enabled", False))
        targets = config_strings(self._value("scheduled_report", "target_sid_list", []))
        if not enabled:
            logger.info("定时方舟日报未启用。")
            return 0
        if not targets:
            logger.warning("定时方舟日报已启用，但未配置目标 SID，本次不创建任务。")
            return 0
        weekdays, invalid_weekdays = normalize_weekdays(
            self._value("scheduled_report", "weekdays", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        )
        times, invalid_times = parse_schedule_times(self._value("scheduled_report", "times", ["08:00"]))
        if invalid_weekdays:
            logger.warning(f"已忽略无效日报星期值：{invalid_weekdays}")
        if invalid_times:
            logger.warning(f"已忽略无效日报时间：{invalid_times}，请使用 HH:MM 格式。")
        if not weekdays or not times:
            logger.warning("定时方舟日报未配置有效的星期和时间，本次不创建任务。")
            return 0
        assert self.scheduler
        day_of_week = ",".join(weekdays)
        for scheduled_time in times:
            hour, minute = (int(value) for value in scheduled_time.split(":"))
            self.scheduler.add_job(
                self._scheduled_report,
                "cron",
                day_of_week=day_of_week,
                hour=hour,
                minute=minute,
                id=f"ark_calendar_report_{hour:02d}{minute:02d}",
                name=f"Ark Calendar Report {scheduled_time}",
                coalesce=True,
                max_instances=1,
                misfire_grace_time=60,
            )
        logger.info(f"已启用定时方舟日报：每周 {day_of_week}，在 {times} 发送至 {len(targets)} 个会话。")
        return len(times)

    def _add_scheduled_birthday_greeting_job(self) -> int:
        enabled = bool(self._value("scheduled_birthday_greeting", "enabled", False))
        targets = config_strings(self._value("scheduled_birthday_greeting", "target_sid_list", []))
        if not enabled:
            logger.info("自动生日祝贺未启用。")
            return 0
        if not targets:
            logger.warning("自动生日祝贺已启用，但未配置目标 SID，本次不创建任务。")
            return 0
        times, invalid_times = parse_schedule_times([
            self._value("scheduled_birthday_greeting", "time", "09:00")
        ])
        if invalid_times:
            logger.warning(f"自动生日祝贺时间无效：{invalid_times}，请使用 HH:MM 格式。")
        if not times:
            logger.warning("自动生日祝贺未配置有效发送时间，本次不创建任务。")
            return 0
        scheduled_time = times[0]
        hour, minute = (int(value) for value in scheduled_time.split(":"))
        assert self.scheduler
        self.scheduler.add_job(
            self._scheduled_birthday_greeting,
            "cron",
            hour=hour,
            minute=minute,
            id=f"ark_calendar_birthday_greeting_{hour:02d}{minute:02d}",
            name=f"Ark Calendar Birthday Greeting {scheduled_time}",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=60,
        )
        logger.info(f"已启用自动生日祝贺：每日 {scheduled_time} 发送至 {len(targets)} 个会话。")
        return 1

    def _add_scheduled_subscription_reminder_job(self) -> int:
        """添加订阅提醒定时任务，每分钟检查一次。"""
        assert self.scheduler
        self.scheduler.add_job(
            self._scheduled_subscription_reminder,
            "cron",
            minute="*",
            id="ark_calendar_subscription_reminder",
            name="Ark Calendar Subscription Reminder",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        logger.info("已启用订阅提醒任务：每分钟检查一次。")
        return 1

    def _scheduled_report_times(self) -> list[str]:
        """返回已生效的日报时间，用于与预缓存任务做撞车检查。"""
        if not bool(self._value("scheduled_report", "enabled", False)):
            return []
        if not config_strings(self._value("scheduled_report", "target_sid_list", [])):
            return []
        times, _ = parse_schedule_times(self._value("scheduled_report", "times", ["08:00"]))
        return times

    def _avoid_report_collision(self, scheduled_time: str) -> str | None:
        """撞上定时日报时把预缓存往后顺延，返回可用时间；无解时返回 None。

        不能简单地不建预缓存任务：它是帮助长图当天唯一的重渲染点
        （help_cache.invalidate() + 按新快照重建），缺了它，00:00-04:00
        之间生成的帮助图会带着"还没结束"的活动留一整天。
        撞车必须避开，因为两个任务用的是各自独立的锁，同一分钟起跑会各自
        强制刷新并并发渲染，既拖慢日报，也会让预缓存的失败排在日报之前发出。
        """
        report_times = self._scheduled_report_times()
        if scheduled_time not in report_times:
            return scheduled_time
        hour, minute = (int(value) for value in scheduled_time.split(":"))
        total = hour * 60 + minute
        for _ in range(self.PRECACHE_SHIFT_ATTEMPTS):
            total = (total + self.PRECACHE_SHIFT_MINUTES) % (24 * 60)
            candidate = "%02d:%02d" % divmod(total, 60)
            if candidate not in report_times:
                logger.warning(
                    f"每日预缓存时间 {scheduled_time} 与定时日报冲突，已顺延到 {candidate} 执行。"
                    "两个任务撞在同一分钟会并发强制刷新并抢渲染，且预缓存的失败会先于日报发出。"
                    "建议直接把 cache_and_render.daily_precache_time 改成日报之后的时间。"
                )
                return candidate
        return None

    def _add_daily_precache_job(self) -> int:
        """添加每日预缓存任务：刷新数据并预渲染日历、帮助图。"""
        assert self.scheduler
        if not bool(self._value("cache_and_render", "daily_precache_enabled", True)):
            logger.info("每日预缓存未启用。")
            return 0
        times, invalid_times = parse_schedule_times([
            self._value("cache_and_render", "daily_precache_time", "04:00")
        ])
        if invalid_times:
            logger.warning(f"已忽略无效预缓存时间：{invalid_times}，请使用 HH:MM 格式。")
        configured_time = times[0] if times else "04:00"
        scheduled_time = self._avoid_report_collision(configured_time)
        if scheduled_time is None:
            logger.warning(
                f"每日预缓存时间 {configured_time} 及其后续顺延时段都与定时日报冲突，"
                "本次不创建预缓存任务。请把 cache_and_render.daily_precache_time "
                "调整到日报时间之外。"
            )
            return 0
        hour, minute = (int(value) for value in scheduled_time.split(":"))
        if (hour, minute) < self.GAME_DAILY_RESET:
            # 帮助长图按自然日缓存、当天不再重渲染，图里的倒计时与可订阅日程都按
            # 快照生成时刻计算。在游戏日切前预缓存，04:00 结束的活动此刻还没结束，
            # 会被当成可订阅项写进图里留一整天。任务照建，只是提醒改时间。
            reset_text = "%02d:%02d" % self.GAME_DAILY_RESET
            logger.warning(
                f"每日预缓存时间 {scheduled_time} 早于游戏日切时间 {reset_text}，"
                f"当天帮助长图与订阅列表会留下 {reset_text} 结束的活动。"
                "建议把 cache_and_render.daily_precache_time 调整到日切之后。"
            )
        self._daily_precache_time = scheduled_time
        self.scheduler.add_job(
            self._daily_precache,
            "cron",
            hour=hour,
            minute=minute,
            id="ark_calendar_daily_precache",
            name="Ark Calendar Daily Precache",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=600,
        )
        logger.info(f"已启用每日预缓存任务：每天 {scheduled_time} 刷新数据并预渲染日历、帮助图。")
        return 1

    def _add_bilibili_dynamic_job(self) -> int:
        """添加B站动态定时检查推送任务。"""
        if not self.bilibili_manager:
            return 0
        enabled = bool(self._value("bilibili_dynamic", "push_enabled", False))
        if not enabled:
            logger.info("B站动态定时推送未启用。")
            return 0
        targets = config_strings(self._value("bilibili_dynamic", "target_sid_list", []))
        if not targets:
            logger.warning("B站动态定时推送已启用，但未配置目标 SID，本次不创建任务。")
            return 0
        interval_minutes = config_int(
            self.config,
            "bilibili_dynamic",
            "check_interval_minutes",
            5,
            minimum=1,
            maximum=60,
        )
        self.scheduler.add_job(
            self._scheduled_bilibili_dynamic_check,
            trigger="interval",
            minutes=interval_minutes,
            id="bilibili_dynamic_check",
            name="Bilibili Dynamic Check",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        logger.info(f"已启用B站动态定时推送任务：每 {interval_minutes} 分钟检查一次，目标 {len(targets)} 个会话。")
        return 1

    async def _scheduled_bilibili_dynamic_check(self) -> None:
        """定时任务：检查并推送B站新动态。"""
        try:
            await self.bilibili_manager.check_and_push()
        except Exception:
            logger.error("B站动态定时检查失败。", exc_info=True)

    async def _daily_precache(self) -> None:
        """刷新当天数据，并预渲染日历与两种帮助长图。"""
        if self._daily_precache_lock.locked():
            logger.warning("每日预缓存：已有任务正在执行，本次跳过。")
            return
        async with self._daily_precache_lock:
            logger.info("每日预缓存开始：强制刷新数据并生成当天图片缓存。")
            try:
                snapshot, outcome = await self.service.snapshot_with_outcome(force=True)
                calendar_image, image_state, _ = await self.image_manager.get_calendar_image(snapshot, self.image_manager._display_config())

                # 任务补跑或凌晨前曾生成帮助图时，先清除当天旧版本，确保使用新快照重渲染。
                self.help_cache.invalidate()
                help_cache_paths: dict[str, Path] = {}
                uncached_modes: list[str] = []
                failed_modes: list[str] = []
                for mode in HelpImageCache.MODES:
                    rendered = await self.help_manager._render_help_image(
                        mode,
                        snapshot=snapshot,
                        user_commands=USER_COMMANDS,
                        admin_commands=ADMIN_COMMANDS,
                        subscription_commands=SUBSCRIPTION_COMMANDS,
                    )
                    cached = self.help_cache.lookup(mode)
                    if cached:
                        help_cache_paths[mode] = cached
                    elif rendered:
                        uncached_modes.append(mode)
                    else:
                        # 一个帮助图失败时仍继续生成另一种，避免一次局部故障放大为整天无缓存。
                        failed_modes.append(mode)

                logger.info(
                    "每日预缓存完成："
                    f"日历={calendar_image}（来源={image_state}），"
                    f"帮助图={', '.join(f'{mode}={path}' for mode, path in help_cache_paths.items()) or '无'}。"
                )
                if uncached_modes:
                    logger.warning(
                        "每日预缓存：以下帮助图本次已生成但未写入缓存，将由后续调用重试："
                        f"{', '.join(uncached_modes)}。"
                    )
                if failed_modes:
                    # 帮助图只是缓存预热，失败时收到命令会按需重渲染，不影响日报投递，
                    # 因此只记日志、不惊动管理员。
                    logger.error(
                        f"每日预缓存：帮助长图生成失败：{', '.join(failed_modes)}，"
                        "将在收到对应命令时按需重试。"
                    )
                await self.notification_manager.observe_health(outcome, "每日预缓存")
            except Exception:
                logger.error("每日预缓存执行失败。", exc_info=True)
                await self.notification_manager.notify(
                    f"【方舟日历异常告警】\n每日 {self._daily_precache_time} 预缓存未能完成，"
                    "详情请查看 AstrBot 日志。",
                    "daily_precache_failed",
                )

    async def _scheduled_report(self) -> None:
        if self._scheduled_report_lock.locked():
            logger.warning("定时方舟日报：已有任务正在执行，本次跳过。")
            return
        async with self._scheduled_report_lock:
            targets = list(dict.fromkeys(config_strings(self._value("scheduled_report", "target_sid_list", []))))
            refresh = bool(self._value("scheduled_report", "refresh_data_before_send", True))
            if not targets:
                logger.warning("定时方舟日报：目标 SID 为空，本次跳过。")
                return
            logger.info(f"定时方舟日报开始：目标 {len(targets)} 个会话，强制刷新={refresh}。")
            try:
                snapshot, outcome = await self.service.snapshot_with_outcome(force=refresh)
                image, image_state, _ = await self.image_manager.get_calendar_image(snapshot, self.image_manager._display_config())
                caption = self.messages.text("scheduled_report_caption")
                quality_notice = data_quality_notice(outcome.quality, self.messages)
                if quality_notice:
                    caption = f"{caption}\n{quality_notice}"
                sent, failed = await self._send_scheduled_image(targets, image, caption)
                logger.info(f"定时方舟日报完成：成功 {sent}/{len(targets)}，图片来源={image_state}。")
                if failed:
                    await self.notification_manager.notify(
                        "【方舟日历异常告警】\n"
                        f"定时日报发送失败：{', '.join(failed)}\n"
                        f"成功发送：{sent}/{len(targets)}",
                        "scheduled_send_failed",
                    )
                await self.notification_manager.notify_refresh_status(snapshot, refresh, sent, len(targets), image_state, outcome)
                await self.notification_manager.observe_health(outcome, "定时日报")
            except Exception:
                logger.error("定时方舟日报执行失败。", exc_info=True)
                await self.notification_manager.notify(
                    "【方舟日历异常告警】\n定时日报未能完成，详情请查看 AstrBot 日志。",
                    "scheduled_report_failed",
                )

    async def _scheduled_birthday_greeting(self) -> None:
        if self._scheduled_birthday_greeting_lock.locked():
            logger.warning("自动生日祝贺：已有任务正在执行，本次跳过。")
            return
        async with self._scheduled_birthday_greeting_lock:
            targets = list(dict.fromkeys(config_strings(
                self._value("scheduled_birthday_greeting", "target_sid_list", [])
            )))
            if not targets:
                logger.warning("自动生日祝贺：目标 SID 为空，本次跳过。")
                return
            try:
                snapshot, outcome = await self.service.snapshot_with_outcome()
                birthdays = snapshot.today_birthdays
                if not birthdays:
                    logger.info("自动生日祝贺：今天没有干员生日，本次不发送。")
                    return
                date_key = datetime.now(CN_TZ).date().isoformat()
                pending_targets = self._birthday_greeting_pending_targets(targets, date_key)
                if not pending_targets:
                    logger.info("自动生日祝贺：目标会话今日均已发送，本次跳过。")
                    return
                names = "、".join(operator.name for operator in birthdays)
                text = self.messages.text(
                    "scheduled_birthday_greeting",
                    names=names,
                    count=len(birthdays),
                )
                quality_notice = data_quality_notice(outcome.quality, self.messages)
                if quality_notice:
                    text = f"{quality_notice}\n{text}"
                sent, failed = await self._send_scheduled_text(pending_targets, text)
                self._record_birthday_greeting_targets(sent, date_key)
                logger.info(
                    f"自动生日祝贺完成：寿星 {names}，成功 {len(sent)}/{len(pending_targets)}。"
                )
                if failed:
                    await self.notification_manager.notify(
                        "【方舟日历异常告警】\n"
                        f"自动生日祝贺发送失败：{', '.join(failed)}\n"
                        f"成功发送：{len(sent)}/{len(pending_targets)}",
                        "scheduled_birthday_greeting_failed",
                    )
                await self.notification_manager.observe_health(outcome, "自动生日祝贺")
            except Exception:
                logger.error("自动生日祝贺执行失败。", exc_info=True)
                await self.notification_manager.notify(
                    "【方舟日历异常告警】\n自动生日祝贺未能完成，详情请查看 AstrBot 日志。",
                    "scheduled_birthday_greeting_failed",
                )

    async def _scheduled_subscription_reminder(self) -> None:
        """定时检查并发送订阅提醒"""
        if self._scheduled_subscription_reminder_lock.locked():
            logger.warning("订阅提醒任务：已有任务正在执行，本次跳过。")
            return
        async with self._scheduled_subscription_reminder_lock:
            try:
                if not self.subscription_manager.has_subscriptions():
                    logger.debug("订阅提醒任务：没有订阅记录，本次跳过数据刷新。")
                    return
                snapshot = await self.service.snapshot()
                # cleanup_expired 会先按当前快照同步延期后的结束时间，再清理真正过期记录。
                self.subscription_manager.cleanup_expired(snapshot)
                pending = self.subscription_manager.get_pending_reminders(snapshot)
                if not pending:
                    logger.debug("订阅提醒任务：没有需要发送的提醒。")
                    return

                logger.info(f"订阅提醒任务：找到 {len(pending)} 个待提醒订阅。")

                # 按 (会话, 订阅者) 分组：同一人在同一会话的多条提醒合并成一条消息，
                # 但不同订阅者各发一条，避免一条消息里出现多个 At 组件。
                grouped: dict[tuple[str, str], list[Subscription]] = defaultdict(list)
                end_texts: dict[str, str] = {}
                for sub, item in pending:
                    try:
                        end_texts[sub.item_id] = parse_iso(item.end).astimezone(CN_TZ).strftime("%H:%M")
                    except (TypeError, ValueError):
                        end_texts[sub.item_id] = "未知时间"
                    grouped[(sub.session_id, sub.user_id)].append(sub)

                success_count = 0
                for (session_id, user_id), subs in grouped.items():
                    try:
                        if not platform_supports_proactive_send(session_id, self.context):
                            logger.warning(f"订阅提醒不支持主动投递至 {session_id}。")
                            continue
                        use_at = platform_supports_at(session_id, self.context)
                        # 白名单平台由 At 组件负责提醒，正文不再拼 @；其他群聊退化为纯文本 @。
                        if use_at:
                            mention = ""
                        elif is_group_session(session_id):
                            mention = f"@{user_id} "
                        else:
                            mention = ""

                        lines = [
                            self.messages.text(
                                "subscription_reminder",
                                user=mention if index == 0 else "",
                                name=sub.item_name,
                                end_time=end_texts.get(sub.item_id, "未知时间"),
                            )
                            for index, sub in enumerate(subs)
                        ]

                        components: list[Any] = []
                        if use_at:
                            components.append(Comp.At(qq=user_id, name=user_id))
                        components.append(Comp.Plain(text="\n\n".join(lines)))

                        dispatched = await self.context.send_message(session_id, MessageChain(components))
                        if dispatched is False:
                            logger.warning(
                                f"订阅提醒未投递至 {session_id}（订阅者 {user_id}）："
                                "请确认该 SID 对应的平台适配器仍在运行。"
                            )
                            continue

                        # 只标记本次确实发出去的订阅，失败的留到下一轮重试。
                        for sub in subs:
                            self.subscription_manager.mark_notified(sub)

                        success_count += len(subs)
                        logger.info(
                            f"订阅提醒已提交投递至 {session_id}（订阅者 {user_id}，"
                            f"{len(subs)} 个提醒，At={'是' if use_at else '否'}）。"
                        )
                    except Exception:
                        logger.error(
                            f"向 {session_id} 发送订阅提醒失败（订阅者 {user_id}）。", exc_info=True
                        )

                logger.info(f"订阅提醒任务完成：成功发送 {success_count} 个提醒。")
            except Exception:
                logger.error("订阅提醒任务执行失败。", exc_info=True)

    def _birthday_greeting_pending_targets(self, targets: list[str], date_key: str) -> list[str]:
        stored = self.service.cache.load("birthday_greeting_state.json")
        stored = stored if isinstance(stored, dict) else {}
        sent = stored.get("sent", {}) if isinstance(stored.get("sent", {}), dict) else {}
        return [sid for sid in targets if sent.get(sid) != date_key]

    def _record_birthday_greeting_targets(self, targets: list[str], date_key: str) -> None:
        stored = self.service.cache.load("birthday_greeting_state.json")
        stored = stored if isinstance(stored, dict) else {}
        sent = stored.get("sent", {}) if isinstance(stored.get("sent", {}), dict) else {}
        sent.update({sid: date_key for sid in targets})
        self.service.cache.save("birthday_greeting_state.json", {"sent": sent})

    async def _send_scheduled_text(self, targets: list[str], text: str) -> tuple[list[str], list[str]]:
        sent: list[str] = []
        failed: list[str] = []
        for sid in targets:
            if not platform_supports_proactive_send(sid, self.context):
                failed.append(sid)
                logger.warning(f"自动生日祝贺不支持主动投递至 SID {sid}。")
                continue
            try:
                dispatched = await self.context.send_message(sid, MessageChain([Comp.Plain(text=text)]))
                if dispatched is False:
                    failed.append(sid)
                    logger.warning(f"自动生日祝贺未投递至 SID {sid}。")
                    continue
                sent.append(sid)
            except Exception:
                failed.append(sid)
                logger.error(f"自动生日祝贺发送到 SID {sid} 失败。", exc_info=True)
        return sent, failed

    async def _send_scheduled_image(self, targets: list[str], image: Path | str, caption: str) -> tuple[int, list[str]]:
        sent = 0
        failed: list[str] = []
        for sid in targets:
            if not platform_supports_proactive_send(sid, self.context):
                failed.append(sid)
                logger.warning(f"定时方舟日报不支持主动投递至 SID {sid}。")
                continue
            try:
                components = [Comp.Plain(text=caption), Comp.Image.fromFileSystem(str(image))]
                dispatched = await self.context.send_message(sid, MessageChain(components))
                if dispatched is False:
                    failed.append(sid)
                    logger.warning(f"定时方舟日报未投递至 SID {sid}。")
                    continue
                sent += 1
            except Exception:
                failed.append(sid)
                logger.error(f"定时方舟日报发送到 SID {sid} 失败。", exc_info=True)
        return sent, failed

