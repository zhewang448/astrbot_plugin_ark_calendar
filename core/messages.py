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
        "data_degraded_notice": "部分数据暂时没有完成实时更新，请博士留意喵。{details}",
        "render_failed": "唔……日历终端这次没能完成绘制，已经记录问题并通知管理员。请博士稍后再试喵。",
        "birthday_missing_query": "请输入干员名称，例如：/方舟生日 卡缇",
        "birthday_found": "博士，干员「{name}」的生日是 {birthday}喵。{details}",
        "birthday_today_greeting": "今天正好是「{name}」的生日，祝你生日快乐喵～ 🎉",
        "scheduled_birthday_greeting": "博士，今天的祝福请送给「{names}」喵。生日快乐，愿好心情陪伴一整天～ 🎉",
        "birthday_unknown": "博士，干员「{name}」的生日暂未公开或数据源未记录喵。{details}",
        "birthday_not_found": "唔，没有找到「{name}」的生日记录。博士可以检查一下干员名称是否完整喵。",
        "birthday_candidates": "唔，找到了多个可能的干员：\n{candidates}\n请使用完整名称重新查询喵。",
        "birthday_lookup_failed": "唔……生日查询暂时没有完成，请博士稍后再试喵。",
        "status_failed": "唔……暂时没有读到方舟日历状态，请博士稍后再试喵。",
        "historical_range_invalid": "历史日程测试参数错误：{error}\n用法：/方舟历史日程测试 <日期>\n例如：/方舟历史日程测试 2026-07-01",
        "historical_render_failed": "历史日程测试图片生成失败，请查看 AstrBot 日志与数据源状态喵。",
        "subscription_added": "好的喵～已为博士订阅「{name}」，将在结束前一天的 {time} 提醒博士喵！",
        "subscription_removed": "收到喵～已为博士取消订阅「{name}」。",
        "subscription_not_found": "唔，博士好像还没有订阅「{name}」喵。",
        "subscription_list_empty": "博士目前还没有订阅任何活动或卡池喵。",
        "subscription_list_header": "博士的订阅列表喵～",
        "subscription_item_not_found": "唔，没有找到「{name}」，请使用 /方舟日历 查看当前活动和卡池喵。",
        "subscription_reminder": "{user}博士，「{name}」将在明天 {end_time} 结束，请抓紧时间参与喵～",
        "subscription_invalid_time": "唔，时间格式不对喵，请使用 HH:MM 格式，例如 12:00 或 09:30。",
    },
    "plain": {
        "rendering_started": "正在生成方舟日历，请稍候……",
        "force_refresh_started": "正在强制刷新方舟日历数据并重新生成图片，请稍候……",
        "scheduled_report_caption": "今日罗德岛行动日历，请查收。",
        "cached_fallback_notice": "数据源刷新失败，本次已发送最近一次保存的日历图片。",
        "data_degraded_notice": "部分数据未完成实时更新。{details}",
        "render_failed": "方舟日历生成失败，已记录问题并通知管理员，请稍后重试。",
        "birthday_missing_query": "请输入干员名称，例如：/方舟生日 卡缇",
        "birthday_found": "干员「{name}」的生日是 {birthday}。{details}",
        "birthday_today_greeting": "今天是干员「{name}」的生日。祝生日快乐！🎉",
        "scheduled_birthday_greeting": "罗德岛今日生日播报：{names}。愿今天的每一份祝福都准时抵达，生日快乐！🎉",
        "birthday_unknown": "干员「{name}」的生日暂未公开或数据源未记录。{details}",
        "birthday_not_found": "未找到「{name}」的生日记录，请检查干员名称是否完整。",
        "birthday_candidates": "找到多个可能的干员：\n{candidates}\n请使用完整名称重新查询。",
        "birthday_lookup_failed": "生日查询暂时失败，请稍后再试。",
        "status_failed": "暂时没有读到方舟日历状态，请稍后再试。",
        "historical_range_invalid": "历史日程测试参数错误：{error}\n用法：/方舟历史日程测试 <日期>\n例如：/方舟历史日程测试 2026-07-01",
        "historical_render_failed": "历史日程测试图片生成失败，请查看 AstrBot 日志与数据源状态。",
        "subscription_added": "已订阅「{name}」，将在结束前一天的 {time} 提醒。",
        "subscription_removed": "已取消订阅「{name}」。",
        "subscription_not_found": "未找到「{name}」的订阅记录。",
        "subscription_list_empty": "当前没有订阅任何活动或卡池。",
        "subscription_list_header": "订阅列表",
        "subscription_item_not_found": "未找到「{name}」，请使用 /方舟日历 查看当前活动和卡池。",
        "subscription_reminder": "{user}提醒：「{name}」将在明天 {end_time} 结束，请抓紧时间参与。",
        "subscription_invalid_time": "时间格式错误，请使用 HH:MM 格式，例如 12:00 或 09:30。",
    },
}


# 自定义文案里填这些哨兵值时，表示"这一句改用某个内置风格"，而不是把它当字面文案。
# 只在整串完全等于哨兵值（忽略大小写与首尾空白）时生效，因此以 @ 开头的正常文案不受影响。
PROFILE_SENTINELS: dict[str, str] = {
    "@catgirl": "rhodes_catgirl",
    "@plain": "plain",
}


class MessageCatalog:
    def __init__(self, config: Any, logger: Any | None = None):
        section = config_section(config, "messages")
        profile = str(section.get("profile", "rhodes_catgirl") or "rhodes_catgirl")
        self.profile = profile if profile in {*PROFILES, "custom"} else "rhodes_catgirl"
        raw_custom = section.get("custom_messages", {})
        self.custom_messages = raw_custom if isinstance(raw_custom, dict) else {}
        self.logger = logger

    def text(self, key: str, **values: Any) -> str:
        default_profile = "plain" if self.profile == "plain" else "rhodes_catgirl"
        default_template = PROFILES[default_profile].get(key, "")
        template = default_template
        custom = str(self.custom_messages.get(key, "") or "").strip()
        if self.profile == "custom" and custom:
            sentinel_profile = PROFILE_SENTINELS.get(custom.lower())
            if sentinel_profile:
                # 逐句选内置风格：留空沿用默认风格，填哨兵值改用指定风格。
                template = PROFILES[sentinel_profile].get(key, default_template)
            else:
                template = custom
        try:
            return template.format_map(defaultdict(str, values))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            if self.logger:
                self.logger.warning(f"自定义消息模板格式错误，已回退内置文案：{key}（{type(exc).__name__}）")
            try:
                return default_template.format_map(defaultdict(str, values))
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                return default_template
