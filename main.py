from __future__ import annotations

import asyncio
import time
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

from .core.config import config_strings, config_value
from .core.messages import MessageCatalog
from .core.render_cache import CalendarImageCache
from .core.renderer import CalendarRenderer
from .core.scheduler_utils import normalize_weekdays, parse_schedule_times
from .core.service import CalendarService

CN_TZ = ZoneInfo("Asia/Shanghai")


class ArkCalendarPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).resolve().parent
        self.data_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_ark_calendar"
        self.service = CalendarService(self.plugin_dir, self.data_dir, config, logger)
        self.renderer = CalendarRenderer(self, self.service)
        self.messages = MessageCatalog(config)
        self.render_cache = CalendarImageCache(self.data_dir / "render")
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduled_report_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self.service.initialize()
        self._initialize_scheduler()
        logger.info("罗德岛行动日历插件 v0.3.0 已初始化。")

    async def terminate(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("方舟日历定时日报调度器已关闭。")
        await self.service.close()

    @filter.command("方舟日报帮助", alias={"方舟日历帮助", "明日方舟日报帮助"})
    async def help_command(self, event: AstrMessageEvent):
        """查看方舟日报的指令、别称与配置说明。"""
        yield event.plain_result(self._help_text())

    @filter.command("方舟日历", alias={"方舟日报", "明日方舟日历", "舟日历"})
    async def calendar_command(self, event: AstrMessageEvent):
        """生成明日方舟活动、寻访、生日和今日信息长图。"""
        cached = self._current_cached_image()
        if cached:
            logger.info("手动方舟日历命令命中最终图片缓存。")
            yield event.image_result(str(cached))
            return
        if self._send_rendering_notice():
            yield event.plain_result(self.messages.text("rendering_started"))
        try:
            snapshot = await self.service.snapshot()
            image, image_state = await self._calendar_image(snapshot)
            if image_state == "fallback":
                yield event.plain_result(self._fallback_notice())
            yield event.image_result(str(image))
            await self._observe_health(snapshot, "手动日历")
        except Exception:
            logger.error("生成方舟日历失败。", exc_info=True)
            await self._notify_admin(
                "【方舟日历异常告警】\n手动日历生成失败，详情请查看 AstrBot 日志。",
                "render_failed",
            )
            yield event.plain_result(self.messages.text("render_failed"))

    @filter.command("方舟生日", alias={"方舟生日查询", "明日方舟生日", "舟生日"})
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
                yield event.plain_result(
                    self.messages.text(
                        "birthday_found",
                        name=operator.name,
                        birthday=f"{operator.birthday_month} 月 {operator.birthday_day} 日",
                        details=details,
                    )
                )
            else:
                yield event.plain_result(
                    self.messages.text("birthday_unknown", name=operator.name, details=details)
                )
        except Exception:
            logger.error("查询干员生日失败。", exc_info=True)
            yield event.plain_result("生日查询暂时失败，请稍后再试。")

    @filter.command("方舟日历状态", alias={"方舟状态", "明日方舟日历状态"})
    async def status_command(self, event: AstrMessageEvent):
        """查看最近一次快照、数据源和最终图片缓存状态。"""
        try:
            snapshot = await self.service.snapshot()
            logger.info("已响应方舟日历状态查询。")
            yield event.plain_result(self._format_status(snapshot))
            await self._observe_health(snapshot, "状态查询")
        except Exception:
            logger.error("读取方舟日历状态失败。", exc_info=True)
            yield event.plain_result("无法读取方舟日历状态，请稍后再试。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("方舟日历刷新", alias={"方舟日历更新", "方舟日报刷新"})
    async def refresh_command(self, event: AstrMessageEvent):
        """管理员强制刷新数据并重新生成日历。"""
        yield event.plain_result(self.messages.text("force_refresh_started"))
        try:
            snapshot = await self.service.snapshot(force=True)
            image, image_state = await self._calendar_image(snapshot)
            if image_state == "fallback" or self.service.last_refresh_used_cache:
                yield event.plain_result(self._fallback_notice())
            yield event.image_result(str(image))
            await self._observe_health(snapshot, "管理员强制刷新")
        except Exception:
            logger.error("强制刷新方舟日历失败。", exc_info=True)
            await self._notify_admin(
                "【方舟日历异常告警】\n管理员强制刷新失败，详情请查看 AstrBot 日志。",
                "refresh_failed",
            )
            yield event.plain_result(self.messages.text("render_failed"))

    def _help_text(self) -> str:
        return (
            "罗德岛行动日历 · 使用说明\n\n"
            "【普通指令】\n"
            "/方舟日历（别名：/方舟日报、/明日方舟日历、/舟日历）\n"
            "生成活动、寻访、生日和今日作战信息长图；命中图片缓存时会直接发送。\n\n"
            "/方舟生日 <干员名称>（别名：/方舟生日查询、/明日方舟生日、/舟生日）\n"
            "以文字查询干员生日，例如：/方舟生日 卡缇。\n\n"
            "/方舟日历状态（别名：/方舟状态、/明日方舟日历状态）\n"
            "查看最近快照、数据源、降级状态和最终图片缓存。\n\n"
            "/方舟日报帮助（别名：/方舟日历帮助、/明日方舟日报帮助）\n"
            "查看本帮助。\n\n"
            "【管理员指令】\n"
            "/方舟日历刷新（别名：/方舟日历更新、/方舟日报刷新）\n"
            "强制刷新数据源并重新生成日历图片。\n\n"
            "【自动日报】\n"
            "请在插件配置的“自动方舟日报”中启用任务，填写星期、发送时间和目标 SID。\n"
            "目标 SID 和管理员 SID 可在对应会话发送 /sid 获取。\n\n"
            "博士如果只想查看帮助，发送 /方舟日报帮助 就可以了喵～"
        )

    def _display_config(self) -> dict[str, Any]:
        return {
            "timeline_days": self._value("basic", "timeline_days", 28, "timeline_days"),
            "include_recent_operators": self._value("basic", "include_recent_operators", True, "include_recent_operators"),
            "include_long_term": self._value("basic", "include_long_term", True, "include_long_term"),
            "show_source_footer": self._value("basic", "show_source_footer", True, "show_source_footer"),
        }

    def _value(self, section: str, key: str, default: Any, legacy_key: str | None = None) -> Any:
        return config_value(self.config, section, key, default, legacy_key)

    def _cache_enabled(self) -> bool:
        return bool(self._value("cache_and_render", "final_image_cache_enabled", True))

    def _cache_max_age(self) -> int:
        requested = max(1, int(self._value("cache_and_render", "final_image_cache_max_age_minutes", 30)))
        return min(requested, max(1, int(self.service.cache_ttl().total_seconds() // 60)))

    def _cache_keep_count(self) -> int:
        return max(1, int(self._value("cache_and_render", "final_image_cache_keep_count", 3)))

    def _send_rendering_notice(self) -> bool:
        return bool(self._value("cache_and_render", "send_rendering_notice", True))

    def _current_cached_image(self) -> Path | None:
        if not self._cache_enabled() or not self.service.last_snapshot or not self.service.snapshot_is_fresh():
            return None
        return self.render_cache.lookup(self.service.last_snapshot, self._display_config())

    async def _calendar_image(self, snapshot) -> tuple[Path | str, str]:
        display_config = self._display_config()
        if self._cache_enabled():
            cached = self.render_cache.lookup(snapshot, display_config)
            if cached:
                logger.info("最终日历图片缓存命中。")
                return cached, "cache"
        started = time.monotonic()
        logger.info("最终日历图片缓存未命中，开始调用渲染器。")
        try:
            rendered = await self.renderer.calendar(snapshot)
            elapsed = time.monotonic() - started
            warning_seconds = max(1, int(self._value("cache_and_render", "slow_render_warning_seconds", 15)))
            if elapsed >= warning_seconds:
                logger.warning(f"方舟日历渲染耗时较长：{elapsed:.2f} 秒。")
            else:
                logger.info(f"方舟日历渲染完成，耗时 {elapsed:.2f} 秒。")
            if not self._cache_enabled():
                return rendered, "rendered"
            cached = self.render_cache.store(
                rendered,
                snapshot,
                display_config,
                self._cache_max_age(),
                self._cache_keep_count(),
            )
            logger.info(f"最终日历图片已保存至插件缓存：{cached}")
            return cached, "rendered"
        except Exception:
            fallback = self.render_cache.fallback() if self._cache_enabled() else None
            if fallback:
                image, manifest = fallback
                logger.warning(f"日历渲染失败，已回退到缓存图片（快照时间：{manifest.get('snapshot_generated_at', '未知')}）。")
                return image, "fallback"
            raise

    def _fallback_notice(self) -> str:
        cached = self.render_cache.fallback()
        time_text = cached[1].get("snapshot_generated_at", "未知") if cached else "未知"
        return f"{self.messages.text('cached_fallback_notice')}\n缓存数据时间：{time_text}"

    def _birthday_details(self, profession: str, rarity: int | None) -> str:
        details: list[str] = []
        if profession:
            details.append(f"职业：{profession}")
        if rarity:
            details.append(f"星级：{rarity}★")
        return f"\n{'　'.join(details)}" if details else ""

    def _format_status(self, snapshot) -> str:
        cache_status = self.render_cache.status(snapshot, self._display_config()) if self._cache_enabled() else {"state": "disabled"}
        lines = ["罗德岛行动日历状态", f"快照时间：{snapshot.generated_at}"]
        if self.service.last_refresh_error:
            lines.append(f"最近刷新：降级使用缓存（{self.service.last_refresh_error}）")
        else:
            lines.append("最近刷新：正常")
        for state in snapshot.source_states:
            status = "正常" if state.ok else "降级"
            detail = f" {state.message}" if state.message else ""
            lines.append(f"{state.name}：{status}{detail}")
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
        enabled = bool(self._value("scheduled_report", "enabled", False))
        targets = config_strings(self._value("scheduled_report", "target_sid_list", []))
        if not enabled:
            logger.info("定时方舟日报未启用。")
            return
        if not targets:
            logger.warning("定时方舟日报已启用，但未配置目标 SID，本次不创建任务。")
            return
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
            return
        self.scheduler = AsyncIOScheduler(timezone=CN_TZ)
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
        self.scheduler.start()
        logger.info(f"已启用定时方舟日报：每周 {day_of_week}，在 {times} 发送至 {len(targets)} 个会话。")

    async def _scheduled_report(self) -> None:
        if self._scheduled_report_lock.locked():
            logger.warning("定时方舟日报：已有任务正在执行，本次跳过。")
            return
        async with self._scheduled_report_lock:
            targets = config_strings(self._value("scheduled_report", "target_sid_list", []))
            refresh = bool(self._value("scheduled_report", "refresh_data_before_send", True))
            if not targets:
                logger.warning("定时方舟日报：目标 SID 为空，本次跳过。")
                return
            logger.info(f"定时方舟日报开始：目标 {len(targets)} 个会话，强制刷新={refresh}。")
            try:
                snapshot = await self.service.snapshot(force=refresh)
                image, image_state = await self._calendar_image(snapshot)
                caption = self.messages.text("scheduled_report_caption")
                sent, failed = await self._send_scheduled_image(targets, image, caption)
                logger.info(f"定时方舟日报完成：成功 {sent}/{len(targets)}，图片来源={image_state}。")
                if failed:
                    await self._notify_admin(
                        "【方舟日历异常告警】\n"
                        f"定时日报发送失败：{', '.join(failed)}\n"
                        f"成功发送：{sent}/{len(targets)}",
                        "scheduled_send_failed",
                    )
                await self._notify_refresh_status(snapshot, refresh, sent, len(targets), image_state)
                await self._observe_health(snapshot, "定时日报")
            except Exception:
                logger.error("定时方舟日报执行失败。", exc_info=True)
                await self._notify_admin(
                    "【方舟日历异常告警】\n定时日报未能完成，详情请查看 AstrBot 日志。",
                    "scheduled_report_failed",
                )

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

    async def _notify_refresh_status(self, snapshot, refreshed: bool, sent: int, total: int, image_state: str) -> None:
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
            f"{self._format_status(snapshot)}"
        )

    async def _observe_health(self, snapshot, origin: str) -> None:
        if not self._notifications_enabled():
            return
        current = self._abnormal_states(snapshot)
        stored = self.service.cache.load("notification_state.json")
        stored = stored if isinstance(stored, dict) else {}
        active = stored.get("active", {}) if isinstance(stored.get("active", {}), dict) else {}
        last_sent = stored.get("last_sent", {}) if isinstance(stored.get("last_sent", {}), dict) else {}
        now = datetime.now(CN_TZ)
        cooldown = max(1, int(self._value("admin_notification", "cooldown_minutes", 60)))
        to_alert: list[str] = []
        for name, message in current.items():
            sent_at = self._parse_time(str(last_sent.get(name, "")))
            expired = sent_at is None or (now - sent_at).total_seconds() >= cooldown * 60
            if active.get(name) != message or expired:
                to_alert.append(f"- {name}：{message}")
                last_sent[name] = now.isoformat()
        recovered = [name for name in active if name not in current]
        if to_alert:
            await self._send_admin_text(
                "【方舟日历异常告警】\n"
                f"触发来源：{origin}\n"
                f"时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
                + "\n".join(to_alert)
            )
        if recovered and bool(self._value("admin_notification", "notify_on_recovery", True)):
            await self._send_admin_text(
                "【方舟日历恢复通知】\n"
                f"触发来源：{origin}\n"
                f"已恢复：{', '.join(recovered)}"
            )
        self.service.cache.save("notification_state.json", {"active": current, "last_sent": last_sent})

    def _abnormal_states(self, snapshot) -> dict[str, str]:
        abnormal = {
            state.name: state.message or "数据源状态异常"
            for state in snapshot.source_states
            if not state.ok
        }
        if self.service.last_refresh_error:
            abnormal["方舟日历刷新"] = self.service.last_refresh_error
        return abnormal

    async def _notify_admin(self, text: str, event: str) -> None:
        if not self._notifications_enabled():
            return
        logger.warning(f"方舟日历管理员通知：{event}")
        await self._send_admin_text(text)

    async def _send_admin_text(self, text: str) -> None:
        for sid in self._admin_sids():
            try:
                await self.context.send_message(sid, MessageChain([Comp.Plain(text=text)]))
            except Exception:
                logger.error(f"向方舟日历管理员 SID {sid} 发送通知失败。", exc_info=True)

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
