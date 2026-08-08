from __future__ import annotations

import asyncio
import time
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
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .core.command_args import split_name_and_time, strip_command_prefix
from .core.config import config_int, config_strings, config_value, sync_builtin_message_previews
from .core.messages import MessageCatalog
from .core.models import parse_iso
from .core.render_cache import CalendarImageCache, HelpImageCache
from .core.renderer import CalendarRenderer
from .core.scheduler_utils import normalize_weekdays, parse_schedule_times
from .core.service import CalendarService
from .core.subscription import Subscription, SubscriptionManager

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
    "生成仅含活动与寻访时间轴的历史测试图片，例如：/方舟历史日程测试 2026-07-01 2026-07-31。",
    argument_hint="<开始日期> <结束日期>",
    example="/方舟历史日程测试 2026-07-01 2026-07-31",
)

# 发送侧确认会把 Comp.At 转成平台原生提醒的适配器类型（PlatformMetadata.name）。
# 其余平台要么只降级成纯文本，要么直接忽略 At 组件，因此统一走纯文本前缀。
AT_CAPABLE_PLATFORMS = frozenset({"aiocqhttp", "discord", "kook", "lark", "satori"})

SUBSCRIPTION_COMMANDS = (SUBSCRIBE_COMMAND, UNSUBSCRIBE_COMMAND, SUBSCRIPTION_LIST_COMMAND)
USER_COMMANDS = (CALENDAR_COMMAND, BIRTHDAY_COMMAND, STATUS_COMMAND, *SUBSCRIPTION_COMMANDS, HELP_COMMAND)
ADMIN_COMMANDS = (REFRESH_COMMAND, HISTORICAL_COMMAND)

