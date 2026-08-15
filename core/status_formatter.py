"""日历状态、数据质量、生日详情等格式化函数。"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


def format_status(snapshot, outcome, cache_status: dict) -> str:
    """格式化日历状态文本。

    Args:
        snapshot: 数据快照
        outcome: 刷新结果
        cache_status: render_cache.status() 返回的状态字典
    """
    lines = ["罗德岛行动日历状态", f"快照时间：{snapshot.generated_at}"]
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


def data_quality_notice(quality: str, messages) -> str:
    """根据数据质量等级生成降级提示。

    Args:
        quality: "fresh" / "degraded" / "fallback" / "failed"
        messages: MessageCatalog 实例，用于渲染文案
    """
    if quality == "fresh":
        return ""
    details = {
        "degraded": "部分数据源使用了缓存或辅助信息暂时缺失。",
        "fallback": "关键数据源不可用，当前使用最近一次完整快照。",
        "failed": "关键数据源不可用，当前内容可能不完整。",
    }.get(quality, "部分数据可能不是最新。")
    return messages.text("data_degraded_notice", details=details)


def birthday_details(profession: str, rarity: int | None) -> str:
    """格式化干员生日查询中的职业/星级附加信息。"""
    details: list[str] = []
    if profession:
        details.append(f"职业：{profession}")
    if rarity:
        details.append(f"星级：{rarity}★")
    return f"\n{'　'.join(details)}" if details else ""


def parse_historical_day(value: str) -> date:
    """解析单个历史日期，兼容 ISO、斜线、点号、紧凑数字和中文格式。

    Raises:
        ValueError: 格式不受支持或日期在未来
    """
    text = re.sub(r"\s+", "", value or "").strip()
    if not text:
        raise ValueError("需要一个具体日期")
    formats = (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
        "%Y年%m月%d日", "%Y年%m月%d号",
    )
    for fmt in formats:
        try:
            target_day = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(
            "日期格式不受支持，可用 YYYY-MM-DD、YYYY/MM/DD、YYYY.MM.DD、YYYYMMDD 或 YYYY年MM月DD日"
        )
    today = datetime.now(CN_TZ).date()
    if target_day > today:
        raise ValueError("只能测试今天及以前的日期")
    return target_day
