from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import config_section


PROFILES: dict[str, dict[str, str]] = {
    "rhodes_catgirl": {
        "rendering_started": "收到，正在为博士整理行动日程并绘制日历，稍等一下喵～",
        "force_refresh_started": "收到，正在重新核对活动、寻访和作战信息，新的行动日历很快送达喵～",
        "scheduled_report_caption": "博士，今日罗德岛行动日历送达，请查收喵～",
        "cached_fallback_notice": "数据源暂时有些忙，本次先为博士送上最近一次保存的行动日历喵。",
        "render_failed": "唔……日历终端这次没能完成绘制，已经记录问题并通知管理员。请博士稍后再试喵。",
        "birthday_missing_query": "请输入干员名称，例如：/方舟生日 卡缇",
        "birthday_found": "博士，干员「{name}」的生日是 {birthday}喵。{details}",
        "birthday_unknown": "博士，干员「{name}」的生日暂未公开或数据源未记录喵。{details}",
        "birthday_not_found": "唔，没有找到「{name}」的生日记录。博士可以检查一下干员名称是否完整喵。",
        "birthday_candidates": "唔，找到了多个可能的干员：\n{candidates}\n请使用完整名称重新查询喵。",
    },
    "plain": {
        "rendering_started": "正在生成方舟日历，请稍候……",
        "force_refresh_started": "正在强制刷新方舟日历数据并重新生成图片，请稍候……",
        "scheduled_report_caption": "今日罗德岛行动日历，请查收。",
        "cached_fallback_notice": "数据源刷新失败，本次已发送最近一次保存的日历图片。",
        "render_failed": "方舟日历生成失败，已记录问题并通知管理员，请稍后重试。",
        "birthday_missing_query": "请输入干员名称，例如：/方舟生日 卡缇",
        "birthday_found": "干员「{name}」的生日是 {birthday}。{details}",
        "birthday_unknown": "干员「{name}」的生日暂未公开或数据源未记录。{details}",
        "birthday_not_found": "未找到「{name}」的生日记录，请检查干员名称是否完整。",
        "birthday_candidates": "找到多个可能的干员：\n{candidates}\n请使用完整名称重新查询。",
    },
}


class MessageCatalog:
    def __init__(self, config: Any):
        section = config_section(config, "messages")
        profile = str(section.get("profile", "rhodes_catgirl") or "rhodes_catgirl")
        self.profile = profile if profile in {*PROFILES, "custom"} else "rhodes_catgirl"
        raw_custom = section.get("custom_messages", {})
        self.custom_messages = raw_custom if isinstance(raw_custom, dict) else {}

    def text(self, key: str, **values: Any) -> str:
        default_profile = "plain" if self.profile == "plain" else "rhodes_catgirl"
        template = PROFILES[default_profile].get(key, "")
        custom = str(self.custom_messages.get(key, "") or "").strip()
        if self.profile == "custom" and custom:
            template = custom
        try:
            return template.format_map(defaultdict(str, values))
        except (KeyError, ValueError):
            return template