# （图片、来源状态、降级 manifest）。仅当状态为 "fallback" 时才有 manifest，
# 降级提示可直接复用它，无需再读一次缓存。
CalendarImageResult = tuple[Path | str, str, dict[str, Any] | None]


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
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduled_report_lock = asyncio.Lock()
        self._scheduled_birthday_greeting_lock = asyncio.Lock()
        self._scheduled_subscription_reminder_lock = asyncio.Lock()
        self._notification_state_lock = asyncio.Lock()
        self._daily_precache_lock = asyncio.Lock()
        self._daily_precache_time = "04:00"
        self._render_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._render_locks_guard = asyncio.Lock()
        self._help_render_locks = {mode: asyncio.Lock() for mode in HelpImageCache.MODES}
        self._startup_precache_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        await self.service.initialize()
        self._initialize_scheduler()
        # 重载后的图片预热放到独立任务中，避免阻塞插件初始化和主事件循环。
        self._startup_precache_task = asyncio.create_task(
            self._precache_help_images_after_reload(),
            name="ark_calendar_startup_help_precache",
        )
        logger.info(f"罗德岛行动日历插件 v{self.service.plugin_version} 已初始化。")

    async def terminate(self) -> None:
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
                lock = self._help_render_locks[mode]
                async with lock:
                    if self.help_cache.lookup(mode):
                        continue
                    await self._render_help_image(mode, snapshot)
            logger.info(f"重载后帮助长图预热完成：{', '.join(missing_modes)}。")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("重载后帮助长图预热失败，将在收到对应命令时按需重试。", exc_info=True)

    @filter.command(CALENDAR_COMMAND.name, alias=CALENDAR_COMMAND.alias_set)
    async def calendar_command(self, event: AstrMessageEvent):
        """生成明日方舟活动、寻访、生日和今日信息长图。"""
        cached = self._current_cached_image()
        if cached:
            logger.info("手动方舟日历命令命中最终图片缓存。")
            quality_notice = self._data_quality_notice()
            if quality_notice:
                yield event.plain_result(quality_notice)
            yield event.image_result(str(cached))
            return
        if self._send_rendering_notice():
            yield event.plain_result(self.messages.text("rendering_started"))
        try:
            snapshot, outcome = await self.service.snapshot_with_outcome()
            quality_notice = self._data_quality_notice(outcome)
            if quality_notice:
                yield event.plain_result(quality_notice)
            image, image_state, fallback_manifest = await self._calendar_image(snapshot)
            if image_state == "fallback":
                yield event.plain_result(self._fallback_notice(fallback_manifest))
            yield event.image_result(str(image))
            await self._observe_health(outcome, "手动日历")
        except Exception:
            logger.error("生成方舟日历失败。", exc_info=True)
            await self._notify_admin(
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
            details = self._birthday_details(operator.profession, operator.rarity)
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
            yield event.plain_result(self._format_status(snapshot, outcome))
            await self._observe_health(outcome, "状态查询")
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
        name, parsed_time = split_name_and_time(arg_text)

        if not name:
            image = await self._help_image("subscribe")
            if image:
                yield event.image_result(str(image))
            else:
                yield event.plain_result("请输入要订阅的活动或卡池名称，例如：/方舟订阅 感谢庆典\n使用 /方舟日历 查看当前活动和卡池")
            return

        time_to_use = parsed_time or "12:00"

        try:
            snapshot = await self.service.snapshot()
            item = self._find_timeline_item(snapshot, name)
            if not item:
                yield event.plain_result(
                    self.messages.text("subscription_item_not_found", name=name)
                )
                return

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
            yield event.plain_result("订阅失败，请稍后重试。")

    @filter.command(UNSUBSCRIBE_COMMAND.name, alias=UNSUBSCRIBE_COMMAND.alias_set)
    async def unsubscribe_command(self, event: AstrMessageEvent, item_name: str = ""):
        """取消订阅活动或卡池。"""
        name = self._argument_text(event, UNSUBSCRIBE_COMMAND, item_name).strip()

        if not name:
            yield event.plain_result("请输入要取消订阅的活动或卡池名称，例如：/方舟取消订阅 感谢庆典")
            return

        try:
            snapshot = await self.service.snapshot()
            item = self._find_timeline_item(snapshot, name)
            if not item:
                yield event.plain_result(
                    self.messages.text("subscription_item_not_found", name=name)
                )
                return

            user_id = str(event.message_obj.sender.user_id)
            session_id = event.unified_msg_origin

            if self.subscription_manager.remove_subscription(item.id, user_id, session_id):
                yield event.plain_result(self.messages.text("subscription_removed", name=item.name))
            else:
                yield event.plain_result(self.messages.text("subscription_not_found", name=item.name))
        except Exception:
            logger.error("取消订阅失败。", exc_info=True)
            yield event.plain_result("取消订阅失败，请稍后重试。")

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
            yield event.plain_result("查询订阅列表失败，请稍后重试。")

    @filter.command(HELP_COMMAND.name, alias=HELP_COMMAND.alias_set)
    async def help_command(self, event: AstrMessageEvent):
        """查看方舟日历的指令、别称与配置说明。"""
        image = await self._help_image("full")
        if image:
            yield event.image_result(str(image))
        else:
            yield event.plain_result(self._help_text())

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command(REFRESH_COMMAND.name, alias=REFRESH_COMMAND.alias_set)
    async def refresh_command(self, event: AstrMessageEvent):
        """管理员强制刷新数据并重新生成日历。"""
        yield event.plain_result(self.messages.text("force_refresh_started"))
        try:
            snapshot, outcome = await self.service.snapshot_with_outcome(force=True)
            # 帮助页会展示可订阅日程；强制刷新后不能继续复用旧帮助图。
            self.help_cache.invalidate()
            quality_notice = self._data_quality_notice(outcome)
            if quality_notice:
                yield event.plain_result(quality_notice)
            image, image_state, fallback_manifest = await self._calendar_image(snapshot)
            if image_state == "fallback":
                yield event.plain_result(self._fallback_notice(fallback_manifest))
            yield event.image_result(str(image))
            await self._observe_health(outcome, "管理员强制刷新")
        except Exception:
            logger.error("强制刷新方舟日历失败。", exc_info=True)
            await self._notify_admin(
                "【方舟日历异常告警】\n管理员强制刷新失败，详情请查看 AstrBot 日志。",
                "refresh_failed",
            )
            yield event.plain_result(self.messages.text("render_failed"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command(HISTORICAL_COMMAND.name, alias=HISTORICAL_COMMAND.alias_set)
    async def historical_schedule_command(
        self,
        event: AstrMessageEvent,
        start_date: str = "",
        end_date: str = "",
    ):
        """管理员渲染指定过去日期区间的活动与寻访时间轴。"""
        try:
            start, end = self._historical_range(start_date, end_date)
        except ValueError as exc:
            yield event.plain_result(self.messages.text("historical_range_invalid", error=exc))
            return
        try:
            snapshot = await self.service.historical_snapshot(start, end)
            image = await self.renderer.historical_calendar(snapshot)
            yield event.image_result(str(image))
        except Exception:
            logger.error("历史日程测试图片生成失败。", exc_info=True)
            yield event.plain_result(self.messages.text("historical_render_failed"))


    @staticmethod
    def _command_rows(commands: tuple[CommandSpec, ...]) -> list[dict[str, Any]]:
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

    async def _help_image(self, mode: str) -> Path | str | None:
        """取当日缓存的帮助长图；未命中则渲染并写入缓存。

        帮助页内容按自然日变化（倒计时、可订阅日程），因此缓存以
        (mode, 日期) 为键，当天复用同一张图，跨日自动失效。
        失败时返回 None 由调用方回退到文字版。
        """
        cached = self.help_cache.lookup(mode)
        if cached:
            logger.info(f"帮助长图命中当日缓存：{mode}。")
            return cached
        lock = self._help_render_locks.get(mode)
        if lock is None:
            return await self._render_help_image(mode)
        async with lock:
            cached = self.help_cache.lookup(mode)
            if cached:
                logger.info(f"帮助长图缓存由并发请求生成：{mode}。")
                return cached
            return await self._render_help_image(mode)

    async def _render_help_image(self, mode: str, snapshot=None) -> Path | str | None:
        """实际调用渲染器并写入当日缓存；缓存写失败不影响本次返回。"""
        try:
            snapshot = snapshot or await self.service.snapshot()
            if mode == "subscribe":
                user_rows = self._command_rows(SUBSCRIPTION_COMMANDS)
                admin_rows: list[dict[str, Any]] = []
            else:
                user_rows = self._command_rows(USER_COMMANDS)
                admin_rows = self._command_rows(ADMIN_COMMANDS)
            rendered = await self.renderer.help_page(
                snapshot,
                user_rows,
                admin_rows,
                mode=mode,
            )
        except Exception:
            logger.error("生成方舟帮助长图失败，已回退到文字版本。", exc_info=True)
            return None
        try:
            stored = self.help_cache.store(rendered, mode)
            if stored:
                logger.info(f"帮助长图已写入当日缓存：{stored}")
                return stored
        except Exception:
            logger.warning(f"帮助长图缓存写入失败，本次直接使用渲染结果：{mode}。", exc_info=True)
        return rendered









    @staticmethod
    def _historical_range(start_text: str, end_text: str) -> tuple[datetime, datetime]:
        if not start_text.strip() or not end_text.strip():
            raise ValueError("需要开始日期和结束日期")
        try:
            start_day = datetime.strptime(start_text.strip(), "%Y-%m-%d").date()
            end_day = datetime.strptime(end_text.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("日期格式必须是 YYYY-MM-DD") from exc
        today = datetime.now(CN_TZ).date()
        if start_day > end_day:
            raise ValueError("开始日期不能晚于结束日期")
        if end_day > today:
            raise ValueError("只能测试今天及以前的历史区间")
        if (end_day - start_day).days + 1 > 90:
            raise ValueError("单次最多测试 90 天")
        return (
            datetime.combine(start_day, datetime.min.time(), CN_TZ),
            datetime.combine(end_day, datetime.max.time(), CN_TZ),
        )

    @staticmethod
    def _help_text() -> str:
        """由命令定义生成帮助文本，使别名只需在一处维护。"""
        sections = [
            "罗德岛行动日历 · 使用说明",
            "【普通指令】\n" + "\n\n".join(spec.help_entry() for spec in USER_COMMANDS),
            "【管理员指令】\n" + "\n\n".join(spec.help_entry() for spec in ADMIN_COMMANDS),
            "【自动日报】\n请在插件配置的“自动方舟日报”中启用任务，填写星期、发送时间和目标 SID。",
            "【自动生日祝贺】\n"
            "可单独设置每日发送时间和目标 SID；当天没有干员生日时不会发送。\n"
            "目标 SID 和管理员 SID 可在对应会话发送 /sid 获取。",
            f"博士如果只想查看帮助，发送 /{HELP_COMMAND.name} 就可以了喵～",
        ]
        return "\n\n".join(sections)

    def _display_config(self) -> dict[str, Any]:
        return {
            "timeline_days": self.service.timeline_days(),
            "template_hash": self.renderer.template_hash,
            "include_recent_operators": self._value("basic", "include_recent_operators", True, "include_recent_operators"),
            "include_long_term": self._value("basic", "include_long_term", True, "include_long_term"),
            "show_source_footer": self._value("basic", "show_source_footer", True, "show_source_footer"),
        }

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

    def _cache_enabled(self) -> bool:
        return bool(self._value("cache_and_render", "final_image_cache_enabled", True))

    def _cache_max_age(self) -> int:
        requested = self._int_value(
            "cache_and_render", "final_image_cache_max_age_minutes", 30,
            minimum=1, maximum=1440,
        )
        return min(requested, max(1, int(self.service.cache_ttl().total_seconds() // 60)))

    def _cache_keep_count(self) -> int:
        return self._int_value(
            "cache_and_render", "final_image_cache_keep_count", 3,
            minimum=1, maximum=100,
        )

    def _fallback_max_age_hours(self) -> int:
        return self._int_value(
            "cache_and_render", "fallback_max_age_hours", 12,
            minimum=1, maximum=168,
        )

    def _send_rendering_notice(self) -> bool:
        return bool(self._value("cache_and_render", "send_rendering_notice", True))

    def _current_cached_image(self) -> Path | None:
        if not self._cache_enabled() or not self.service.last_snapshot or not self.service.snapshot_is_fresh():
            return None
        return self.render_cache.lookup(self.service.last_snapshot, self._display_config())

    async def _calendar_image(self, snapshot) -> CalendarImageResult:
        display_config = self._display_config()
        if not self._cache_enabled():
            return await self._render_calendar_image(snapshot, display_config)
        cached = self.render_cache.lookup(snapshot, display_config)
        if cached:
            logger.info("最终日历图片缓存命中。")
            return cached, "cache", None
        signature = self.render_cache.signature(snapshot, display_config)
        lock = await self._retain_render_lock(signature)
        try:
            async with lock:
                cached = self.render_cache.lookup(snapshot, display_config)
                if cached:
                    logger.info("最终日历图片缓存由并发请求生成。")
                    return cached, "cache", None
                return await self._render_calendar_image(snapshot, display_config)
        finally:
            await self._release_render_lock(signature, lock)

    async def _retain_render_lock(self, signature: str) -> asyncio.Lock:
        async with self._render_locks_guard:
            lock, references = self._render_locks.get(signature, (asyncio.Lock(), 0))
            self._render_locks[signature] = (lock, references + 1)
            return lock

    async def _release_render_lock(self, signature: str, lock: asyncio.Lock) -> None:
        async with self._render_locks_guard:
            current = self._render_locks.get(signature)
            if current is None or current[0] is not lock:
                return
            _, references = current
            if references <= 1:
                self._render_locks.pop(signature, None)
            else:
                self._render_locks[signature] = (lock, references - 1)

    async def _render_calendar_image(self, snapshot, display_config: dict[str, Any]) -> CalendarImageResult:
        started = time.monotonic()
        logger.info("最终日历图片缓存未命中，开始调用渲染器。")
        try:
            rendered = await self.renderer.calendar(snapshot)
            elapsed = time.monotonic() - started
            warning_seconds = self._int_value("cache_and_render", "slow_render_warning_seconds", 60, minimum=1, maximum=3600)
            if elapsed >= warning_seconds:
                logger.warning(f"方舟日历渲染耗时较长：{elapsed:.2f} 秒。")
            else:
                logger.info(f"方舟日历渲染完成，耗时 {elapsed:.2f} 秒。")
            if not self._cache_enabled():
                return rendered, "rendered", None
            cached = self.render_cache.store(
                rendered,
                snapshot,
                display_config,
                self._cache_max_age(),
                self._cache_keep_count(),
            )
            logger.info(f"最终日历图片已保存至插件缓存：{cached}")
            return cached, "rendered", None
        except Exception:
            fallback = self.render_cache.fallback(self._fallback_max_age_hours()) if self._cache_enabled() else None
            if fallback:
                image, manifest = fallback
                logger.warning(f"日历渲染失败，已回退到缓存图片（快照时间：{manifest.get('snapshot_generated_at', '未知')}）。")
                return image, "fallback", manifest
            raise

    def _fallback_notice(self, manifest: dict[str, Any] | None) -> str:
        """依据实际发出图片的 manifest 生成降级提示。"""
        time_text = str((manifest or {}).get("snapshot_generated_at", "") or "未知")
        return f"{self.messages.text('cached_fallback_notice')}\n缓存数据时间：{time_text}"

    def _data_quality_notice(self, outcome=None) -> str:
        quality = (outcome or self.service.last_refresh_outcome).quality
        if quality == "fresh":
            return ""
        details = {
            "degraded": "部分数据源使用了缓存或辅助信息暂时缺失。",
            "fallback": "关键数据源不可用，当前使用最近一次完整快照。",
            "failed": "关键数据源不可用，当前内容可能不完整。",
        }.get(quality, "部分数据可能不是最新。")
        return self.messages.text("data_degraded_notice", details=details)

    def _birthday_details(self, profession: str, rarity: int | None) -> str:
        details: list[str] = []
        if profession:
            details.append(f"职业：{profession}")
        if rarity:
            details.append(f"星级：{rarity}★")
        return f"\n{'　'.join(details)}" if details else ""

    @staticmethod
    def _is_birthday_today(operator) -> bool:
        now = datetime.now(CN_TZ)
        return operator.birthday_month == now.month and operator.birthday_day == now.day

    def _format_status(self, snapshot, outcome=None) -> str:
        cache_status = self.render_cache.status(snapshot, self._display_config()) if self._cache_enabled() else {"state": "disabled"}
        lines = ["罗德岛行动日历状态", f"快照时间：{snapshot.generated_at}"]
        outcome = outcome or self.service.last_refresh_outcome
        quality = outcome.quality
        quality_text = {
            "fresh": "正常",
            "degraded": "部分数据源降级",
            "fallback": "已使用最近一次完整快照",
            "failed": "失败",
        }.get(quality, quality)
        if outcome.error:
            quality_text += f"（{outcome.error}）"
        lines.append(f"最近刷新：{quality_text}")
        source_labels = {"fresh": "正常", "fallback": "缓存降级", "failed": "不可用"}
        source_states = outcome.source_states or snapshot.source_states
        for state in source_states:
            status = source_labels.get(state.status, "正常" if state.ok else "降级")
            detail = f" {state.message}" if state.message else ""
            data_time = f"（数据时间：{state.updated_at}）" if state.updated_at else ""
            lines.append(f"{state.name}：{status}{data_time}{detail}")
        state = cache_status.get("state")
        if state == "valid":
            lines.append(f"最终图片缓存：有效（至 {cache_status.get('expires_at', '')}）")
        elif state == "stale":
            lines.append(f"最终图片缓存：已过期（最近渲染 {cache_status.get('rendered_at', '')}）")
        elif state == "disabled":
            lines.append("最终图片缓存：已关闭")
        else:
            lines.append("最终图片缓存：暂无")
        return "\n".join(lines)

    def _initialize_scheduler(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone=CN_TZ)
        report_jobs = self._add_scheduled_report_jobs()
        birthday_jobs = self._add_scheduled_birthday_greeting_job()
        reminder_jobs = self._add_scheduled_subscription_reminder_job()
        precache_job = self._add_daily_precache_job()
        if not report_jobs and not birthday_jobs and not reminder_jobs and not precache_job:
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
        """添加订阅提醒定时任务，每小时检查一次"""
        assert self.scheduler
        self.scheduler.add_job(
            self._scheduled_subscription_reminder,
            "cron",
            minute=0,
            id="ark_calendar_subscription_reminder",
            name="Ark Calendar Subscription Reminder",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        logger.info("已启用订阅提醒任务：每小时检查一次。")
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

    async def _daily_precache(self) -> None:
        """刷新当天数据，并预渲染日历与两种帮助长图。"""
        if self._daily_precache_lock.locked():
            logger.warning("每日预缓存：已有任务正在执行，本次跳过。")
            return
        async with self._daily_precache_lock:
            logger.info("每日预缓存开始：强制刷新数据并生成当天图片缓存。")
            try:
                snapshot, outcome = await self.service.snapshot_with_outcome(force=True)
                calendar_image, image_state, _ = await self._calendar_image(snapshot)

                # 任务补跑或凌晨前曾生成帮助图时，先清除当天旧版本，确保使用新快照重渲染。
                self.help_cache.invalidate()
                help_cache_paths: dict[str, Path] = {}
                uncached_modes: list[str] = []
                failed_modes: list[str] = []
                for mode in HelpImageCache.MODES:
                    rendered = await self._render_help_image(mode, snapshot)
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
                await self._observe_health(outcome, "每日预缓存")
            except Exception:
                logger.error("每日预缓存执行失败。", exc_info=True)
                await self._notify_admin(
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
                image, image_state, _ = await self._calendar_image(snapshot)
                caption = self.messages.text("scheduled_report_caption")
                quality_notice = self._data_quality_notice(outcome)
                if quality_notice:
                    caption = f"{caption}\n{quality_notice}"
                sent, failed = await self._send_scheduled_image(targets, image, caption)
                logger.info(f"定时方舟日报完成：成功 {sent}/{len(targets)}，图片来源={image_state}。")
                if failed:
                    await self._notify_admin(
                        "【方舟日历异常告警】\n"
                        f"定时日报发送失败：{', '.join(failed)}\n"
                        f"成功发送：{sent}/{len(targets)}",
                        "scheduled_send_failed",
                    )
                await self._notify_refresh_status(snapshot, refresh, sent, len(targets), image_state, outcome)
                await self._observe_health(outcome, "定时日报")
            except Exception:
                logger.error("定时方舟日报执行失败。", exc_info=True)
                await self._notify_admin(
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
                quality_notice = self._data_quality_notice(outcome)
                if quality_notice:
                    text = f"{quality_notice}\n{text}"
                sent, failed = await self._send_scheduled_text(pending_targets, text)
                self._record_birthday_greeting_targets(sent, date_key)
                logger.info(
                    f"自动生日祝贺完成：寿星 {names}，成功 {len(sent)}/{len(pending_targets)}。"
                )
                if failed:
                    await self._notify_admin(
                        "【方舟日历异常告警】\n"
                        f"自动生日祝贺发送失败：{', '.join(failed)}\n"
                        f"成功发送：{len(sent)}/{len(pending_targets)}",
                        "scheduled_birthday_greeting_failed",
                    )
                await self._observe_health(outcome, "自动生日祝贺")
            except Exception:
                logger.error("自动生日祝贺执行失败。", exc_info=True)
                await self._notify_admin(
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
                snapshot = await self.service.snapshot()
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
                        use_at = self._platform_supports_at(session_id)
                        # 白名单平台由 At 组件负责提醒，正文不再拼 @；其他群聊退化为纯文本 @。
                        if use_at:
                            mention = ""
                        elif self._is_group_session(session_id):
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

                        delivered = await self.context.send_message(session_id, MessageChain(components))
                        if delivered is False:
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
                            f"订阅提醒已发送至 {session_id}（订阅者 {user_id}，"
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
            try:
                await self.context.send_message(sid, MessageChain([Comp.Plain(text=text)]))
                sent.append(sid)
            except Exception:
                failed.append(sid)
                logger.error(f"自动生日祝贺发送到 SID {sid} 失败。", exc_info=True)
        return sent, failed

    async def _send_scheduled_image(self, targets: list[str], image: Path | str, caption: str) -> tuple[int, list[str]]:
        sent = 0
        failed: list[str] = []
        for sid in targets:
            try:
                components = [Comp.Plain(text=caption), Comp.Image.fromFileSystem(str(image))]
                await self.context.send_message(sid, MessageChain(components))
                sent += 1
            except Exception:
                failed.append(sid)
                logger.error(f"定时方舟日报发送到 SID {sid} 失败。", exc_info=True)
        return sent, failed

    async def _notify_refresh_status(
        self, snapshot, refreshed: bool, sent: int, total: int, image_state: str, outcome=None
    ) -> None:
        if not refreshed or not self._notifications_enabled():
            return
        mode = str(self._value("admin_notification", "refresh_status_mode", "abnormal_only") or "abnormal_only")
        if mode != "all":
            # “仅异常时”由 _observe_health 统一处理，避免同一次任务重复通知。
            return
        await self._send_admin_text(
            "【方舟日历刷新状态】\n"
            f"刷新时间：{snapshot.generated_at}\n"
            f"发送结果：{sent}/{total}\n"
            f"图片来源：{self._image_state_label(image_state)}\n\n"
            f"{self._format_status(snapshot, outcome)}"
        )

    async def _observe_health(self, outcome, origin: str) -> None:
        if not self._notifications_enabled():
            return
        async with self._notification_state_lock:
            current = self._abnormal_states(outcome)
            stored = self.service.cache.load("notification_state.json")
            stored = stored if isinstance(stored, dict) else {}
            active = stored.get("active", {}) if isinstance(stored.get("active", {}), dict) else {}
            last_sent = stored.get("last_sent", {}) if isinstance(stored.get("last_sent", {}), dict) else {}
            now = datetime.now(CN_TZ)
            cooldown = self._int_value("admin_notification", "cooldown_minutes", 60, minimum=1, maximum=10080)
            alert_keys: list[str] = []
            alert_lines: list[str] = []
            for event_key, item in current.items():
                sent_at = self._parse_time(str(last_sent.get(event_key, "")))
                expired = sent_at is None or (now - sent_at).total_seconds() >= cooldown * 60
                if event_key not in active or expired:
                    alert_keys.append(event_key)
                    alert_lines.append(f"- {item['name']}：{item['message']}")
            recovered_keys = [event_key for event_key in active if event_key not in current]
            if alert_lines:
                succeeded, _ = await self._send_admin_text(
                    "【方舟日历异常告警】\n"
                    f"触发来源：{origin}\n"
                    f"时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
                    + "\n".join(alert_lines)
                )
                if succeeded:
                    for event_key in alert_keys:
                        last_sent[event_key] = now.isoformat()
            if recovered_keys and bool(self._value("admin_notification", "notify_on_recovery", True)):
                recovered_names = []
                for event_key in recovered_keys:
                    previous = active.get(event_key, {})
                    recovered_names.append(
                        str(previous.get("name", event_key)) if isinstance(previous, dict) else str(event_key)
                    )
                await self._send_admin_text(
                    "【方舟日历恢复通知】\n"
                    f"触发来源：{origin}\n"
                    f"已恢复：{', '.join(recovered_names)}"
                )
            self.service.cache.save("notification_state.json", {"active": current, "last_sent": last_sent})

    def _abnormal_states(self, outcome) -> dict[str, dict[str, str]]:
        """只依据本次刷新结果判定异常，避免把并发任务的故障算到自己头上。"""
        abnormal: dict[str, dict[str, str]] = {}
        for state in outcome.source_states:
            if state.ok:
                continue
            event_key = state.event_key or f"source:{state.name}:unavailable"
            abnormal[event_key] = {
                "name": state.name,
                "message": state.message or "数据源状态异常",
            }
        if outcome.error and outcome.quality in {"failed", "fallback"}:
            abnormal["calendar_refresh:failed"] = {
                "name": "方舟日历刷新",
                "message": outcome.error,
            }
        return abnormal

    async def _notify_admin(self, text: str, event: str) -> None:
        if not self._notifications_enabled():
            return
        logger.warning(f"方舟日历管理员通知开始发送：{event}")
        succeeded, failed = await self._send_admin_text(text)
        if succeeded:
            logger.info(f"方舟日历管理员通知已送达：{event}（{len(succeeded)} 个 SID）。")
        if failed:
            logger.warning(
                f"方舟日历管理员通知未送达：{event}（{len(failed)} 个 SID）。"
                "请确认 admin_sid_list 使用对应会话 /sid 返回的完整 SID，且该消息平台已连接。"
            )

    async def _send_admin_text(
        self,
        text: str,
        targets: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        succeeded: list[str] = []
        failed: list[str] = []
        for sid in targets or self._admin_sids():
            try:
                delivered = await self.context.send_message(sid, MessageChain([Comp.Plain(text=text)]))
                if delivered:
                    succeeded.append(sid)
                else:
                    failed.append(sid)
                    logger.warning(
                        f"向方舟日历管理员 SID {sid} 发送通知未被消息平台接收。"
                        "请使用该会话 /sid 返回的完整 SID，并确认对应平台在线。"
                    )
            except Exception:
                failed.append(sid)
                logger.error(f"向方舟日历管理员 SID {sid} 发送通知失败。", exc_info=True)
        return succeeded, failed
    def _notifications_enabled(self) -> bool:
        return bool(self._value("admin_notification", "enabled", False)) and bool(self._admin_sids())

    def _admin_sids(self) -> list[str]:
        return config_strings(self._value("admin_notification", "admin_sid_list", []))

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value).astimezone(CN_TZ)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _image_state_label(state: str) -> str:
        return {"cache": "最终图片缓存", "rendered": "新渲染图片", "fallback": "降级缓存图片"}.get(state, state)

    def _find_timeline_item(self, snapshot, name: str):
        """根据名称查找活动或卡池"""
        name_normalized = name.lower().strip()
        all_items = snapshot.events + snapshot.gacha_pools + snapshot.long_term_events

        # 精确匹配
        for item in all_items:
            if item.name.lower() == name_normalized:
                return item

        # 模糊匹配
        for item in all_items:
            if name_normalized in item.name.lower() or item.name.lower() in name_normalized:
                return item

        return None

    @staticmethod
    def _split_sid(session_id: str) -> tuple[str, str] | None:
        """把完整 SID 拆成 (platform_id, message_type)。

        SID 形如 `platform_id:message_type:session_id`，与 AstrBot 的
        `MessageSession.from_str()` 保持一致的 `split(":", 2)` 语义；
        段数不足说明不是完整 SID，返回 None 由调用方按未知处理。
        """
        parts = session_id.split(":", 2)
        if len(parts) < 3:
            return None
        return parts[0], parts[1]

    @classmethod
    def _is_group_session(cls, session_id: str) -> bool:
        """判断是否为群聊会话；无法解析出完整 SID 时按非群聊处理。"""
        parsed = cls._split_sid(session_id)
        if not parsed:
            return False
        return parsed[1] == MessageType.GROUP_MESSAGE.value

    def _platform_supports_at(self, session_id: str) -> bool:
        """判断该会话所在平台能否把 Comp.At 转成原生提醒。

        SID 首段是平台实例 id（用户可改名），不是适配器类型，因此要经
        `get_platform_inst()` 取 `meta().name` 才能与白名单比对。取不到实例
        （平台未启用等）时按不支持处理，退回纯文本。
        """
        parsed = self._split_sid(session_id)
        if not parsed:
            return False
        try:
            platform = self.context.get_platform_inst(parsed[0])
        except Exception:
            logger.warning(f"解析平台实例失败，订阅提醒退回纯文本：{parsed[0]}", exc_info=True)
            return False
        if platform is None:
            return False
        return platform.meta().name in AT_CAPABLE_PLATFORMS
