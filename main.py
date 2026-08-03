from __future__ import annotations

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .core.renderer import CalendarRenderer
from .core.service import CalendarService


class ArkCalendarPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).resolve().parent
        self.data_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_ark_calendar"
        self.service = CalendarService(self.plugin_dir, self.data_dir, config, logger)
        self.renderer = CalendarRenderer(self, self.service)

    async def initialize(self) -> None:
        await self.service.initialize()
        logger.info("罗德岛行动日历插件已初始化。")

    @filter.command("方舟日历")
    async def calendar_command(self, event: AstrMessageEvent):
        """生成明日方舟活动、寻访、生日和今日信息长图。"""
        try:
            snapshot = await self.service.snapshot()
            image = await self.renderer.calendar(snapshot)
            yield event.image_result(image)
        except Exception as exc:
            logger.error("生成方舟日历失败。", exc_info=True)
            yield event.plain_result(f"方舟日历生成失败：{exc}")

    @filter.command("方舟生日")
    async def birthday_command(self, event: AstrMessageEvent, operator_name: str = ""):
        """查询指定干员的生日，例如：/方舟生日 卡缇。"""
        if not operator_name.strip():
            yield event.plain_result("请输入干员名称，例如：/方舟生日 卡缇")
            return
        try:
            operator, candidates = await self.service.find_operator(operator_name)
            if not operator:
                if candidates:
                    yield event.plain_result("找到多个可能的干员：\n" + "\n".join(f"- {name}" for name in candidates) + "\n请使用完整名称重新查询。")
                else:
                    yield event.plain_result(f"未找到干员“{operator_name}”。")
                return
            image = await self.renderer.birthday(operator)
            yield event.image_result(image)
        except Exception as exc:
            logger.error("查询干员生日失败。", exc_info=True)
            yield event.plain_result(f"生日查询失败：{exc}")

    @filter.command("方舟日历状态")
    async def status_command(self, event: AstrMessageEvent):
        """查看日历数据源和缓存状态。"""
        try:
            snapshot = await self.service.snapshot()
            lines = [f"方舟日历缓存时间：{snapshot.generated_at}"]
            lines.extend(
                f"{state.name}：{'正常' if state.ok else '降级'} {state.message}".rstrip()
                for state in snapshot.source_states
            )
            yield event.plain_result("\n".join(lines))
        except Exception as exc:
            yield event.plain_result(f"无法读取日历状态：{exc}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("方舟日历刷新")
    async def refresh_command(self, event: AstrMessageEvent):
        """管理员强制刷新数据并重新生成日历。"""
        try:
            snapshot = await self.service.snapshot(force=True)
            image = await self.renderer.calendar(snapshot)
            yield event.image_result(image)
        except Exception as exc:
            logger.error("强制刷新方舟日历失败。", exc_info=True)
            yield event.plain_result(f"刷新失败：{exc}")

    async def terminate(self) -> None:
        await self.service.close()
